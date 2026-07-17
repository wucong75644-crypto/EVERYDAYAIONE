"""迁移 133 的企微附件单次消费契约测试。"""

from pathlib import Path


MIGRATIONS = Path(__file__).parent.parent / "migrations"
SQL = (MIGRATIONS / "133_wecom_attachment_single_consumption.sql").read_text()
ROLLBACK = (
    MIGRATIONS
    / "rollback"
    / "133_wecom_attachment_single_consumption_rollback.sql"
).read_text()


def test_repairs_only_active_attachments_already_bound_to_tasks() -> None:
    repair = SQL[: SQL.index("CREATE OR REPLACE FUNCTION")]

    assert "SET reference_state = 'referenced'" in repair
    assert "WHERE a.reference_state = 'active'" in repair
    assert "FROM task_attachment_refs r" in repair
    assert "r.attachment_id = a.id" in repair


def test_binding_freezes_refs_before_consuming_active_attachment() -> None:
    insert_at = SQL.index("INSERT INTO task_attachment_refs")
    consume_at = SQL.index("SET reference_state = 'referenced'", insert_at)

    assert insert_at < consume_at
    assert "ON CONFLICT (task_id, attachment_id) DO NOTHING" in SQL
    assert "r.task_id = p_task_id" in SQL
    assert "r.input_message_id = p_input_message_id" in SQL
    assert "r.attachment_id = a.id" in SQL


def test_replay_cannot_consume_a_different_active_attachment() -> None:
    consume = SQL[SQL.index(
        "UPDATE conversation_attachment_refs a",
        SQL.index("CREATE OR REPLACE FUNCTION"),
    ):]

    assert "WHERE a.reference_state = 'active'" in consume
    assert "r.task_id = p_task_id" in consume
    assert "r.input_message_id = p_input_message_id" in consume


def test_rollback_restores_binding_without_reactivating_history() -> None:
    assert "CREATE OR REPLACE FUNCTION bind_task_attachments" in ROLLBACK
    assert "SET last_referenced_at = NOW()" in ROLLBACK
    assert "SET reference_state = 'active'" not in ROLLBACK
    assert "SET reference_state = 'referenced'" not in ROLLBACK
