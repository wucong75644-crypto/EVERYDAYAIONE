"""ChangeSet 迁移契约静态检查，避免漏列、漏回滚或意外触碰定时任务表。"""

from pathlib import Path


ROOT = Path(__file__).parents[1]
MIGRATION = ROOT / "migrations/248_change_sets.sql"
ROLLBACK = ROOT / "migrations/rollback/248_change_sets_rollback.sql"


def test_changeset_migration_has_required_tables_and_fields():
    sql = MIGRATION.read_text(encoding="utf-8")
    for table in ("change_sets", "change_checks", "change_events"):
        assert f"CREATE TABLE IF NOT EXISTS public.{table}" in sql
    for column in (
        "resource_type", "resource_id", "operation", "base_revision",
        "base_snapshot", "proposed_snapshot", "patch", "diff", "risk_level",
        "policy_snapshot", "status", "idempotency_key", "expires_at",
        "audit_subject", "plan_snapshot", "tool_policy_snapshot", "check_summary",
    ):
        assert column in sql
    assert "UNIQUE INDEX IF NOT EXISTS uq_change_sets_org_idempotency" in sql
    assert "ON CONFLICT (org_id, idempotency_key) DO NOTHING" in sql
    assert "FOR UPDATE" in sql
    assert "transition_change_set" in sql
    assert "scheduled_tasks" not in sql


def test_changeset_migration_has_rls_and_safe_rollback():
    sql = MIGRATION.read_text(encoding="utf-8")
    rollback = ROLLBACK.read_text(encoding="utf-8")
    for table in ("change_sets", "change_checks", "change_events"):
        assert f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY" in sql
        assert f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY" in sql
    assert "cannot roll back 248 while ChangeSet data exists" in rollback
    assert "DROP TABLE IF EXISTS public.change_events" in rollback
