"""Chat 多轮流式工具循环协调器测试。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.handlers.chat.stream_loop import ChatStreamLoop
from services.handlers.chat.stream_session import StreamDelivery


class _Budget:
    def __init__(self, turns_used: int = 0) -> None:
        self.turns_used = turns_used
        self.stop_reason = None

    def use_turn(self) -> None:
        self.turns_used += 1


def _delivery() -> StreamDelivery:
    return StreamDelivery(
        task_id="task-1",
        conversation_id="conv-1",
        message_id="message-1",
        user_id="user-1",
        org_id="org-1",
    )


def _prepared(turns_used: int = 0) -> SimpleNamespace:
    return SimpleNamespace(
        budget=_Budget(turns_used),
        core_tools=[],
        tool_context=SimpleNamespace(discovered_tools=set()),
        permission=MagicMock(need_exit_attachment=False),
        permission_mode="auto",
        stream_kwargs={},
        adapter=MagicMock(),
        messages=[],
    )


def _handler() -> MagicMock:
    handler = MagicMock()
    handler.org_id = "org-1"
    handler._pending_emit_payloads = []
    handler._pending_form_block = None
    handler._save_accumulated_content = AsyncMock()
    handler._save_accumulated_blocks = AsyncMock()
    handler._handle_user_cancel = AsyncMock()
    handler._execute_tool_calls = AsyncMock(return_value=[])
    handler._get_conv_source.return_value = "web"
    return handler


def _websocket(cancelled: bool = False) -> MagicMock:
    websocket = MagicMock()
    websocket.is_cancelled.return_value = cancelled
    websocket.send_to_task_or_user = AsyncMock()
    websocket.check_steer.return_value = None
    return websocket


@pytest.mark.asyncio
async def test_loop_stops_after_plain_text_turn() -> None:
    loop = ChatStreamLoop(
        handler=_handler(),
        prepared=_prepared(),
        delivery=_delivery(),
        websocket=_websocket(),
        thinking_effort=None,
        thinking_mode=None,
    )
    async def run_core(**kwargs):
        kwargs["totals"].text = "答案"
        kwargs["blocks"].append({"type": "text", "text": "答案"})

    with patch(
        "services.handlers.chat.stream_loop._run_loop",
        new_callable=AsyncMock,
        side_effect=run_core,
    ):
        await loop.run()

    assert loop.totals.text == "答案"
    assert loop.content_blocks == [{"type": "text", "text": "答案"}]


@pytest.mark.asyncio
async def test_loop_cancelled_before_provider_call_persists_anchor() -> None:
    handler = _handler()
    loop = ChatStreamLoop(
        handler=handler,
        prepared=_prepared(),
        delivery=_delivery(),
        websocket=_websocket(cancelled=True),
        thinking_effort=None,
        thinking_mode=None,
    )

    async def run_core(**kwargs):
        await kwargs["request"].on_cancel(
            [], [], "", "", "loop_top",
        )

    with patch(
        "services.handlers.chat.stream_loop._run_loop",
        new_callable=AsyncMock,
        side_effect=run_core,
    ):
        await loop.run()

    handler._handle_user_cancel.assert_awaited_once()
    assert handler._handle_user_cancel.call_args.args[5] == "loop_top"


@pytest.mark.asyncio
async def test_compatibility_loop_routes_to_shared_core() -> None:
    handler = _handler()
    loop = ChatStreamLoop(
        handler=handler,
        prepared=_prepared(),
        delivery=_delivery(),
        websocket=_websocket(),
        thinking_effort="high",
        thinking_mode="deep",
    )

    with patch(
        "services.handlers.chat.stream_loop._run_loop",
        new_callable=AsyncMock,
    ) as run_core:
        await loop.run()

    run_core.assert_awaited_once()
    request = run_core.call_args.kwargs["request"]
    assert request.thinking_effort == "high"
    assert request.thinking_mode == "deep"
    assert request.steer_reader is not None
    assert request.on_cancel is not None
