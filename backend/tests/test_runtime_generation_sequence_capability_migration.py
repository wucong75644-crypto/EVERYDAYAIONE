"""Runtime generation queue sequence migration contract."""

from pathlib import Path


MIGRATIONS = Path(__file__).parent.parent / "migrations"
MIGRATION = MIGRATIONS / "205_runtime_generation_sequence_capability.sql"
ROLLBACK = (
    MIGRATIONS
    / "rollback"
    / "205_runtime_generation_sequence_capability_rollback.sql"
)


def test_only_runtime_receives_queue_sequence_usage() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert (
        "REVOKE ALL ON SEQUENCE task_queue_sequence_seq\n"
        "FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,\n"
        "    everydayai_worker, everydayai;"
    ) in sql
    assert (
        "GRANT USAGE ON SEQUENCE task_queue_sequence_seq "
        "TO everydayai_runtime;"
    ) in sql
    assert "GRANT SELECT" not in sql
    assert "GRANT UPDATE" not in sql


def test_rollback_removes_runtime_queue_sequence_usage() -> None:
    sql = ROLLBACK.read_text(encoding="utf-8")

    assert (
        "REVOKE USAGE ON SEQUENCE task_queue_sequence_seq "
        "FROM everydayai_runtime;"
    ) in sql
    assert "GRANT " not in sql
