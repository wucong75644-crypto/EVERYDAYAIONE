"""Chat 多轮流式工具循环协调器测试。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.handlers.chat.stream_loop import ChatStreamLoop
from services.handlers.chat.stream_session import (
    StreamDelivery,
    StreamTurnResult,
)


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
        stream_kwargs={},
        adapter=MagicMock(),
        messages=[],
    )


def _turn(
    *,
    text: str = "",
    tool_calls: dict | None = None,
) -> StreamTurnResult:
    return StreamTurnResult(
        text=text,
        thinking="",
        thinking_committed=True,
        thinking_started_at=None,
        request_started_at=1,
        tool_calls=tool_calls or {},
        cancelled=False,
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
    with (
        patch(
            "services.handlers.chat.stream_loop.prepare_tool_turn",
            return_value=[],
        ),
        patch(
            "services.handlers.chat.stream_loop.read_stream_turn",
            new_callable=AsyncMock,
            return_value=_turn(text="答案"),
        ),
    ):
        await loop.run()

    assert loop.turn_result.text == "答案"
    assert loop.prepared.budget.turns_used == 1


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

    await loop.run()

    handler._handle_user_cancel.assert_awaited_once()
    assert handler._handle_user_cancel.call_args.args[5] == "loop_top"


@pytest.mark.asyncio
async def test_loop_executes_tools_then_continues_to_final_text() -> None:
    handler = _handler()
    websocket = _websocket()
    loop = ChatStreamLoop(
        handler=handler,
        prepared=_prepared(),
        delivery=_delivery(),
        websocket=websocket,
        thinking_effort=None,
        thinking_mode=None,
    )
    call = {"id": "call-1", "name": "query", "arguments": "{}"}
    with (
        patch(
            "services.handlers.chat.stream_loop.prepare_tool_turn",
            return_value=[],
        ),
        patch(
            "services.handlers.chat.stream_loop.read_stream_turn",
            new_callable=AsyncMock,
            side_effect=[_turn(tool_calls={0: call}), _turn(text="完成")],
        ),
        patch(
            "services.handlers.chat.stream_loop.begin_tool_calls",
            new_callable=AsyncMock,
            return_value={"call-1": 1.0},
        ),
        patch(
            "services.handlers.chat.stream_loop.apply_tool_results",
            return_value=[],
        ),
        patch(
            "services.handlers.chat.stream_loop.compact_tool_context",
            new_callable=AsyncMock,
        ) as compact,
    ):
        await loop.run()

    handler._execute_tool_calls.assert_awaited_once()
    compact.assert_awaited_once()
    assert loop.turn_result.text == "完成"
    assert loop.prepared.budget.turns_used == 2


@pytest.mark.asyncio
async def test_loop_does_not_recheck_final_text_against_evidence() -> None:
    from services.agent.agent_result import AgentResult
    from services.agent.runtime.artifact_collector import collect_tool_result
    from services.agent.runtime.runtime_state import RuntimeState

    state = RuntimeState.observing()
    state.ledger.record(
        collect_tool_result(
            AgentResult(
                summary="结构化数据",
                data=[{"有效订单": 1053}],
                source="erp_agent",
            ),
            tool_call_id="erp-1",
        )[0]
    )
    prepared = _prepared()
    prepared.runtime_state = state
    loop = ChatStreamLoop(
        handler=_handler(),
        prepared=prepared,
        delivery=_delivery(),
        websocket=_websocket(),
        thinking_effort=None,
        thinking_mode=None,
    )
    with (
        patch(
            "services.handlers.chat.stream_loop.prepare_tool_turn",
            return_value=[],
        ),
        patch(
            "services.handlers.chat.stream_loop.read_stream_turn",
            new_callable=AsyncMock,
            return_value=_turn(text="模型最终回答1457单"),
        ) as read_turn,
    ):
        await loop.run()

    assert loop.turn_result.text == "模型最终回答1457单"
    assert loop.prepared.budget.turns_used == 1
    read_turn.assert_awaited_once()


@pytest.mark.asyncio
async def test_empty_output_retries_once_then_uses_tool_fallback() -> None:
    loop = ChatStreamLoop(
        handler=_handler(),
        prepared=_prepared(turns_used=1),
        delivery=_delivery(),
        websocket=_websocket(),
        thinking_effort=None,
        thinking_mode="enabled",
    )
    loop.content_blocks = [
        {"type": "tool_step", "output": "工具原始结果"}
    ]
    with (
        patch(
            "services.handlers.chat.stream_loop.prepare_tool_turn",
            return_value=[],
        ),
        patch(
            "services.handlers.chat.stream_loop.read_stream_turn",
            new_callable=AsyncMock,
            side_effect=[_turn(), _turn()],
        ),
    ):
        await loop.run()

    assert loop.empty_output_retried is True
    assert loop.thinking_mode is None
    assert "工具原始结果" in loop.turn_result.text
    assert loop.totals.text == loop.turn_result.text
