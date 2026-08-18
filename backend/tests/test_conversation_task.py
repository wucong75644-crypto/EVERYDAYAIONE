from unittest.mock import MagicMock

import pytest

from services.conversation_task import cancel_actor_task, is_actor_task


def test_is_actor_task_accepts_jsonb_and_serialized_json():
    assert is_actor_task({"delivery_context": {"actor": True}})
    assert is_actor_task({"delivery_context": '{"actor": true}'})
    assert not is_actor_task({"delivery_context": {"actor": False}})
    assert not is_actor_task({"delivery_context": "invalid"})


def test_cancel_actor_task_enqueues_durable_cancel_command():
    db = MagicMock()
    db.rpc.return_value.execute.return_value.data = {"outcome": "enqueued"}

    assert cancel_actor_task(
        db,
        {
            "id": "internal",
            "conversation_id": "conversation",
            "turn_id": "turn",
        },
        "user",
        "org",
    )
    name, params = db.rpc.call_args.args
    assert name == "append_conversation_control_command"
    assert params["p_conversation_id"] == "conversation"
    assert params["p_task_id"] == "internal"
    assert params["p_turn_id"] == "turn"
    assert params["p_event_type"] == "cancel"
    assert params["p_dedupe_key"] == "cancel:internal"
    assert params["p_payload"].obj == {
        "reason": "user_cancelled",
        "user_id": "user",
    }


def test_cancel_actor_task_rejects_unknown_result():
    db = MagicMock()
    db.rpc.return_value.execute.return_value.data = {"outcome": "invalid"}

    with pytest.raises(RuntimeError, match="ACTOR_CANCEL_FAILED"):
        cancel_actor_task(
            db,
            {"id": "internal", "conversation_id": "conversation"},
            "user",
            None,
        )
