"""迁移 131 的附件集合和任务绑定契约测试。"""

from pathlib import Path


SQL = (
    Path(__file__).parent.parent
    / "migrations/131_attachment_asset_lifecycle.sql"
).read_text()
ROLLBACK = (
    Path(__file__).parent.parent
    / "migrations/rollback/131_attachment_asset_lifecycle_rollback.sql"
).read_text()
TASK_SQL = (
    Path(__file__).parent.parent
    / "migrations/132_wecom_channel_task_enqueue.sql"
).read_text()


def test_asset_identity_columns_and_task_refs_exist() -> None:
    for column in (
        "provider_name",
        "canonical_name",
        "detected_mime_type",
        "detection_source",
        "content_sha256",
        "attachment_set_id",
        "last_referenced_at",
    ):
        assert f"ADD COLUMN IF NOT EXISTS {column}" in SQL
    assert "CREATE TABLE IF NOT EXISTS task_attachment_refs" in SQL
    assert "UNIQUE (task_id, attachment_id)" in SQL


def test_staging_reuses_collecting_set_and_replaces_referenced_set() -> None:
    assert "CREATE OR REPLACE FUNCTION stage_wecom_attachment_v2" in SQL
    assert "WECOM_ATTACHMENT_CONTENT_CONFLICT" in SQL
    assert "FROM task_attachment_refs r" in SQL
    assert "SET reference_state = 'replaced'" in SQL
    assert "v_set_id := COALESCE(v_set_id, gen_random_uuid())" in SQL


def test_enqueue_binds_without_consuming_active_set() -> None:
    assert "CREATE OR REPLACE FUNCTION bind_task_attachments" in SQL
    assert "INSERT INTO task_attachment_refs" in SQL
    assert "SET last_referenced_at = NOW()" in SQL
    enqueue = SQL[SQL.index(
        "CREATE OR REPLACE FUNCTION enqueue_wecom_generation_turn_v2"
    ):]
    assert "current_attachment_parts" in enqueue
    assert "bind_task_attachments" in enqueue
    assert "SET reference_state = 'referenced'" not in enqueue


def test_replay_keeps_frozen_input_content() -> None:
    assert "IF v_input.id IS NULL THEN" in SQL
    assert "v_content := v_input.content;" in SQL


def test_tenant_facade_and_group_binding_are_preserved() -> None:
    assert SQL.count(
        "CREATE OR REPLACE FUNCTION enqueue_wecom_generation_turn_v2"
    ) == 2
    assert "FROM conversation_channel_bindings b" in SQL
    assert "p_task_data->>'org_id'" in SQL
    assert "enqueue_wecom_task_record" in SQL
    assert "INSERT INTO tasks" in TASK_SQL
    assert "v_task.user_id IS DISTINCT FROM v_user_id" in TASK_SQL


def test_rollback_removes_only_v2_contract() -> None:
    assert "DROP TABLE IF EXISTS task_attachment_refs" in ROLLBACK
    assert "DROP FUNCTION IF EXISTS stage_wecom_attachment_v2" in ROLLBACK
    assert "DROP COLUMN IF EXISTS attachment_set_id" in ROLLBACK
    assert "enqueue_wecom_generation_turn(" not in ROLLBACK
