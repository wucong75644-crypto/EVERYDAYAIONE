"""Generation preparation helper capability migration contract."""

from pathlib import Path


MIGRATIONS = Path(__file__).parent.parent / "migrations"
MIGRATION = MIGRATIONS / "204_generation_prepare_helper_capabilities.sql"
ROLLBACK = (
    MIGRATIONS
    / "rollback"
    / "204_generation_prepare_helper_capabilities_rollback.sql"
)

MESSAGE_HELPER = """_prepare_generation_messages(
    TEXT, UUID, UUID, UUID, JSONB, JSONB
)"""
TASK_HELPER = """_prepare_generation_tasks(
    JSONB, UUID, UUID, UUID, UUID, UUID, UUID, BIGINT, UUID
)"""


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_runtime_receives_the_complete_prepare_generation_helper_chain() -> None:
    sql = _read(MIGRATION)

    for signature in (MESSAGE_HELPER, TASK_HELPER):
        assert (
            f"REVOKE ALL ON FUNCTION {signature} "
            "FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,\n"
            "    everydayai_worker, everydayai;"
        ) in sql
        assert f"GRANT EXECUTE ON FUNCTION {signature} TO everydayai_runtime;" in sql


def test_non_runtime_service_roles_do_not_receive_helper_execution() -> None:
    sql = _read(MIGRATION)

    for role in ("everydayai_wecom_runtime", "everydayai_worker", "everydayai"):
        assert f") TO {role};" not in sql


def test_rollback_removes_both_runtime_helper_grants() -> None:
    sql = _read(ROLLBACK)

    for signature in (MESSAGE_HELPER, TASK_HELPER):
        assert f"REVOKE ALL ON FUNCTION {signature} FROM everydayai_runtime;" in sql
    assert "GRANT " not in sql
