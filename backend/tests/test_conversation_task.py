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


def test_cancel_paused_actor_task_uses_final_cancel_rpc():
    db = MagicMock()
    db.rpc.return_value.execute.return_value.data = {"outcome": "cancelled"}

    assert cancel_actor_task(
        db,
        {"id": "internal", "status": "paused"},
        "user",
        None,
    )
    db.rpc.assert_called_once_with(
        "cancel_paused_generation_turn",
        {
            "p_task_id": "internal",
            "p_user_id": "user",
            "p_org_id": None,
        },
    )


def test_control_target_uses_continuation_gate_and_slot_without_changing_source():
    from services.conversation_task import actor_control_target
    source={'id':'original','client_task_id':'old-gate','request_params':{'_task_slot_id':'old-slot'}}
    result={'task_id':'continuation','client_task_id':'current-gate','external_task_id':None,
        'assistant_message_id':'current-output','task_slot_id':'current-slot'}
    target=actor_control_target(source,result)
    assert target['id']=='continuation' and target['client_task_id']=='current-gate'
    assert target['request_params']['_task_slot_id']=='current-slot'
    assert source['id']=='original' and source['request_params']['_task_slot_id']=='old-slot'
