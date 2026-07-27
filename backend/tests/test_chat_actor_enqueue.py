"""Web Chat Actor enqueue 单元测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from schemas.message import TextPart
from services.handlers.base import TaskMetadata
from services.handlers.context_snapshot import ContextAnchor
from services.handlers.chat.actor_enqueue import enqueue_web_chat
from services.handlers.chat_handler import ChatHandler


class _Handler:
    def __init__(self):
        self.db = MagicMock()
        self.org_id = "org-1"


def _metadata(*, prepared: bool = False):
    metadata = TaskMetadata(
        client_task_id="client-1",
        input_message_id="input-1",
        turn_id="turn-1",
        execution_mode="serial",
    )
    if prepared:
        metadata.context_anchor = ContextAnchor(
            task_id="prepared-task", conversation_id="conv-1", turn_id="turn-1",
            input_message_id="input-1", base_revision=2,
            through_message_id=None, org_id="org-1",
        )
    return metadata


@pytest.mark.asyncio
async def test_chat_handler_start_only_wakes_prepared_task(monkeypatch):
    publish = AsyncMock()
    monkeypatch.setattr(
        "services.handlers.chat.actor_enqueue._publish_wakeup",
        publish,
    )
    db = MagicMock()
    handler = ChatHandler(db)
    handler.org_id = "org-1"

    result = await handler.start(
        message_id="message-1",
        conversation_id="conv-1",
        user_id="user-1",
        content=[TextPart(text="你好")],
        params={"model": "model-1"},
        metadata=_metadata(prepared=True),
    )

    assert result == "client-1"
    db.rpc.assert_not_called()
    publish.assert_awaited_once_with("conv-1", "org-1")


@pytest.mark.asyncio
async def test_prepared_task_only_wakes_actor_without_rpc(monkeypatch):
    publish = AsyncMock()
    monkeypatch.setattr(
        "services.handlers.chat.actor_enqueue._publish_wakeup", publish,
    )
    handler = _Handler()
    metadata = _metadata(prepared=True)

    await enqueue_web_chat(
        handler=handler, external_task_id="client-1", message_id="message-1",
        conversation_id="conv-1", user_id="user-1", model_id="model-1",
        content=[TextPart(text="你好")], params={}, metadata=metadata,
    )

    handler.db.rpc.assert_not_called()
    publish.assert_awaited_once_with("conv-1", "org-1")


@pytest.mark.asyncio
async def test_prepared_personal_task_uses_personal_wakeup_scope(monkeypatch):
    publish = AsyncMock()
    monkeypatch.setattr(
        "services.handlers.chat.actor_enqueue._publish_wakeup", publish,
    )
    handler = _Handler()
    handler.org_id = None
    metadata = _metadata(prepared=True)
    metadata.context_anchor = ContextAnchor(
        task_id="prepared-task", conversation_id="conv-1", turn_id="turn-1",
        input_message_id="input-1", base_revision=2,
        through_message_id=None, org_id=None,
    )

    await enqueue_web_chat(
        handler=handler, external_task_id="client-1", message_id="message-1",
        conversation_id="conv-1", user_id="user-1", model_id="model-1",
        content=[TextPart(text="你好")], params={}, metadata=metadata,
    )

    handler.db.rpc.assert_not_called()
    publish.assert_awaited_once_with("conv-1", None)


@pytest.mark.asyncio
async def test_enqueue_requires_turn_anchor():
    metadata = _metadata(prepared=True)
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


@pytest.mark.asyncio
async def test_enqueue_requires_prepared_context_anchor():
    with pytest.raises(
        RuntimeError, match="ACTOR_PREPARED_CONTEXT_ANCHOR_MISSING",
    ):
        await enqueue_web_chat(
            handler=_Handler(),
            external_task_id="task",
            message_id="message",
            conversation_id="conv",
            user_id="user",
            model_id="model",
            content=[TextPart(text="test")],
            params={},
            metadata=_metadata(),
        )
