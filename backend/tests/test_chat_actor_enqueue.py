"""Web Chat Actor enqueue 单元测试。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from schemas.message import TextPart
from services.handlers.base import TaskMetadata
from services.handlers.chat.actor_enqueue import enqueue_web_chat
from services.handlers.chat_handler import ChatHandler


class _RPC:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return SimpleNamespace(data=self.value)


class _DB:
    def __init__(self):
        self.calls = []

    def rpc(self, name, params):
        self.calls.append((name, params))
        return _RPC({
            "task_id": params["p_task_data"].obj["id"],
            "already_enqueued": False,
        })


class _Handler:
    def __init__(self):
        self.db = _DB()
        self.org_id = "org-1"

    def _extract_text_content(self, content):
        return content[0].text

    def _serialize_params(self, params):
        return dict(params)

    def _build_task_data(self, **kwargs):
        return {
            "id": "random",
            "external_task_id": kwargs["task_id"],
            "client_task_id": kwargs["metadata"].client_task_id,
            "conversation_id": kwargs["conversation_id"],
            "user_id": kwargs["user_id"],
            "org_id": self.org_id,
            "type": kwargs["task_type"],
            "status": kwargs["status"],
            "model_id": kwargs["model_id"],
            "assistant_message_id": kwargs["message_id"],
            "placeholder_message_id": kwargs["message_id"],
            "request_params": kwargs["request_params"],
        }


def _metadata():
    return TaskMetadata(
        client_task_id="client-1",
        input_message_id="input-1",
        turn_id="turn-1",
        execution_mode="serial",
    )


@pytest.mark.asyncio
async def test_chat_handler_start_always_enqueues_actor(monkeypatch):
    enqueue = AsyncMock(return_value="client-1")
    monkeypatch.setattr(
        "services.handlers.chat.actor_enqueue.enqueue_web_chat",
        enqueue,
    )
    handler = ChatHandler(SimpleNamespace())

    result = await handler.start(
        message_id="message-1",
        conversation_id="conv-1",
        user_id="user-1",
        content=[TextPart(text="你好")],
        params={"model": "model-1"},
        metadata=_metadata(),
    )

    assert result == "client-1"
    enqueue.assert_awaited_once()
    assert enqueue.await_args.kwargs["external_task_id"] == "client-1"


@pytest.mark.asyncio
async def test_enqueue_uses_stable_internal_id_and_jsonb(monkeypatch):
    publish = AsyncMock()
    monkeypatch.setattr(
        "services.handlers.chat.actor_enqueue._publish_wakeup",
        publish,
    )
    first = _Handler()
    second = _Handler()

    result_one = await enqueue_web_chat(
        handler=first,
        external_task_id="client-1",
        message_id="message-1",
        conversation_id="conv-1",
        user_id="user-1",
        model_id="model-1",
        content=[TextPart(text="你好")],
        params={"permission_mode": "auto"},
        metadata=_metadata(),
    )
    await enqueue_web_chat(
        handler=second,
        external_task_id="client-1",
        message_id="message-1",
        conversation_id="conv-1",
        user_id="user-1",
        model_id="model-1",
        content=[TextPart(text="你好")],
        params={},
        metadata=_metadata(),
    )

    first_params = first.db.calls[0][1]
    second_params = second.db.calls[0][1]
    assert result_one == "client-1"
    assert first_params["p_task_data"].obj["id"] == (
        second_params["p_task_data"].obj["id"]
    )
    assert first_params["p_delivery_context"].obj == {
        "actor": True,
        "channel": "web",
    }
    assert publish.await_count == 2
    publish.assert_awaited_with("conv-1", "org-1")


@pytest.mark.asyncio
async def test_enqueue_requires_turn_anchor():
    metadata = _metadata()
    metadata.input_message_id = None

    with pytest.raises(RuntimeError, match="ACTOR_ENQUEUE_TURN_ANCHOR_MISSING"):
        await enqueue_web_chat(
            handler=_Handler(),
            external_task_id="task",
            message_id="message",
            conversation_id="conv",
            user_id="user",
            model_id="model",
            content=[TextPart(text="test")],
            params={},
            metadata=metadata,
        )
