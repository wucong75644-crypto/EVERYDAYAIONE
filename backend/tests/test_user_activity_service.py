"""用户活跃事件服务测试。"""

from unittest.mock import MagicMock

from psycopg.types.json import Jsonb

from services.user_activity_service import record_user_activity


def test_record_user_activity_calls_rpc_with_expected_payload():
    db = MagicMock()
    rpc_chain = MagicMock()
    db.rpc.return_value = rpc_chain

    record_user_activity(
        db,
        user_id="user-1",
        event_type="message_sent",
        org_id="org-1",
        source="web",
        resource_type="message",
        resource_id="msg-1",
        metadata={"conversation_id": "conv-1"},
    )

    db.rpc.assert_called_once()
    fn_name, params = db.rpc.call_args[0]
    assert fn_name == "record_user_activity"
    assert params["p_user_id"] == "user-1"
    assert params["p_event_type"] == "message_sent"
    assert params["p_org_id"] == "org-1"
    assert params["p_source"] == "web"
    assert params["p_resource_type"] == "message"
    assert params["p_resource_id"] == "msg-1"
    assert isinstance(params["p_metadata"], Jsonb)
    assert params["p_metadata"].obj == {"conversation_id": "conv-1"}
    rpc_chain.execute.assert_called_once()


def test_record_user_activity_wraps_default_metadata_as_jsonb():
    db = MagicMock()

    record_user_activity(
        db,
        user_id="user-1",
        event_type="login_success",
    )

    _, params = db.rpc.call_args[0]
    assert isinstance(params["p_metadata"], Jsonb)
    assert params["p_metadata"].obj == {}


def test_record_user_activity_skips_invalid_event_type():
    db = MagicMock()

    record_user_activity(
        db,
        user_id="user-1",
        event_type="mousemove",
    )

    db.rpc.assert_not_called()


def test_record_user_activity_failure_does_not_raise():
    db = MagicMock()
    db.rpc.side_effect = RuntimeError("db down")

    record_user_activity(
        db,
        user_id="user-1",
        event_type="login_success",
    )

    db.rpc.assert_called_once()
