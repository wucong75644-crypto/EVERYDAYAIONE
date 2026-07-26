"""Runtime generation facade migration contract."""

from pathlib import Path


MIGRATIONS = Path(__file__).parent.parent / "migrations"
MIGRATION = MIGRATIONS / "206_runtime_generation_capability_facade.sql"
ROLLBACK = (
    MIGRATIONS
    / "rollback"
    / "206_runtime_generation_capability_facade_rollback.sql"
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_public_facade_validates_runtime_actor_and_org_scope() -> None:
    sql = _read(MIGRATION)

    assert ") RENAME TO _prepare_generation_owner;" in sql
    assert "SECURITY DEFINER" in sql
    assert "session_user <> 'everydayai_runtime'" in sql
    assert "current_setting('app.access_kind', TRUE) <> 'runtime'" in sql
    assert "tenant_actor_user_id() IS DISTINCT FROM p_user_id" in sql
    assert "tenant_org_id() IS DISTINCT FROM p_org_id" in sql
    assert "NOT public.tenant_user_fact_visible(p_org_id, p_user_id)" in sql
    assert "RETURN public._prepare_generation_owner(" in sql


def test_only_public_facade_remains_executable_by_runtime() -> None:
    sql = _read(MIGRATION)

    for private_name in (
        "_prepare_generation_owner",
        "_prepare_generation_messages",
        "_prepare_generation_tasks",
    ):
        assert f"REVOKE ALL ON FUNCTION {private_name}(" in sql
    assert (
        "GRANT EXECUTE ON FUNCTION prepare_generation(\n"
        "    UUID, TEXT, UUID, UUID, UUID, UUID, JSONB, JSONB, JSONB\n"
        ") TO everydayai_runtime;"
    ) in sql
    assert "GRANT EXECUTE ON FUNCTION _prepare_generation_" not in sql
    assert "REVOKE ALL ON SEQUENCE task_queue_sequence_seq" in sql
    assert "GRANT USAGE ON SEQUENCE" not in sql


def test_rollback_restores_previous_invoker_capabilities() -> None:
    sql = _read(ROLLBACK)

    assert "DROP FUNCTION prepare_generation(" in sql
    assert ") RENAME TO prepare_generation;" in sql
    assert "GRANT EXECUTE ON FUNCTION _prepare_generation_messages(" in sql
    assert "GRANT EXECUTE ON FUNCTION _prepare_generation_tasks(" in sql
    assert (
        "GRANT USAGE ON SEQUENCE task_queue_sequence_seq "
        "TO everydayai_runtime;"
    ) in sql
