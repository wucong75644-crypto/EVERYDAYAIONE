"""Static contract for migration 212 Agent Runtime core foundation."""

from pathlib import Path
import ast
import re


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = tuple(
    ROOT / f"migrations/{number}_{name}.sql"
    for number, name in (
        (212, "agent_runtime_core_foundation"),
        (213, "agent_runtime_session_run_rpcs"),
        (214, "agent_runtime_run_lifecycle_rpcs"),
        (215, "agent_runtime_model_event_projection_rpcs"),
    )
)
ROLLBACKS = tuple(
    ROOT / f"migrations/rollback/{path.stem}_rollback.sql"
    for path in reversed(MIGRATIONS)
)
SQL = "\n".join(path.read_text(encoding="utf-8") for path in MIGRATIONS)
ROLLBACK = "\n".join(path.read_text(encoding="utf-8") for path in ROLLBACKS)

TABLES = {
    "agent_runtime_sessions",
    "agent_session_commands",
    "agent_runs",
    "agent_run_attempts",
    "agent_model_steps",
    "agent_runtime_events",
    "agent_projection_outbox",
}
PUBLIC_RPCS = {
    "ensure_agent_runtime_session",
    "submit_session_command",
    "create_agent_run",
    "claim_agent_run",
    "renew_agent_run",
    "set_agent_run_waiting",
    "wake_agent_run",
    "complete_agent_run",
    "fail_agent_run",
    "cancel_agent_run",
    "create_model_step",
    "complete_model_step",
    "fail_model_step",
    "claim_agent_projection_outbox",
    "complete_agent_projection_outbox",
    "fail_agent_projection_outbox",
}
AR05_ENUMS = {
    "SessionCommandType": {
        "submit_input", "steer", "cancel", "approve",
        "reject", "switch_agent", "compact",
    },
    "ModelStepStatus": {
        "pending", "running", "completed", "failed", "cancelled",
    },
    "RunAttemptOutcome": {
        "completed", "lease_lost", "crashed", "cancelled", "failed",
    },
    "RuntimeActorType": {
        "user", "system", "model", "executor", "reconciler", "admin",
    },
}


def _domain_root() -> Path:
    candidates = (
        ROOT / "services/agent/runtime/domain",
        ROOT.parents[1] / "ar-05/backend/services/agent/runtime/domain",
    )
    return next(path for path in candidates if path.exists())


def _enum_values(path: Path, class_name: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    enum_class = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    return {
        node.value.value
        for node in enum_class.body
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    }


def test_creates_exact_foundation_tables_with_force_rls() -> None:
    created = set(re.findall(r"CREATE TABLE (agent_[a-z_]+)", SQL))
    assert created == TABLES
    for table in TABLES:
        assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;" in SQL
        assert f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;" in SQL
    assert all(path.stat().st_size > 0 for path in MIGRATIONS + ROLLBACKS)
    assert all(
        len(path.read_text(encoding="utf-8").splitlines()) <= 500
        for path in MIGRATIONS + ROLLBACKS
    )


def test_ar05_enums_and_event_envelope_match_database_contract() -> None:
    domain = _domain_root()
    sources = {
        "SessionCommandType": domain / "session.py",
        "ModelStepStatus": domain / "model_step.py",
        "RunAttemptOutcome": domain / "run.py",
        "RuntimeActorType": domain / "events.py",
    }
    for name, expected in AR05_ENUMS.items():
        assert _enum_values(sources[name], name) == expected
        for value in expected:
            assert f"'{value}'" in SQL
    for field in (
        "scope_kind", "scope_id", "event_type", "event_version",
        "durability", "correlation_id", "actor_type", "payload_hash",
        "occurred_at", "redaction_revision", "run_id", "model_step_id",
        "action_id", "actor_id", "causation_event_id", "payload",
        "trace_id", "span_id",
    ):
        assert re.search(rf"\b{field}\b", SQL)


def test_rpc_surface_is_complete_and_security_definer() -> None:
    created = set(re.findall(
        r"CREATE FUNCTION ([a-z_]+)\(", SQL,
    ))
    assert PUBLIC_RPCS <= created
    assert {
        "_assert_agent_runtime_actor",
        "_finish_agent_run",
        "append_agent_runtime_event",
    } <= created
    for name in PUBLIC_RPCS | {"append_agent_runtime_event"}:
        match = re.search(
            rf"CREATE FUNCTION {name}\(.*?\n\$\$;",
            SQL,
            flags=re.DOTALL,
        )
        assert match is not None
        assert "SECURITY DEFINER" in match.group(0)
        assert "SET search_path = pg_catalog, public" in match.group(0)


def test_internal_event_append_is_not_granted_to_login_roles() -> None:
    grant_statements = re.findall(
        r"GRANT EXECUTE ON FUNCTION(?P<body>.*?)TO [^;]+;",
        SQL,
        flags=re.DOTALL,
    )
    assert all("append_agent_runtime_event" not in body for body in grant_statements)
    assert "REVOKE ALL ON FUNCTION\n    _assert_agent_runtime_actor" in SQL
    assert "append_agent_runtime_event(" in SQL


def test_runtime_roles_receive_only_ingress_and_cancel_rpcs() -> None:
    ingress_grants = re.findall(
        r"GRANT EXECUTE ON FUNCTION(?P<body>.*?)"
        r"TO everydayai_runtime, everydayai_wecom_runtime;",
        SQL,
        flags=re.DOTALL,
    )
    body = "\n".join(ingress_grants)
    assert "ensure_agent_runtime_session" in body
    assert "submit_session_command" in body
    assert (
        "GRANT EXECUTE ON FUNCTION cancel_agent_run(UUID, BIGINT, TEXT)\n"
        "TO everydayai_runtime, everydayai_wecom_runtime, everydayai_worker;"
    ) in SQL
    assert "claim_agent_run" not in body


def test_worker_receives_run_model_step_and_projection_rpcs() -> None:
    worker_grants = re.findall(
        r"GRANT EXECUTE ON FUNCTION(?P<body>.*?)TO everydayai_worker;",
        SQL,
        flags=re.DOTALL,
    )
    body = "\n".join(worker_grants)
    for name in PUBLIC_RPCS - {
        "ensure_agent_runtime_session",
        "submit_session_command",
    }:
        assert name in body


def test_core_tables_have_no_runtime_direct_grants() -> None:
    revoke = re.search(
        r"REVOKE ALL ON TABLE(?P<body>.*?)FROM PUBLIC,"
        r" everydayai_runtime, everydayai_wecom_runtime,",
        flags=re.DOTALL,
        string=SQL,
    )
    assert revoke is not None
    for table in TABLES:
        assert table in revoke.group("body")
    assert not re.search(
        r"GRANT\s+(SELECT|INSERT|UPDATE|DELETE|ALL).*agent_",
        SQL,
        flags=re.IGNORECASE | re.DOTALL,
    )


def test_scope_parent_fencing_cas_and_atomic_event_contracts_exist() -> None:
    assert "FROM conversations" in SQL
    assert "FROM org_members" in SQL
    assert "tenant_actor_user_id()" in SQL
    assert "tenant_org_id()" in SQL
    assert "execution_token IS DISTINCT FROM p_execution_token" in SQL
    assert "state_version <> p_expected_state_version" in SQL
    assert "FOR UPDATE SKIP LOCKED" in SQL
    assert "next_event_sequence = next_event_sequence + 1" in SQL
    assert "UNIQUE (session_id, sequence)" in SQL
    assert "UNIQUE (session_id, idempotency_key)" in SQL
    assert "UNIQUE (command_id)" in SQL
    assert "request_hash TEXT NOT NULL" in SQL
    assert "v_idempotency_key" in SQL
    assert "idempotency_conflict" in SQL


def test_rollback_fails_closed_on_facts_and_drops_only_new_objects() -> None:
    assert "AGENT_RUNTIME_ROLLBACK_FACTS_PRESENT" in ROLLBACK
    assert "USING ERRCODE = '55000'" in ROLLBACK
    for table in TABLES:
        assert f"DROP TABLE IF EXISTS {table};" in ROLLBACK
    assert "ALTER TABLE conversations" not in ROLLBACK
    assert "DROP TABLE IF EXISTS messages" not in ROLLBACK
    assert "DROP TABLE IF EXISTS tasks" not in ROLLBACK
