"""通道无关的 Chat 模型流与工具循环执行内核。"""

from __future__ import annotations

import asyncio
import hashlib
import json
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
from services.conversation_commands import (
    CommandType,
    ConversationCommand,
    SafePoint,
)
from services.conversation_turn_runtime import ConversationTurnRuntime
from services.conversation_state import ConversationState


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
    resume_state: dict[str, Any] | None = None


@dataclass(frozen=True)
class ChatExecutionResult:
    parts: list[ContentPart]
    content_blocks: list[dict[str, Any]]
    usage: dict[str, Any]
    credits_cost: int
    tool_digest: dict[str, Any] | None


async def execute_chat(
    *,
    handler: Any,
    request: ChatExecutionRequest,
    cancellation_event: asyncio.Event | None = None,
    sink: ExecutionSink | None = None,
    runtime: ConversationTurnRuntime | None = None,
) -> ChatExecutionResult:
    """执行固定上下文的一次生成，不提交任务、消息或 revision 终态。"""
    event = cancellation_event or asyncio.Event()
    output = sink or CollectingExecutionSink()
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
    handler._adapter = prepared.adapter
    if runtime is not None:
        runtime.set_command_applier(
            lambda command: _apply_runtime_command(command, prepared.messages)
        )
    if request.resume_state:
        _restore_resume_state(prepared, request.resume_state)
    handler._pending_emit_payloads = []
    handler._pending_form_block = None
    totals = StreamTotals()
    blocks: list[dict[str, Any]] = []
    if request.resume_state:
        _restore_output_state(totals, blocks, prepared, request.resume_state)
    try:
        await output.start()
        stable_state: dict[str, Any] = {}

        def capture_stable_state() -> None:
            stable_state.clear()
            stable_state.update(
                _build_resume_state(prepared, blocks, totals, runtime)
            )

        capture_stable_state()
        if runtime is not None:
            async def persist_checkpoint() -> int | None:
                capture_stable_state()
                persist = getattr(output, "persist_checkpoint", None)
                if persist is not None:
                    await persist(stable_state)
                else:
                    persist_progress = getattr(output, "persist_progress", None)
                    if persist_progress is not None:
                        await persist_progress()
                actor_db = getattr(handler, "db", None)
                token = getattr(runtime, "execution_token", None)
                if actor_db is None or not token or not hasattr(actor_db, "rpc"):
                    return None
                response = await actor_db.rpc(
                    "save_generation_checkpoint",
                    {
                        "p_task_id": request.task_id,
                        "p_execution_token": token,
                        "p_safe_point": (
                            runtime.last_safe_point.value
                            if runtime.last_safe_point else "unknown"
                        ),
                        "p_state": stable_state,
                    },
                ).execute()
                result = response.data if response else None
                if not isinstance(result, dict):
                    raise RuntimeError("ACTOR_CHECKPOINT_RESULT_INVALID")
                if result.get("outcome") in {
                    "ownership_lost", "lease_expired", "terminal",
                }:
                    raise asyncio.CancelledError
                version = result.get("version")
                return int(version) if isinstance(version, int) else None

            runtime.set_checkpoint(persist_checkpoint)
        await _run_loop(
            handler=handler,
            request=request,
            prepared=prepared,
            cancellation_event=event,
            sink=output,
            totals=totals,
            blocks=blocks,
            runtime=runtime,
            capture_stable=capture_stable_state,
        )
        if runtime is not None:
            await runtime.safe_point(SafePoint.BEFORE_COMMIT)
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
    runtime: ConversationTurnRuntime | None,
    capture_stable: Any = None,
) -> None:
    while not prepared.budget.stop_reason:
        if runtime:
            runtime.set_state(ConversationState.RUNNING_MODEL)
            await runtime.safe_point(SafePoint.BEFORE_MODEL)
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
        )
        turn_text, turn_thinking, calls = await _read_turn(
            prepared,
            tools,
            cancellation_event,
            sink,
            totals,
            runtime,
        )
        if runtime:
            await runtime.safe_point(SafePoint.AFTER_MODEL)
        await _append_turn_blocks(
            blocks,
            sink,
            thinking=turn_thinking,
            text=turn_text,
        )
        if not calls:
            if capture_stable is not None:
                capture_stable()
            return
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
            runtime=runtime,
            capture_stable=capture_stable,
        )


async def _read_turn(
    prepared: Any,
    tools: list[dict[str, Any]],
    cancellation_event: asyncio.Event,
    sink: ExecutionSink,
    totals: StreamTotals,
    runtime: ConversationTurnRuntime | None,
) -> tuple[str, str, list[dict[str, Any]]]:
    turn_text = ""
    turn_thinking = ""
    calls: dict[int, dict[str, Any]] = {}
    async for chunk in prepared.adapter.stream_chat(
        messages=prepared.messages,
        tools=tools,
        **prepared.stream_kwargs,
    ):
        if runtime:
            await runtime.safe_point(SafePoint.MODEL_CHUNK)
        _raise_if_cancelled(cancellation_event)
        if chunk.thinking_content:
            turn_thinking += chunk.thinking_content
            totals.thinking += chunk.thinking_content
            await sink.on_thinking(chunk.thinking_content)
        if chunk.content:
            turn_text += chunk.content
            totals.text += chunk.content
            await sink.on_text(chunk.content)
        if chunk.tool_calls:
            accumulate_tool_call_delta(calls, chunk.tool_calls)
        _accumulate_usage(totals, chunk)
    ordered_calls = [calls[index] for index in sorted(calls)]
    if runtime:
        for index, call in enumerate(ordered_calls):
            call["id"] = _stable_actor_tool_call_id(
                runtime.turn_id,
                index,
                call.get("name", ""),
                call.get("arguments", ""),
            )
    else:
        ordered_calls.sort(key=lambda call: call.get("id", ""))
    return turn_text, turn_thinking, ordered_calls


def _stable_actor_tool_call_id(
    turn_id: str,
    index: int,
    tool_name: str,
    arguments: str,
) -> str:
    """为 Actor 重试生成稳定调用 ID，避免供应商 call ID 变化导致重复副作用。"""
    fingerprint = hashlib.sha256(
        f"{tool_name}\n{arguments}".encode("utf-8")
    ).hexdigest()[:24]
    return f"actor-call:{turn_id}:{index}:{fingerprint}"


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
    runtime: ConversationTurnRuntime | None,
    capture_stable: Any = None,
) -> None:
    prepared.messages.append(_assistant_tool_message(turn_text, calls))
    start_times: dict[str, float] = {}
    for call in calls:
        block = build_running_step(call)
        blocks.append(block)
        start_times[call["id"]] = time.monotonic()
        await sink.on_block(block)
    if runtime:
        runtime.set_state(ConversationState.WAITING_TOOL)
        await runtime.safe_point(SafePoint.BEFORE_TOOL)
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
    )
    if runtime:
        tool_call_ids = [call["id"] for call in calls]
        command_id = (
            f"tool-batch:{request.task_id}:{runtime.turn_id}:"
            f"{','.join(tool_call_ids)}"
        )
        command = ConversationCommand(
            command_id=command_id,
            command_type=CommandType.TOOL_COMPLETED,
            conversation_id=request.conversation_id,
            task_id=request.task_id,
            turn_id=runtime.turn_id,
            payload={"tool_call_ids": tool_call_ids},
        )
        if runtime.command_store and runtime.execution_token:
            append = getattr(runtime.command_store, "append", None)
            if append is not None:
                await append(
                    conversation_id=request.conversation_id,
                    task_id=request.task_id,
                    turn_id=runtime.turn_id,
                    command_type=CommandType.TOOL_COMPLETED,
                    dedupe_key=command_id,
                    payload=command.payload or {},
                )
        runtime.push(command)
    _raise_if_cancelled(cancellation_event)
    image_urls = apply_tool_results(
        tool_results=results,
        messages=prepared.messages,
        content_blocks=blocks,
        start_times=start_times,
        tool_context=prepared.tool_context,
    )
    append_tool_images(prepared.messages, image_urls)
    await _consume_emit_payloads(handler, blocks, sink)
    await compact_tool_context(
        messages=prepared.messages,
        conversation_source=handler._get_conv_source(request.conversation_id),
        turn=turn,
    )
    if capture_stable is not None:
        capture_stable()
    if runtime:
        await runtime.safe_point(SafePoint.AFTER_TOOL)
    logger.info(
        f"Headless tool turn complete | task={request.task_id} | "
        f"turn={turn + 1} | tools={[call['name'] for call in calls]}"
    )


def _restore_resume_state(prepared: Any, state: dict[str, Any]) -> None:
    messages = state.get("messages")
    if isinstance(messages, list) and all(isinstance(item, dict) for item in messages):
        prepared.messages = [dict(item) for item in messages]
    restore = getattr(prepared.budget, "restore", None)
    if restore is not None:
        restore(state.get("budget"))


def _restore_output_state(
    totals: StreamTotals,
    blocks: list[dict[str, Any]],
    prepared: Any,
    state: dict[str, Any],
) -> None:
    raw_blocks = state.get("blocks")
    if isinstance(raw_blocks, list):
        blocks.extend(
            dict(item) for item in raw_blocks if isinstance(item, dict)
        )
    text = state.get("text")
    thinking = state.get("thinking")
    if isinstance(text, str):
        totals.text = text
    if isinstance(thinking, str):
        totals.thinking = thinking
    usage = state.get("usage")
    if isinstance(usage, dict):
        totals.usage.update(usage)


def _build_resume_state(
    prepared: Any,
    blocks: list[dict[str, Any]],
    totals: StreamTotals,
    runtime: ConversationTurnRuntime | None,
) -> dict[str, Any]:
    """只保存最近完成安全边界的状态；恢复从 before_model 重新进入。"""
    safe_point = runtime.last_safe_point.value if runtime and runtime.last_safe_point else "initial"
    return _json_safe({
        "version": 1,
        "resume_from": "before_model",
        "safe_point": safe_point,
        "messages": prepared.messages,
        "blocks": blocks,
        "text": totals.text,
        "thinking": totals.thinking,
        "usage": totals.usage,
        "budget": (
            prepared.budget.snapshot()
            if hasattr(prepared.budget, "snapshot")
            else {
                "turns_used": getattr(prepared.budget, "turns_used", 0),
                "elapsed": getattr(prepared.budget, "elapsed", 0.0),
            }
        ),
    })


def _json_safe(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return {}


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


async def _apply_runtime_command(
    command: ConversationCommand,
    messages: list[dict[str, Any]],
) -> None:
    """把需要进入下一轮模型上下文的持久事件应用到当前消息投影。"""
    if command.command_type is not CommandType.SUBTASK_COMPLETED:
        return
    payload = command.payload or {}
    child_task_id = str(payload.get("child_task_id") or "")
    parent_command_id = str(payload.get("parent_command_id") or "")
    if not child_task_id or not parent_command_id:
        raise RuntimeError("ACTOR_SUBTASK_RESULT_INVALID")
    result = payload.get("result")
    if not isinstance(result, dict):
        result = {}
    messages.append({
        "role": "tool",
        "tool_call_id": parent_command_id,
        "content": json.dumps({
            "child_task_id": child_task_id,
            "status": payload.get("status") or "completed",
            "result": result,
            "error_message": payload.get("error_message") or "",
        }, ensure_ascii=False),
    })


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
