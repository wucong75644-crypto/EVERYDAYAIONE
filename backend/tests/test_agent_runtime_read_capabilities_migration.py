"""Static security contract for Agent Runtime migration 216."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT / "migrations/216_agent_runtime_read_projection_capabilities.sql"
).read_text(encoding="utf-8")
ROLLBACK = (
    ROOT / "migrations/rollback/"
    "216_agent_runtime_read_projection_capabilities_rollback.sql"
).read_text(encoding="utf-8")
PUBLIC_RPCS = {
    "get_agent_runtime_session",
    "replay_agent_runtime_events",
    "get_agent_runtime_run_claim",
    "get_claimed_agent_projection_event",
}


def test_migration_adds_only_read_functions() -> None:
    assert "CREATE TABLE" not in MIGRATION
    assert "ALTER TABLE" not in MIGRATION
    assert not re.search(
        r"\b(INSERT|UPDATE|DELETE|TRUNCATE)\b", MIGRATION,
    )
    for name in PUBLIC_RPCS:
        body = re.search(
            rf"CREATE FUNCTION {name}\(.*?\n\$\$;",
            MIGRATION,
            flags=re.DOTALL,
        )
        assert body is not None
        assert "STABLE SECURITY DEFINER" in body.group(0)
        assert "SET search_path = pg_catalog, public" in body.group(0)


def test_roles_are_closed_and_tables_remain_ungranted() -> None:
    assert "FROM PUBLIC, everydayai_runtime" in MIGRATION
    assert "TO everydayai_runtime, everydayai_wecom_runtime, everydayai_worker" in MIGRATION
    assert "get_agent_runtime_run_claim(UUID, TEXT)," in MIGRATION
    assert "TO everydayai_worker;" in MIGRATION
    assert not re.search(
        r"GRANT\s+(SELECT|INSERT|UPDATE|DELETE|ALL)\s+ON\s+TABLE",
        MIGRATION,
        flags=re.IGNORECASE,
    )


def test_scope_sequence_lease_and_rollback_contracts() -> None:
    assert "tenant_actor_user_id()" in MIGRATION
    assert "tenant_org_id()" in MIGRATION
    assert "member.status = 'active'" in MIGRATION
    assert "AGENT_RUNTIME_EVENT_SEQUENCE_GAP" in MIGRATION
    assert "lease_token IS DISTINCT FROM p_lease_token" in MIGRATION
    assert "lease_expires_at <= clock_timestamp()" in MIGRATION
    for name in PUBLIC_RPCS | {"_assert_agent_runtime_session_read"}:
        assert f"{name}(" in ROLLBACK
    assert "GRANT" not in ROLLBACK
    assert "ALTER TABLE" not in ROLLBACK
