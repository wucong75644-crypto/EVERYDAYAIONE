"""单轮 Provider 流读取与请求级累积状态。"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from loguru import logger

from schemas.websocket import (
    build_content_block_add,
    build_message_chunk,
    build_thinking_chunk,
)
from services.handlers.chat_tool_mixin import accumulate_tool_call_delta


@dataclass(frozen=True)
class StreamDelivery:
    task_id: str
    conversation_id: str
    message_id: str
    user_id: str
    org_id: str | None


@dataclass
class StreamTotals:
    text: str = ""
    thinking: str = ""
    usage: dict[str, Any] = field(
        default_factory=lambda: {"prompt_tokens": 0, "completion_tokens": 0}
    )
    chunk_count: int = 0
    last_finish_reason: str | None = None


@dataclass
class StreamTurnResult:
    text: str
    thinking: str
    thinking_committed: bool
    thinking_started_at: float | None
    request_started_at: float
    tool_calls: dict[int, dict[str, Any]]
    cancelled: bool


async def read_stream_turn(
    *,
    adapter: Any,
    messages: list[dict[str, Any]],
    stream_kwargs: dict[str, Any],
    thinking_effort: str | None,
    thinking_mode: str | None,
    delivery: StreamDelivery,
    totals: StreamTotals,
    content_blocks: list[dict[str, Any]],
    websocket: Any,
    save_accumulated: Callable[[str, str], Awaitable[None]],
) -> StreamTurnResult:
    """读取一轮模型流并产生工具调用；不执行工具、不决定任务终态。"""
    turn_text = ""
    turn_thinking = ""
    thinking_committed = False
    thinking_started_at: float | None = None
    request_started_at = time.monotonic()
    tool_calls: dict[int, dict[str, Any]] = {}
    cancelled = False

    async for chunk in adapter.stream_chat(
        messages=messages,
        reasoning_effort=thinking_effort,
        thinking_mode=thinking_mode,
        **stream_kwargs,
    ):
        if websocket.is_cancelled(delivery.task_id):
            from services.cancel_metrics import record_cancel_latency

            record_cancel_latency(
                delivery.task_id,
                delivery.org_id,
                phase="stream",
                had_partial=bool(turn_text or turn_thinking),
                tools_in_flight=len(tool_calls),
            )
            logger.info(
                f"LLM stream cancelled by user | task={delivery.task_id}"
            )
            cancelled = True
            break

        if chunk.thinking_content:
            if thinking_started_at is None:
                thinking_started_at = time.monotonic()
            turn_thinking += chunk.thinking_content
            totals.thinking += chunk.thinking_content
            await websocket.send_to_task_or_user(
                delivery.task_id,
                delivery.user_id,
                build_thinking_chunk(
                    task_id=delivery.task_id,
                    conversation_id=delivery.conversation_id,
                    message_id=delivery.message_id,
                    chunk=chunk.thinking_content,
                    accumulated=totals.thinking,
                ),
            )

        if chunk.content and not thinking_committed:
            thinking_committed = True
            duration_ms = _thinking_duration(
                thinking_started_at,
                request_started_at,
            )
            thinking_block = {
                "type": "thinking",
                "text": turn_thinking,
                "duration_ms": duration_ms,
            }
            content_blocks.append(thinking_block)
            await _push_block(websocket, delivery, thinking_block)

        if chunk.content:
            turn_text += chunk.content
            totals.text += chunk.content
            totals.chunk_count += 1
            await websocket.send_to_task_or_user(
                delivery.task_id,
                delivery.user_id,
                build_message_chunk(
                    task_id=delivery.task_id,
                    conversation_id=delivery.conversation_id,
                    message_id=delivery.message_id,
                    chunk=chunk.content,
                ),
            )
            if totals.chunk_count % 20 == 0:
                asyncio.create_task(
                    save_accumulated(delivery.task_id, totals.text)
                )

        if chunk.tool_calls:
            accumulate_tool_call_delta(tool_calls, chunk.tool_calls)
        _accumulate_usage(totals, chunk)

    return StreamTurnResult(
        text=turn_text,
        thinking=turn_thinking,
        thinking_committed=thinking_committed,
        thinking_started_at=thinking_started_at,
        request_started_at=request_started_at,
        tool_calls=tool_calls,
        cancelled=cancelled,
    )


def _thinking_duration(
    thinking_started_at: float | None,
    request_started_at: float,
) -> int:
    started_at = thinking_started_at or request_started_at
    return int((time.monotonic() - started_at) * 1000)


async def _push_block(
    websocket: Any,
    delivery: StreamDelivery,
    block: dict[str, Any],
) -> None:
    try:
        await websocket.send_to_task_or_user(
            delivery.task_id,
            delivery.user_id,
            build_content_block_add(
                task_id=delivery.task_id,
                conversation_id=delivery.conversation_id,
                message_id=delivery.message_id,
                block=block,
            ),
        )
    except Exception as error:
        logger.warning(
            f"thinking block push failed | task={delivery.task_id} | {error}"
        )


def _accumulate_usage(totals: StreamTotals, chunk: Any) -> None:
    if chunk.prompt_tokens or chunk.completion_tokens:
        totals.usage["prompt_tokens"] += chunk.prompt_tokens or 0
        totals.usage["completion_tokens"] += chunk.completion_tokens or 0
    if chunk.credits_consumed is not None:
        totals.usage["api_credits"] = chunk.credits_consumed
    if chunk.finish_reason:
        totals.last_finish_reason = chunk.finish_reason
