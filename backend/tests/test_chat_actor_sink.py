"""ActorWebSink 单元测试。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from services.handlers.chat.actor_sink import (
    ActorDelivery,
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


class _DeliveryStore:
    def __init__(self):
        self.events = []
        self.snapshots = []
        self._seq = 0

    async def begin(self, **_kwargs):
        from services.conversation_delivery_store import DeliverySession

        return DeliverySession(
            session_id="session-1",
            stream_id="stream-1",
            execution_attempt=2,
            next_seq=self._seq,
            snapshot_seq=0,
            snapshot_content="",
            snapshot_blocks=[],
        )

    async def append(self, *, event_type, payload, **_kwargs):
        self._seq += 1
        self.events.append((event_type, payload))
        return {"outcome": "appended", "delivery_seq": self._seq}

    async def save_snapshot(self, *, content, blocks, **_kwargs):
        self.snapshots.append((content, blocks))
        return {"outcome": "saved"}


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
async def test_sink_block_update_is_ordered_and_persisted():
    db = _DB([{"outcome": "updated"}] * 8)
    websocket = _WebSocket()
    delivery_store = _DeliveryStore()
    sink = ActorWebSink(
        db, _delivery(), asyncio.Event(), websocket,
        delivery_store=delivery_store,
    )

    running = {
        "type": "tool_step",
        "tool_name": "erp_agent",
        "tool_call_id": "call-1",
        "status": "running",
    }
    completed = {**running, "status": "completed", "output": "查询完成"}

    await sink.start()
    await sink.on_block(running)
    await sink.on_block_update(completed)

    assert [event[0] for event in delivery_store.events] == [
        "message_start", "content_block_add", "content_block_add",
    ]
    assert websocket.messages[-1][3]["payload"]["block"]["status"] == "completed"
    assert websocket.messages[-1][3]["payload"]["delivery_seq"] == 3
    assert delivery_store.snapshots[-1][1] == [completed]
