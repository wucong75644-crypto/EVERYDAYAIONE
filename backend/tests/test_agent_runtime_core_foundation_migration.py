"""Static contract for migration 212 Agent Runtime core foundation."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
SQL = (
    ROOT / "migrations/212_agent_runtime_core_foundation.sql"
).read_text(encoding="utf-8")
ROLLBACK = (
    ROOT / "migrations/rollback/212_agent_runtime_core_foundation_rollback.sql"
).read_text(encoding="utf-8")

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


def test_creates_exact_foundation_tables_with_force_rls() -> None:
    created = set(re.findall(r"CREATE TABLE (agent_[a-z_]+)", SQL))
    assert created == TABLES
    for table in TABLES:
        assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;" in SQL
        assert f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;" in SQL


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
    ingress_grant = re.search(
        r"GRANT EXECUTE ON FUNCTION\s+ensure_agent_runtime_session"
        r"(?P<body>.*?)TO everydayai_runtime, everydayai_wecom_runtime;",
        SQL,
        flags=re.DOTALL,
    )
    assert ingress_grant is not None
    assert "submit_session_command" in ingress_grant.group("body")
    assert "cancel_agent_run" in ingress_grant.group("body")
    assert "claim_agent_run" not in ingress_grant.group("body")


def test_worker_receives_run_model_step_and_projection_rpcs() -> None:
    worker_grant = re.search(
        r"GRANT EXECUTE ON FUNCTION\s+create_agent_run"
        r"(?P<body>.*?)TO everydayai_worker;",
        SQL,
        flags=re.DOTALL,
    )
    assert worker_grant is not None
    body = "create_agent_run" + worker_grant.group("body")
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


def test_rollback_fails_closed_on_facts_and_drops_only_new_objects() -> None:
    assert "AGENT_RUNTIME_ROLLBACK_FACTS_PRESENT" in ROLLBACK
    assert "USING ERRCODE = '55000'" in ROLLBACK
    for table in TABLES:
        assert f"DROP TABLE IF EXISTS {table};" in ROLLBACK
    assert "ALTER TABLE conversations" not in ROLLBACK
    assert "DROP TABLE IF EXISTS messages" not in ROLLBACK
    assert "DROP TABLE IF EXISTS tasks" not in ROLLBACK
