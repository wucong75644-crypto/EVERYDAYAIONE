"""Chat 流结果与预算收尾测试。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from schemas.message import TextPart
from services.handlers.chat.stream_finalize import (
    StreamFinalizationInput,
    finalize_stream_result,
)
from services.handlers.chat.stream_session import StreamDelivery


def _delivery() -> StreamDelivery:
    return StreamDelivery(
        task_id="task-1",
        conversation_id="conv-1",
        message_id="message-1",
        user_id="user-1",
        org_id="org-1",
    )


def _state(text: str = "答案") -> StreamFinalizationInput:
    return StreamFinalizationInput(
        messages=[{"role": "user", "content": "问题"}],
        content_blocks=[],
        accumulated_text=text,
        accumulated_thinking="",
        turn_text=text,
        turn_thinking="",
        thinking_committed=True,
        thinking_started_at=None,
        usage={"prompt_tokens": 2, "completion_tokens": 1},
    )


@pytest.mark.asyncio
async def test_finalize_stream_result_builds_completion_without_committing() -> None:
    handler = MagicMock()
    handler._calculate_credits.return_value = 3
    handler.on_error = AsyncMock()
    websocket = MagicMock()
    websocket.send_to_task_or_user = AsyncMock()

    result = await finalize_stream_result(
        handler=handler,
        adapter=MagicMock(),
        budget=SimpleNamespace(stop_reason=None, turns_used=1),
        delivery=_delivery(),
        state=_state(),
        websocket=websocket,
        save_blocks=AsyncMock(),
    )

    assert result.completion_args == {
        "task_id": "task-1",
        "result": [TextPart(text="答案")],
        "credits_consumed": 3,
        "tool_digest": None,
    }
    handler.on_error.assert_not_awaited()
    assert result.clear_pending_emit_payloads is True


@pytest.mark.asyncio
async def test_budget_synthesis_replaces_fallback_text() -> None:
    handler = MagicMock()
    handler._calculate_credits.return_value = 1
    handler.on_error = AsyncMock()
    websocket = MagicMock()
    websocket.send_to_task_or_user = AsyncMock()
    save_blocks = AsyncMock()
    state = _state("部分结果")
    state.content_blocks = [{"type": "text", "text": "部分结果"}]

    with patch(
        "services.agent.stop_policy.synthesize_wrap_up",
        new_callable=AsyncMock,
        return_value="最终总结",
    ):
        result = await finalize_stream_result(
            handler=handler,
            adapter=MagicMock(),
            budget=SimpleNamespace(stop_reason="max_turns", turns_used=5),
            delivery=_delivery(),
            state=state,
            websocket=websocket,
            save_blocks=save_blocks,
        )
    await asyncio.sleep(0)

    assert result.accumulated_text == "最终总结"
    assert any(
        isinstance(part, TextPart) and part.text == "最终总结"
        for part in result.completion_args["result"]
    )
    save_blocks.assert_awaited_once()


@pytest.mark.asyncio
async def test_budget_without_any_output_records_error_and_skips_completion() -> None:
    handler = MagicMock()
    handler._calculate_credits.return_value = 0
    handler.on_error = AsyncMock()
    websocket = MagicMock()
    websocket.send_to_task_or_user = AsyncMock()

    with patch(
        "services.agent.stop_policy.synthesize_wrap_up",
        new_callable=AsyncMock,
        return_value="",
    ):
        result = await finalize_stream_result(
            handler=handler,
            adapter=MagicMock(),
            budget=SimpleNamespace(
                stop_reason="wall_timeout",
                turns_used=2,
            ),
            delivery=_delivery(),
            state=_state(""),
            websocket=websocket,
            save_blocks=AsyncMock(),
        )

    assert result.completion_args is None
    handler.on_error.assert_awaited_once_with(
        task_id="task-1",
        error_code="BUDGET_EXCEEDED",
        error_message="任务耗时过长，请稍后重试。",
    )


@pytest.mark.asyncio
async def test_grounded_blocked_budget_skips_model_wrap_up() -> None:
    from services.agent.runtime.grounded_final import GROUNDED_FINAL_BLOCKED

    handler = MagicMock()
    handler._calculate_credits.return_value = 0
    handler.on_error = AsyncMock()
    websocket = MagicMock()
    websocket.send_to_task_or_user = AsyncMock()
    state = _state(GROUNDED_FINAL_BLOCKED)
    state.grounded_blocked = True

    with patch(
        "services.agent.stop_policy.synthesize_wrap_up",
        new_callable=AsyncMock,
    ) as synthesize:
        result = await finalize_stream_result(
            handler=handler,
            adapter=MagicMock(),
            budget=SimpleNamespace(stop_reason="max_turns", turns_used=5),
            delivery=_delivery(),
            state=state,
            websocket=websocket,
            save_blocks=AsyncMock(),
        )

    synthesize.assert_not_awaited()
    assert result.completion_args["result"] == [
        TextPart(text=GROUNDED_FINAL_BLOCKED)
    ]
