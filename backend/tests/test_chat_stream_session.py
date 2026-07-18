"""Chat 单轮 Provider 流读取测试。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.adapters.base import StreamChunk
from services.handlers.chat.stream_session import (
    StreamDelivery,
    StreamTotals,
    read_stream_turn,
)


class _Adapter:
    def __init__(self, chunks: list[StreamChunk]) -> None:
        self._chunks = chunks

    async def stream_chat(self, **_kwargs):
        for chunk in self._chunks:
            yield chunk


def _delivery() -> StreamDelivery:
    return StreamDelivery(
        task_id="task-1",
        conversation_id="conv-1",
        message_id="message-1",
        user_id="user-1",
        org_id="org-1",
    )


@pytest.mark.asyncio
async def test_read_stream_turn_accumulates_usage_and_tool_calls() -> None:
    tool_delta = SimpleNamespace(
        index=0,
        id="call-1",
        name="query",
        arguments_delta='{"id":1}',
    )
    adapter = _Adapter(
        [
            StreamChunk(thinking_content="分析"),
            StreamChunk(content="结论"),
            StreamChunk(
                tool_calls=[tool_delta],
                prompt_tokens=12,
                completion_tokens=3,
                credits_consumed=2.5,
                finish_reason="tool_calls",
            ),
        ]
    )
    websocket = MagicMock()
    websocket.is_cancelled.return_value = False
    websocket.send_to_task_or_user = AsyncMock()
    totals = StreamTotals()
    blocks: list[dict] = []

    result = await read_stream_turn(
        adapter=adapter,
        messages=[{"role": "user", "content": "查询"}],
        stream_kwargs={"tools": []},
        thinking_effort=None,
        thinking_mode=None,
        delivery=_delivery(),
        totals=totals,
        content_blocks=blocks,
        websocket=websocket,
        save_accumulated=AsyncMock(),
    )

    assert result.text == "结论"
    assert result.thinking == "分析"
    assert result.tool_calls[0]["name"] == "query"
    assert result.request_started_at > 0
    assert totals.usage == {
        "prompt_tokens": 12,
        "completion_tokens": 3,
        "api_credits": 2.5,
    }
    assert totals.last_finish_reason == "tool_calls"
    assert blocks[0]["type"] == "thinking"


@pytest.mark.asyncio
async def test_read_stream_turn_schedules_recovery_snapshot_every_20_chunks() -> None:
    adapter = _Adapter([StreamChunk(content="x") for _ in range(20)])
    websocket = MagicMock()
    websocket.is_cancelled.return_value = False
    websocket.send_to_task_or_user = AsyncMock()
    save_accumulated = AsyncMock()

    await read_stream_turn(
        adapter=adapter,
        messages=[],
        stream_kwargs={},
        thinking_effort=None,
        thinking_mode=None,
        delivery=_delivery(),
        totals=StreamTotals(),
        content_blocks=[],
        websocket=websocket,
        save_accumulated=save_accumulated,
    )
    await asyncio.sleep(0)

    save_accumulated.assert_awaited_once_with("task-1", "x" * 20)


@pytest.mark.asyncio
async def test_read_stream_turn_stops_on_cancel_with_org_context() -> None:
    adapter = _Adapter([StreamChunk(content="ignored")])
    websocket = MagicMock()
    websocket.is_cancelled.return_value = True
    websocket.send_to_task_or_user = AsyncMock()

    with patch(
        "services.cancel_metrics.record_cancel_latency"
    ) as record_cancel:
        result = await read_stream_turn(
            adapter=adapter,
            messages=[],
            stream_kwargs={},
            thinking_effort=None,
            thinking_mode=None,
            delivery=_delivery(),
            totals=StreamTotals(),
            content_blocks=[],
            websocket=websocket,
            save_accumulated=AsyncMock(),
        )

    assert result.cancelled is True
    assert result.text == ""
    record_cancel.assert_called_once_with(
        "task-1",
        "org-1",
        phase="stream",
        had_partial=False,
        tools_in_flight=0,
    )


@pytest.mark.asyncio
async def test_buffered_turn_does_not_stream_unverified_numbers() -> None:
    adapter = _Adapter(
        [
            StreamChunk(thinking_content="我猜可能是1457"),
            StreamChunk(content="重新计算是1457单"),
        ]
    )
    websocket = MagicMock()
    websocket.is_cancelled.return_value = False
    websocket.send_to_task_or_user = AsyncMock()
    totals = StreamTotals()

    result = await read_stream_turn(
        adapter=adapter,
        messages=[],
        stream_kwargs={"tools": []},
        thinking_effort=None,
        thinking_mode=None,
        delivery=_delivery(),
        totals=totals,
        content_blocks=[],
        websocket=websocket,
        save_accumulated=AsyncMock(),
        buffer_output=True,
    )

    assert result.text == "重新计算是1457单"
    assert result.thinking == "我猜可能是1457"
    assert totals.text == ""
    assert totals.thinking == ""
    websocket.send_to_task_or_user.assert_not_awaited()
