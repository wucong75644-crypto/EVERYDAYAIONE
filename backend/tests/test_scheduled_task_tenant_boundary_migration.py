"""定时任务控制面租户边界迁移合同。"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = (
    ROOT / "backend/migrations/180_scheduled_task_tenant_boundary.sql"
).read_text(encoding="utf-8")
ROLLBACK = (
    ROOT
    / "backend/migrations/rollback"
    / "180_scheduled_task_tenant_boundary_rollback.sql"
).read_text(encoding="utf-8")


def test_migration_requires_owner_and_forces_rls() -> None:
    assert "SCHEDULED_CONTROL_OWNER_INVALID" in MIGRATION
    for table in ("scheduled_tasks", "scheduled_task_runs"):
        assert (
            f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY"
            in MIGRATION
        )
        assert (
            f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY"
            in MIGRATION
        )


def test_runtime_policy_is_org_scoped_and_worker_has_no_table_acl() -> None:
    assert "org_id = tenant_org_id()" in MIGRATION
    assert "tenant_actor_is_active_member(org_id)" in MIGRATION
    assert "task.id = scheduled_task_runs.task_id" in MIGRATION
    assert "task.org_id = scheduled_task_runs.org_id" in MIGRATION
    assert "GRANT SELECT, INSERT, UPDATE, DELETE" in MIGRATION
    assert "ON TABLE public.scheduled_tasks\nTO everydayai_runtime" in MIGRATION
    assert "ON TABLE public.scheduled_task_runs\nTO everydayai_runtime" in MIGRATION
    assert "TO everydayai_worker;" not in MIGRATION
    assert (
        "FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime, "
        "everydayai_worker"
    ) in MIGRATION


def test_rollback_removes_policies_force_rls_and_runtime_acl() -> None:
    for table in ("scheduled_tasks", "scheduled_task_runs"):
        assert f"DROP POLICY IF EXISTS tenant_{table}" in ROLLBACK
        assert (
            f"ALTER TABLE public.{table} NO FORCE ROW LEVEL SECURITY"
            in ROLLBACK
        )
        assert (
            f"ALTER TABLE public.{table} DISABLE ROW LEVEL SECURITY"
            in ROLLBACK
        )
    assert "FROM everydayai_runtime" in ROLLBACK
    assert "TO everydayai;" in ROLLBACK
