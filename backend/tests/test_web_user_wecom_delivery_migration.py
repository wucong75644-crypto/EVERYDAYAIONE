"""Web 用户消息企微事务 Outbox 迁移契约测试。"""

from pathlib import Path


MIGRATIONS = Path(__file__).parent.parent / "migrations"
MIGRATION = (
    MIGRATIONS / "134_web_user_wecom_delivery.sql"
).read_text(encoding="utf-8")
ROLLBACK = (
    MIGRATIONS / "rollback/134_web_user_wecom_delivery_rollback.sql"
).read_text(encoding="utf-8")


def test_migration_adds_delivery_kind_and_idempotency_scope():
    assert "ADD COLUMN IF NOT EXISTS delivery_kind TEXT" in MIGRATION
    assert "UNIQUE (task_id, channel, delivery_kind)" in MIGRATION
    assert "'assistant_terminal', 'web_user_message'" in MIGRATION
    assert "'delivery_kind', v_delivery.delivery_kind" in MIGRATION


def test_web_delivery_trigger_uses_verified_real_wecom_context():
    assert "AFTER INSERT ON tasks" in MIGRATION
    assert "c.source = 'wecom'" in MIGRATION
    assert "JOIN conversation_channel_bindings b" in MIGRATION
    assert "t.delivery_context->>'corp_id' = b.corp_id" in MIGRATION
    assert "t.delivery_context->>'chatid' = b.external_chat_id" in MIGRATION
    assert "t.delivery_context->>'chattype' = b.chat_type" in MIGRATION
    assert "ORDER BY t.created_at DESC, t.id DESC" in MIGRATION


def test_web_delivery_trigger_prevents_loops_and_stale_stream_updates():
    assert (
        """NEW.delivery_context @> '{"actor":true,"channel":"web"}'::JSONB"""
        in MIGRATION
    )
    for key in (
        "stream_task_id", "stream_req_id", "stream_id", "stream_started_at",
    ):
        assert f"- '{key}'" in MIGRATION
    assert "'web_user_message', v_target_context" in MIGRATION


def test_rollback_removes_mirror_rows_and_restores_original_contract():
    assert "DROP TRIGGER IF EXISTS tasks_web_user_wecom_delivery_trigger" in ROLLBACK
    assert "WHERE delivery_kind = 'web_user_message'" in ROLLBACK
    assert "DROP COLUMN IF EXISTS delivery_kind" in ROLLBACK
    assert "UNIQUE (task_id, channel)" in ROLLBACK
    assert "ON CONFLICT (task_id, channel) DO NOTHING" in ROLLBACK
