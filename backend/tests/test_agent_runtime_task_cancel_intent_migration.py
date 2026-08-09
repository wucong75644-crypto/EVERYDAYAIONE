"""Static contract for AR-18-A1.2-B1 task cancel intent lane."""

from pathlib import Path
import re

from scripts.migration_runner import discover_migrations


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = tuple(
    ROOT / "migrations" / name for name in (
        "227_22_01_agent_runtime_task_cancel_intent.sql",
        "227_22_02_agent_runtime_task_cancel_create_run_fence.sql",
        "227_22_03_agent_runtime_task_cancel_claim_fence.sql",
    )
)
ROLLBACKS = tuple(
    ROOT / "migrations/rollback" / f"{path.stem}_rollback.sql"
    for path in MIGRATIONS
)


def _body(sql: str, name: str) -> str:
    match = re.search(
        rf"CREATE(?: OR REPLACE)? FUNCTION {name}\b.*?AS \$\$(.*?)\$\$;",
        sql,
        re.DOTALL,
    )
    assert match, name
    return match.group(1)


def _normalized_body(path: Path, name: str) -> str:
    return re.sub(r"\s+", "", _body(path.read_text(encoding="utf-8"), name))


def test_lane_identity_order_and_ledger_rollback_mapping() -> None:
    discovered = discover_migrations(ROOT / "migrations")
    selected = [item for item in discovered if item.path in MIGRATIONS]
    assert [item.path for item in selected] == list(MIGRATIONS)
    assert [item.rollback_identity for item in selected] == [
        path.name for path in ROLLBACKS
    ]


def test_fact_table_is_durable_immutable_and_owner_only() -> None:
    sql = MIGRATIONS[0].read_text(encoding="utf-8")
    assert "CREATE TABLE agent_runtime_task_cancel_intents" in sql
    for contract in (
        "UNIQUE (task_id)", "UNIQUE (submit_command_id)",
        "UNIQUE (session_id, idempotency_key)",
        "request_hash ~ '^[0-9a-f]{64}$'",
        "status IN ('requested', 'applied')",
        "AGENT_RUNTIME_TASK_CANCEL_INTENT_IMMUTABLE",
        "ENABLE ROW LEVEL SECURITY", "FORCE ROW LEVEL SECURITY",
    ):
        assert contract in sql
    assert "REVOKE ALL ON TABLE agent_runtime_task_cancel_intents" in sql


def test_facade_lock_order_binding_hash_and_secret_free_response() -> None:
    sql = MIGRATIONS[0].read_text(encoding="utf-8")
    body = _body(sql, "request_agent_runtime_task_cancel_v1")
    locks = (
        "FROM agent_runtime_sessions", "FROM agent_session_commands",
        "FROM agent_command_claims", "_lock_agent_runtime_task_cancel_intent",
        "FROM agent_runs", "FROM tasks",
    )
    positions = [body.index(fragment) for fragment in locks]
    assert positions == sorted(positions)
    for binding in (
        "assistant_message_id", "runtime_session_id", "runtime_command_id",
        "'{\"actor\":false,\"runtime\":true}'", "output_message_id",
        "v_command.request_hash IS DISTINCT FROM md5", "tenant_org_id()",
        "tenant_actor_user_id()",
    ):
        assert binding in body
    assert "_agent_runtime_task_cancel_request_hash" in body
    assert "payload" not in "".join(
        re.findall(r"jsonb_build_object\((.*?)\)", body, re.DOTALL)[-3:]
    )


def test_all_production_root_run_entries_are_fenced() -> None:
    direct = MIGRATIONS[1].read_text(encoding="utf-8")
    claim = MIGRATIONS[2].read_text(encoding="utf-8")
    direct_body = _body(direct, "create_agent_run")
    assert direct_body.index("FROM agent_command_claims") < direct_body.index(
        "_lock_agent_runtime_task_cancel_intent"
    ) < direct_body.index("FROM agent_runs")
    eligibility = _body(claim, "_agent_command_run_eligibility")
    eligible = _body(claim, "_claim_eligible_agent_command")
    scanner = _body(claim, "claim_pending_agent_command_and_ensure_run")
    assert eligibility.index("_lock_agent_runtime_task_cancel_intent") < eligibility.index(
        "FROM agent_runs"
    )
    assert eligible.index("_lock_agent_runtime_task_cancel_intent") < eligible.index(
        "INSERT INTO agent_command_claims"
    )
    assert "LEFT JOIN agent_runtime_task_cancel_intents" in scanner
    assert "intent.id IS NULL" in scanner
    assert "CREATE OR REPLACE FUNCTION _ensure_agent_command_run" not in claim


def test_security_definer_search_path_and_acl_are_narrow() -> None:
    sql = "\n".join(path.read_text(encoding="utf-8") for path in MIGRATIONS)
    declarations = re.findall(
        r"CREATE(?: OR REPLACE)? FUNCTION .*?AS \$\$", sql, re.DOTALL,
    )
    assert len(declarations) == 9
    for declaration in declarations:
        assert "SECURITY DEFINER" in declaration or "SECURITY INVOKER" in declaration
        assert "search_path = pg_catalog, public" in declaration
    assert "TO everydayai_runtime, everydayai_wecom_runtime;" in sql
    assert "claim_pending_agent_command_and_ensure_run" in sql
    assert "TO everydayai_agent_runtime_worker;" in sql


def test_rollbacks_guard_facts_and_restore_exact_function_bodies() -> None:
    for rollback in ROLLBACKS:
        sql = rollback.read_text(encoding="utf-8")
        assert sql.index("AGENT_RUNTIME_TASK_CANCEL_ROLLBACK_FACTS_EXIST") < sql.find(
            "CREATE OR REPLACE FUNCTION"
        ) or "CREATE OR REPLACE FUNCTION" not in sql
    comparisons = (
        (ROLLBACKS[1], "create_agent_run", ROOT / "migrations/213_agent_runtime_session_run_rpcs.sql"),
        (ROLLBACKS[2], "_agent_command_run_eligibility", ROOT / "migrations/219_02a_agent_runtime_command_claim_terminal_compatibility.sql"),
        (ROLLBACKS[2], "_claim_eligible_agent_command", ROOT / "migrations/219_02a_agent_runtime_command_claim_terminal_compatibility.sql"),
        (ROLLBACKS[2], "claim_pending_agent_command_and_ensure_run", ROOT / "migrations/219_02a_agent_runtime_command_claim_terminal_compatibility.sql"),
    )
    for rollback, name, original in comparisons:
        assert _normalized_body(rollback, name) == _normalized_body(original, name)


def test_lane_does_not_enable_production_or_expand_scope() -> None:
    sql = "\n".join(path.read_text(encoding="utf-8").lower() for path in MIGRATIONS)
    for forbidden in (
        "production_ready", "ingress_enabled=true", "provider_cancel",
        "scheduler", "media", "child_run",
    ):
        assert forbidden not in sql
