from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from psycopg.types.json import Jsonb

from schemas.wecom import WecomIncomingMessage
from services.wecom.actor_enqueue import enqueue_wecom_message


def _message() -> WecomIncomingMessage:
    return WecomIncomingMessage(
        msgid="msg-1",
        wecom_userid="wx-user",
        corp_id="corp",
        chatid="chat",
        chattype="single",
        msgtype="text",
        channel="smart_robot",
        org_id="org",
        text_content="你好",
    )


def _handler() -> MagicMock:
    handler = MagicMock()
    conversation = handler.db.table.return_value.select.return_value.eq.return_value
    conversation.single.return_value.execute.return_value = SimpleNamespace(
        data={"model_id": "qwen3.5-plus", "chat_settings": {"thinking_mode": "deep"}},
    )
    handler._build_task_data.return_value = {
        "id": "random",
        "conversation_id": "conversation",
        "user_id": "user",
        "org_id": "org",
        "assistant_message_id": "output",
    }
    handler.db.rpc.return_value.execute.return_value = SimpleNamespace(
        data={"task_id": "internal", "already_enqueued": False},
    )
    return handler


@pytest.mark.asyncio
async def test_enqueue_is_stable_atomic_and_contains_no_secret():
    handler = _handler()
    wakeup = AsyncMock(return_value=True)

    with patch(
        "services.conversation_worker.RedisConversationWakeup.publish",
        new=wakeup,
    ):
        first = await enqueue_wecom_message(
            handler=handler,
            msg=_message(),
            user_id="user",
            conversation_id="conversation",
            image_urls=[],
        )
        first_params = handler.db.rpc.call_args.args[1]
        second = await enqueue_wecom_message(
            handler=handler,
            msg=_message(),
            user_id="user",
            conversation_id="conversation",
            image_urls=[],
        )
        second_params = handler.db.rpc.call_args.args[1]

    assert first.task_id == second.task_id == "internal"
    assert first_params["p_task_data"].obj["id"] == second_params["p_task_data"].obj["id"]
    assert first_params["p_input_message_id"] == second_params["p_input_message_id"]
    assert first_params["p_output_message_id"] == second_params["p_output_message_id"]
    assert isinstance(first_params["p_input_content"], Jsonb)
    delivery = first_params["p_delivery_context"].obj
    assert delivery["channel"] == "wecom"
    assert delivery["chatid"] == "chat"
    assert "agent_secret" not in delivery
    wakeup.assert_awaited()


@pytest.mark.asyncio
async def test_enqueue_requires_provider_message_id():
    handler = _handler()
    msg = _message()
    msg.msgid = ""

    with pytest.raises(RuntimeError, match="WECOM_ACTOR_MSGID_MISSING"):
        await enqueue_wecom_message(
            handler=handler,
            msg=msg,
            user_id="user",
            conversation_id="conversation",
            image_urls=[],
        )


@pytest.mark.asyncio
async def test_enqueue_file_uses_structured_filepart_without_scanned_text():
    handler = _handler()
    msg = _message()
    msg.msgtype = "file"
    msg.text_content = None
    file_payload = {
        "url": "https://cdn/report.csv",
        "workspace_path": "上传/企微/stable_report.csv",
        "name": "report.csv",
        "mime_type": "text/csv",
        "size": 10,
    }
    with patch(
        "services.conversation_worker.RedisConversationWakeup.publish",
        new=AsyncMock(return_value=True),
    ):
        await enqueue_wecom_message(
            handler=handler,
            msg=msg,
            user_id="user",
            conversation_id="conversation",
            image_urls=[],
            file_payload=file_payload,
        )

    content = handler.db.rpc.call_args.args[1]["p_input_content"].obj
    assert content == [{"type": "file", **file_payload}]
    assert all("[文件:" not in str(part) for part in content)
