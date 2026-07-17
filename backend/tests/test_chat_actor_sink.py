"""ActorWebSink 单元测试。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from services.handlers.chat.actor_sink import (
    ActorDelivery,
    ActorPersistenceSink,
    ActorWebSink,
)


class _RPC:
    def __init__(self, result):
        self._result = result

    async def execute(self):
        return SimpleNamespace(data=self._result)


class _DB:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def rpc(self, name, params):
        self.calls.append((name, params))
        outcome = self.outcomes.pop(0) if self.outcomes else {"outcome": "updated"}
        return _RPC(outcome)


class _WebSocket:
    def __init__(self):
        self.messages = []

    async def send_to_task_or_user(self, task_id, user_id, message, org_id=None):
        self.messages.append((task_id, user_id, org_id, message))


class _FailingWebSocket:
    async def send_to_task_or_user(self, *_args, **_kwargs):
        raise ConnectionError("redis down")


def _delivery() -> ActorDelivery:
    return ActorDelivery(
        task_id="internal-1",
        push_task_id="client-1",
        execution_token="token-1",
        conversation_id="conv-1",
        message_id="message-1",
        user_id="user-1",
        org_id="org-1",
        model_id="model-1",
    )


@pytest.mark.asyncio
async def test_sink_streams_and_persists_with_execution_token():
    db = _DB([{"outcome": "updated"}, {"outcome": "updated"}])
    websocket = _WebSocket()
    sink = ActorWebSink(db, _delivery(), asyncio.Event(), websocket)

    await sink.start()
    await sink.on_text("你好")
    block = {"type": "text", "text": "你好"}
    await sink.on_block(block)
    await sink.flush()

    assert [item[3]["type"] for item in websocket.messages] == [
        "message_start",
        "message_chunk",
        "content_block_add",
        "stream_end",
    ]
    assert db.calls[-1][1]["p_execution_token"] == "token-1"
    assert db.calls[-1][1]["p_accumulated_content"] == "你好"
    assert db.calls[-1][1]["p_accumulated_blocks"].obj == [block]


@pytest.mark.asyncio
async def test_sink_cancels_execution_when_progress_fencing_is_lost():
    event = asyncio.Event()
    sink = ActorWebSink(
        _DB([{"outcome": "ownership_lost"}]),
        _delivery(),
        event,
        _WebSocket(),
    )

    with pytest.raises(asyncio.CancelledError):
        await sink.on_block({"type": "text", "text": "partial"})

    assert event.is_set()


@pytest.mark.asyncio
async def test_sink_degrades_when_progress_store_is_temporarily_unavailable():
    db = _DB([None])
    websocket = _WebSocket()
    sink = ActorWebSink(db, _delivery(), asyncio.Event(), websocket)

    await sink.on_block({"type": "text", "text": "partial"})

    assert websocket.messages[0][3]["type"] == "content_block_add"


@pytest.mark.asyncio
async def test_sink_delivery_failure_does_not_abort_generation():
    sink = ActorWebSink(
        _DB([{"outcome": "updated"}]),
        _delivery(),
        asyncio.Event(),
        _FailingWebSocket(),
    )

    await sink.start()
    await sink.on_text("继续生成")


@pytest.mark.asyncio
async def test_persistence_sink_saves_progress_without_web_delivery():
    db = _DB([{"outcome": "updated"}])
    sink = ActorPersistenceSink(db, _delivery(), asyncio.Event())

    await sink.start()
    await sink.on_text("企微结果")
    await sink.flush()

    assert db.calls[-1][1]["p_accumulated_content"] == "企微结果"
