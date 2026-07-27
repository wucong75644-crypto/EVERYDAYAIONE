from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / (
    "migrations/220_26_agent_runtime_projection_dead_recovery.sql"
)
ROLLBACK = ROOT / (
    "migrations/rollback/"
    "220_26_agent_runtime_projection_dead_recovery_rollback.sql"
)


def test_dead_recovery_is_audited_and_scope_closed() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "CREATE TABLE agent_projection_dead_recoveries" in sql
    assert "recovery_version" in sql
    assert "recovery_count" in sql
    assert "CREATE FUNCTION list_agent_projection_dead_items(" in sql
    assert "CREATE FUNCTION get_agent_projection_dead_item(" in sql
    assert "CREATE FUNCTION requeue_agent_projection_dead(" in sql
    assert "tenant_platform_admin()" in sql
    assert "ALTER TABLE agent_projection_dead_recoveries FORCE ROW LEVEL SECURITY" in sql
    assert "TO everydayai_runtime" in sql
    assert "FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime" in sql


def test_claim_ownership_and_lock_order_are_replaced_additively() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "_claim_agent_projection_outbox_215" in sql
    assert "_apply_agent_compat_projection_220_12" in sql
    assert "projection_kind = 'audit'" in sql
    session_lock = sql.index("FROM agent_runtime_sessions")
    outbox_lock = sql.index("FROM agent_projection_outbox", session_lock)
    assert session_lock < outbox_lock


def test_rollback_is_exact_and_guarded() -> None:
    sql = ROLLBACK.read_text(encoding="utf-8")

    assert "AGENT_PROJECTION_DEAD_RECOVERY_ROLLBACK_HAS_FACTS" in sql
    assert "RENAME TO claim_agent_projection_outbox" in sql
    assert "RENAME TO apply_agent_compat_projection" in sql
    assert "DROP TABLE agent_projection_dead_recoveries" in sql
