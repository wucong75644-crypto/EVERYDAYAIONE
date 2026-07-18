"""Chat 流执行完成后的预算合成与结构化结果收割。"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from loguru import logger

from schemas.websocket import build_stream_end
from services.handlers.chat.outcome_builder import (
    append_final_turn_blocks,
    build_content_parts,
)
from services.handlers.chat.stream_session import StreamDelivery


@dataclass
class StreamFinalizationInput:
    messages: list[dict[str, Any]]
    content_blocks: list[dict[str, Any]]
    accumulated_text: str
    accumulated_thinking: str
    turn_text: str
    turn_thinking: str
    thinking_committed: bool
    thinking_started_at: float | None
    usage: dict[str, Any]
    safety_blocked: bool = False


@dataclass
class StreamFinalizationResult:
    accumulated_text: str
    completion_args: dict[str, Any] | None
    clear_pending_emit_payloads: bool


async def finalize_stream_result(
    *,
    handler: Any,
    adapter: Any,
    budget: Any,
    delivery: StreamDelivery,
    state: StreamFinalizationInput,
    websocket: Any,
    save_blocks: Callable[[str, list[dict[str, Any]]], Awaitable[None]],
) -> StreamFinalizationResult:
    """执行预算合成并构造完成参数；不提交 completed 终态。"""
    budget_failed = False
    if budget.stop_reason and not state.safety_blocked:
        state.accumulated_text, budget_failed = await _apply_budget_stop(
            handler=handler,
            adapter=adapter,
            budget=budget,
            delivery=delivery,
            state=state,
            save_blocks=save_blocks,
        )

    clear_pending_emit = not state.content_blocks
    if state.content_blocks:
        thinking_duration = (
            int((time.monotonic() - state.thinking_started_at) * 1000)
            if state.thinking_started_at else 0
        )
        append_final_turn_blocks(
            state.content_blocks,
            thinking=state.turn_thinking,
            thinking_committed=state.thinking_committed,
            thinking_duration_ms=thinking_duration,
            text=state.turn_text,
        )

    fallback_thinking_duration = (
        int((time.monotonic() - state.thinking_started_at) * 1000)
        if state.thinking_started_at else None
    )
    result_parts = build_content_parts(
        state.content_blocks,
        fallback_text=state.accumulated_text,
        fallback_thinking=state.accumulated_thinking,
        fallback_thinking_duration_ms=fallback_thinking_duration,
    )
    completion_args = None
    if not budget_failed:
        completion_args = {
            "task_id": delivery.task_id,
            "result": result_parts,
            "credits_consumed": handler._calculate_credits(state.usage),
            "tool_digest": _build_digest(
                state.messages,
                delivery.conversation_id,
                budget.turns_used,
            ),
        }
    await websocket.send_to_task_or_user(
        delivery.task_id,
        delivery.user_id,
        build_stream_end(
            task_id=delivery.task_id,
            conversation_id=delivery.conversation_id,
            message_id=delivery.message_id,
        ),
    )
    return StreamFinalizationResult(
        accumulated_text=state.accumulated_text,
        completion_args=completion_args,
        clear_pending_emit_payloads=clear_pending_emit,
    )


async def _apply_budget_stop(
    *,
    handler: Any,
    adapter: Any,
    budget: Any,
    delivery: StreamDelivery,
    state: StreamFinalizationInput,
    save_blocks: Callable[[str, list[dict[str, Any]]], Awaitable[None]],
) -> tuple[str, bool]:
    from services.agent.stop_policy import synthesize_wrap_up

    reason = stop_message(budget.stop_reason)
    logger.warning(
        f"Budget exhausted | task={delivery.task_id} | "
        f"reason={budget.stop_reason} | turns={budget.turns_used}"
    )
    synthesis = await synthesize_wrap_up(
        adapter=adapter,
        messages=state.messages,
        content_blocks=state.content_blocks,
        reason=reason,
    )
    if synthesis:
        if state.content_blocks:
            state.content_blocks.append({"type": "text", "text": synthesis})
            asyncio.create_task(
                save_blocks(delivery.task_id, state.content_blocks)
            )
        return synthesis, False
    if state.accumulated_text:
        return (
            state.accumulated_text
            + f"\n\n> ⚠️ 已达到执行上限（{reason}），以上为部分结果。",
            False,
        )
    await handler.on_error(
        task_id=delivery.task_id,
        error_code="BUDGET_EXCEEDED",
        error_message=reason,
    )
    return "", True


def _build_digest(
    messages: list[dict[str, Any]],
    conversation_id: str,
    turns_used: int,
) -> dict[str, Any] | None:
    if turns_used <= 1:
        return None
    from services.handlers.tool_digest import build_tool_digest

    try:
        return build_tool_digest(messages, conversation_id)
    except Exception as error:
        logger.warning(f"Tool digest build failed | error={error}")
        return None


def stop_message(reason: str) -> str:
    messages = {
        "wrap_up_budget": "接近执行上限，正在总结当前进展。",
        "max_turns": "已达到单次对话工具调用上限。",
        "wall_timeout": "任务耗时过长，请稍后重试。",
    }
    return messages.get(reason, reason)
