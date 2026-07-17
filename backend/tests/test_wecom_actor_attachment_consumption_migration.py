from pathlib import Path


SQL = (
    Path(__file__).parent.parent
    / "migrations/130_wecom_actor_attachment_consumption.sql"
).read_text()
ROLLBACK = (
    Path(__file__).parent.parent
    / "migrations/rollback/130_wecom_actor_attachment_consumption_rollback.sql"
).read_text()


def test_active_attachments_are_consumed_under_lock() -> None:
    assert "consume_active_conversation_attachments" in SQL
    assert "FOR UPDATE;" in SQL
    assert "SET reference_state = 'referenced'" in SQL
    assert "'workspace_path', a.workspace_path" in SQL


def test_replay_does_not_consume_another_attachment() -> None:
    select_existing = SQL.index(
        "SELECT * INTO v_input FROM messages"
    )
    consume = SQL.index(
        "consume_active_conversation_attachments(", select_existing
    )
    assert select_existing < consume
    assert "IF v_input.id IS NULL THEN" in SQL
    assert "v_content := v_input.content;" in SQL


def test_channel_scope_is_verified_against_binding() -> None:
    assert "v_conversation.scope_type = 'channel'" in SQL
    assert "FROM conversation_channel_bindings b" in SQL
    assert "b.corp_id = p_delivery_context->>'corp_id'" in SQL
    assert "b.external_chat_id = p_delivery_context->>'chatid'" in SQL


def test_sender_identity_is_preserved() -> None:
    assert "sender_user_id, sender_channel_identity" in SQL
    assert "p_delivery_context->>'wecom_userid'" in SQL


def test_rollback_restores_previous_enqueue_function() -> None:
    assert "CREATE OR REPLACE FUNCTION enqueue_wecom_generation_turn" in ROLLBACK
    assert "DROP FUNCTION IF EXISTS consume_active_conversation_attachments" in ROLLBACK
