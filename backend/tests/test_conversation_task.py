from unittest.mock import MagicMock

import pytest

from services.conversation_task import cancel_actor_task, is_actor_task


def test_is_actor_task_accepts_jsonb_and_serialized_json():
    assert is_actor_task({"delivery_context": {"actor": True}})
    assert is_actor_task({"delivery_context": '{"actor": true}'})
    assert not is_actor_task({"delivery_context": {"actor": False}})
    assert not is_actor_task({"delivery_context": "invalid"})


def test_cancel_actor_task_uses_scoped_fencing_rpc():
    db = MagicMock()
    db.rpc.return_value.execute.return_value.data = {"outcome": "cancelled"}

    assert cancel_actor_task(
        db,
        {"id": "internal"},
        "user",
        "org",
    )
    db.rpc.assert_called_once_with(
        "cancel_generation_turn",
        {
            "p_task_id": "internal",
            "p_user_id": "user",
            "p_org_id": "org",
        },
    )


def test_cancel_actor_task_rejects_unknown_result():
    db = MagicMock()
    db.rpc.return_value.execute.return_value.data = {"outcome": "invalid"}

    with pytest.raises(RuntimeError, match="ACTOR_CANCEL_FAILED"):
        cancel_actor_task(db, {"id": "internal"}, "user", None)
