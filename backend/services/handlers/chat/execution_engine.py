"""通道无关的 Chat 模型流与工具循环执行内核。"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from schemas.message import ContentPart
from services.handlers.chat.execution_sink import (
    CollectingExecutionSink,
    ExecutionSink,
)
from services.handlers.chat.outcome_builder import build_content_parts
from services.handlers.chat.stream_session import StreamTotals
from services.handlers.chat.stream_setup import prepare_chat_stream
from services.handlers.chat.tool_loop import (
    append_tool_images,
    apply_tool_results,
    build_running_step,
    compact_tool_context,
    prepare_tool_turn,
)
from services.handlers.chat_tool_mixin import accumulate_tool_call_delta


@dataclass(frozen=True)
class ChatExecutionRequest:
    content: list[ContentPart]
    user_id: str
    conversation_id: str
    task_id: str
    message_id: str
    model_id: str
    context_anchor: Any
    params: dict[str, Any] = field(default_factory=dict)
    permission_mode: str = "auto"
    needs_google_search: bool = False
    calculate_credits: bool = True
    execution_scope: Any = None


@dataclass(frozen=True)
class ChatExecutionResult:
    parts: list[ContentPart]
    content_blocks: list[dict[str, Any]]
    usage: dict[str, Any]
    credits_cost: int
    tool_digest: dict[str, Any] | None
    data_evidence: list[dict[str, Any]] = field(default_factory=list)


async def execute_chat(
    *,
    handler: Any,
    request: ChatExecutionRequest,
    cancellation_event: asyncio.Event | None = None,
    sink: ExecutionSink | None = None,
) -> ChatExecutionResult:
    """执行固定上下文的一次生成，不提交任务、消息或 revision 终态。"""
    event = cancellation_event or asyncio.Event()
    output = sink or CollectingExecutionSink()
    from services.agent.runtime.runtime_state import RuntimeState

    prepared = await prepare_chat_stream(
        handler=handler,
        content=request.content,
        user_id=request.user_id,
        conversation_id=request.conversation_id,
        task_id=request.task_id,
        model_id=request.model_id,
        permission_mode=request.permission_mode,
        needs_google_search=request.needs_google_search,
        params=request.params,
        context_anchor=request.context_anchor,
    )
    runtime_state = getattr(prepared, "runtime_state", RuntimeState.observing())
    handler._adapter = prepared.adapter
    handler._pending_emit_payloads = []
    handler._pending_form_block = None
    totals = StreamTotals()
    blocks: list[dict[str, Any]] = []
    try:
        await output.start()
        await _run_loop(
            handler=handler,
            request=request,
            prepared=prepared,
            cancellation_event=event,
            sink=output,
            totals=totals,
            blocks=blocks,
            runtime_state=runtime_state,
        )
        if prepared.budget.stop_reason and runtime_state.contract.enabled:
            runtime_state.evaluate(budget_exhausted=True)
        await _apply_budget_stop(prepared, totals, blocks)
        await _consume_emit_payloads(handler, blocks, output)
        await output.flush()
        parts = build_content_parts(
            blocks,
            fallback_text=totals.text,
            fallback_thinking=totals.thinking,
        )
        return ChatExecutionResult(
            parts=parts,
            content_blocks=blocks,
            usage=totals.usage,
            credits_cost=(
                handler._calculate_credits(totals.usage)
                if request.calculate_credits else 0
            ),
            tool_digest=_build_digest(
                prepared.messages,
                request.conversation_id,
                prepared.budget.turns_used,
            ),
            data_evidence=runtime_state.persistence_projection(),
        )
    finally:
        await prepared.adapter.close()


async def _run_loop(
    *,
    handler: Any,
    request: ChatExecutionRequest,
    prepared: Any,
    cancellation_event: asyncio.Event,
    sink: ExecutionSink,
    totals: StreamTotals,
    blocks: list[dict[str, Any]],
    runtime_state: Any,
) -> None:
    while not prepared.budget.stop_reason:
        _raise_if_cancelled(cancellation_event)
        prepared.budget.use_turn()
        turn = prepared.budget.turns_used - 1
        tools = prepare_tool_turn(
            core_tools=prepared.core_tools,
            discovered_names=prepared.tool_context.discovered_tools,
            org_id=handler.org_id,
            turn=turn,
            messages=prepared.messages,
            tool_context=prepared.tool_context,
            permission=prepared.permission,
            runtime_state=runtime_state,
        )
        tools = runtime_state.final_tools(tools)
        buffer_output = runtime_state.should_guard_output
        turn_text, turn_thinking, calls = await _read_turn(
            prepared,
            tools,
            cancellation_event,
            sink,
            totals,
            buffer_output=buffer_output,
        )
        if not calls:
            if runtime_state.should_continue_after_plain_text():
                _append_completion_reminder(prepared.messages, runtime_state)
                continue
            from services.agent.runtime.evidence_guard.finalize import (
                GuardDecision,
                append_retry_context,
                review_final_draft,
            )

            decision = review_final_draft(runtime_state, turn_text)
            if decision.decision == GuardDecision.RETRY:
                logger.warning(
                    f"Evidence guard retry | task={request.task_id} | "
                    f"attempt={runtime_state.guard_attempts} | "
                    f"issues={len(decision.receipt.issues)}"
                )
                append_retry_context(prepared.messages, turn_text, decision)
                continue
            if decision.decision == GuardDecision.BLOCK:
                logger.error(
                    f"Evidence guard blocked | task={request.task_id} | "
                    f"attempts={runtime_state.guard_attempts}"
                )
            final_text = decision.text
            if buffer_output:
                await _release_buffered_turn(
                    totals,
                    sink,
                    thinking=turn_thinking,
                    text=final_text,
                )
            await _append_turn_blocks(
                blocks,
                sink,
                thinking=turn_thinking,
                text=final_text,
            )
            return
        if buffer_output:
            await _release_buffered_turn(
                totals,
                sink,
                thinking=turn_thinking,
                text=turn_text,
            )
        await _append_turn_blocks(
            blocks,
            sink,
            thinking=turn_thinking,
            text=turn_text,
        )
        await _execute_tools(
            handler=handler,
            request=request,
            prepared=prepared,
            turn=turn,
            turn_text=turn_text,
            calls=calls,
            cancellation_event=cancellation_event,
            sink=sink,
            blocks=blocks,
            runtime_state=runtime_state,
        )
        if (
            runtime_state.contract.enabled
            and runtime_state.evaluate().decision.value == "finalize"
        ):
            runtime_state.request_final_synthesis()
            _append_final_synthesis_prompt(prepared.messages)


async def _read_turn(
    prepared: Any,
    tools: list[dict[str, Any]],
    cancellation_event: asyncio.Event,
    sink: ExecutionSink,
    totals: StreamTotals,
    *,
    buffer_output: bool = False,
) -> tuple[str, str, list[dict[str, Any]]]:
    turn_text = ""
    turn_thinking = ""
    calls: dict[int, dict[str, Any]] = {}
    async for chunk in prepared.adapter.stream_chat(
        messages=prepared.messages,
        tools=tools,
        **prepared.stream_kwargs,
    ):
        _raise_if_cancelled(cancellation_event)
        if chunk.thinking_content:
            turn_thinking += chunk.thinking_content
            if not buffer_output:
                totals.thinking += chunk.thinking_content
                await sink.on_thinking(chunk.thinking_content)
        if chunk.content:
            turn_text += chunk.content
            if not buffer_output:
                totals.text += chunk.content
                await sink.on_text(chunk.content)
        if chunk.tool_calls:
            accumulate_tool_call_delta(calls, chunk.tool_calls)
        _accumulate_usage(totals, chunk)
    return (
        turn_text,
        turn_thinking,
        sorted(calls.values(), key=lambda call: call.get("id", "")),
    )


async def _append_turn_blocks(
    blocks: list[dict[str, Any]],
    sink: ExecutionSink,
    *,
    thinking: str,
    text: str,
) -> None:
    for block in (
        {"type": "thinking", "text": thinking} if thinking else None,
        {"type": "text", "text": text} if text else None,
    ):
        if block:
            blocks.append(block)
            await sink.on_block(block)


async def _release_buffered_turn(
    totals: StreamTotals,
    sink: ExecutionSink,
    *,
    thinking: str,
    text: str,
) -> None:
    if thinking:
        totals.thinking += thinking
        await sink.on_thinking(thinking)
    if text:
        totals.text += text
        await sink.on_text(text)


async def _execute_tools(
    *,
    handler: Any,
    request: ChatExecutionRequest,
    prepared: Any,
    turn: int,
    turn_text: str,
    calls: list[dict[str, Any]],
    cancellation_event: asyncio.Event,
    sink: ExecutionSink,
    blocks: list[dict[str, Any]],
    runtime_state: Any,
) -> None:
    prepared.messages.append(_assistant_tool_message(turn_text, calls))
    start_times: dict[str, float] = {}
    for call in calls:
        block = build_running_step(call)
        blocks.append(block)
        start_times[call["id"]] = time.monotonic()
        await sink.on_block(block)
    _raise_if_cancelled(cancellation_event)
    results = await handler._execute_tool_calls(
        calls,
        request.task_id,
        request.conversation_id,
        request.message_id,
        request.user_id,
        turn + 1,
        messages=prepared.messages,
        budget=prepared.budget,
        runtime_state=runtime_state,
    )
    _raise_if_cancelled(cancellation_event)
    image_urls = apply_tool_results(
        tool_results=results,
        messages=prepared.messages,
        content_blocks=blocks,
        start_times=start_times,
        tool_context=prepared.tool_context,
        runtime_state=runtime_state,
    )
    append_tool_images(prepared.messages, image_urls)
    await _consume_emit_payloads(handler, blocks, sink)
    await compact_tool_context(
        messages=prepared.messages,
        conversation_source=handler._get_conv_source(request.conversation_id),
        turn=turn,
    )
    logger.info(
        f"Headless tool turn complete | task={request.task_id} | "
        f"turn={turn + 1} | tools={[call['name'] for call in calls]}"
    )


def _assistant_tool_message(
    text: str,
    calls: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": text or None,
        "tool_calls": [
            {
                "id": call["id"],
                "type": "function",
                "function": {
                    "name": call["name"],
                    "arguments": call["arguments"],
                },
            }
            for call in calls
        ],
    }


def _append_completion_reminder(
    messages: list[dict[str, Any]],
    runtime_state: Any,
) -> None:
    reason = runtime_state.last_completion.reason
    messages.append(
        {
            "role": "system",
            "content": (
                "交付合同尚未满足，请继续使用可用工具完成缺失产物。"
                f"当前状态：{reason}"
            ),
        }
    )


def _append_final_synthesis_prompt(messages: list[dict[str, Any]]) -> None:
    messages.append(
        {
            "role": "system",
            "content": (
                "必需产物已经通过运行时验证。请基于现有工具结果完成一次"
                "简洁文字收尾；不得再次调用工具。"
            ),
        }
    )


async def _consume_emit_payloads(
    handler: Any,
    blocks: list[dict[str, Any]],
    sink: ExecutionSink,
) -> None:
    from services.handlers.emit_payloads import build_block_from_payload

    for payload in handler._pending_emit_payloads:
        block = build_block_from_payload(payload)
        if block:
            blocks.append(block)
            await sink.on_block(block)
    handler._pending_emit_payloads = []
    form = getattr(handler, "_pending_form_block", None)
    if form:
        blocks.append(form)
        await sink.on_block(form)
        handler._pending_form_block = None


async def _apply_budget_stop(
    prepared: Any,
    totals: StreamTotals,
    blocks: list[dict[str, Any]],
) -> None:
    if not prepared.budget.stop_reason:
        return
    from services.agent.stop_policy import synthesize_wrap_up
    from services.handlers.chat.stream_finalize import stop_message

    synthesis = await synthesize_wrap_up(
        adapter=prepared.adapter,
        messages=prepared.messages,
        content_blocks=blocks,
        reason=stop_message(prepared.budget.stop_reason),
    )
    if synthesis:
        totals.text = synthesis
        blocks.append({"type": "text", "text": synthesis})
    elif not totals.text:
        raise RuntimeError("CHAT_BUDGET_EXHAUSTED_WITHOUT_OUTPUT")


def _accumulate_usage(totals: StreamTotals, chunk: Any) -> None:
    totals.usage["prompt_tokens"] += chunk.prompt_tokens or 0
    totals.usage["completion_tokens"] += chunk.completion_tokens or 0
    if chunk.credits_consumed is not None:
        totals.usage["api_credits"] = chunk.credits_consumed
    if chunk.finish_reason:
        totals.last_finish_reason = chunk.finish_reason


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


def _raise_if_cancelled(event: asyncio.Event) -> None:
    if event.is_set():
        raise asyncio.CancelledError
