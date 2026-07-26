"""Migration 207 Runtime media submission capability contract."""

from pathlib import Path


MIGRATIONS = Path(__file__).parent.parent / "migrations"
SQL = (
    MIGRATIONS / "207_runtime_media_submission_capabilities.sql"
).read_text()
ROLLBACK = (
    MIGRATIONS
    / "rollback"
    / "207_runtime_media_submission_capabilities_rollback.sql"
).read_text()


def test_submission_facades_validate_runtime_tenant_and_task_owner() -> None:
    assert SQL.count("SECURITY DEFINER") == 2
    assert SQL.count("session_user <> 'everydayai_runtime'") == 2
    assert SQL.count(
        "current_setting('app.access_kind', TRUE) <> 'runtime'"
    ) == 2
    assert SQL.count("tenant_actor_user_id() IS NULL") == 2
    assert SQL.count(
        "tenant_org_id() IS DISTINCT FROM p_org_id"
    ) == 2
    assert SQL.count(
        "v_task.user_id IS DISTINCT FROM public.tenant_actor_user_id()"
    ) == 2
    assert SQL.count(
        "public.tenant_user_fact_visible(v_task.org_id, v_task.user_id)"
    ) == 2


def test_only_runtime_receives_public_submission_capabilities() -> None:
    assert SQL.count("TO everydayai_runtime;") == 2
    assert SQL.count(
        "FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,"
    ) == 4
    assert "_attach_generation_external_task_owner" in SQL
    assert "_fail_prepared_generation_task_owner" in SQL


def test_rollback_restores_private_pre_207_functions() -> None:
    assert "DROP FUNCTION attach_generation_external_task(" in ROLLBACK
    assert "DROP FUNCTION fail_prepared_generation_task(" in ROLLBACK
    assert (
        "RENAME TO attach_generation_external_task" in ROLLBACK
    )
    assert "RENAME TO fail_prepared_generation_task" in ROLLBACK
    assert "GRANT EXECUTE" not in ROLLBACK
