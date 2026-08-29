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
    replay_context: dict[str, Any] | None = None


@dataclass(frozen=True)
class ChatExecutionResult:
    parts: list[ContentPart]
    content_blocks: list[dict[str, Any]]
    usage: dict[str, Any]
    credits_cost: int
    tool_digest: dict[str, Any] | None
    replay_context: dict[str, Any] | None = None


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
    if runtime:
        checkpoint = getattr(output, "flush_progress", None)
        if checkpoint is not None:
            runtime.set_checkpoint_callback(checkpoint)
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
        replay_context=request.replay_context,
    )
    handler._adapter = prepared.adapter
    handler._pending_emit_payloads = []
    handler._pending_form_block = None
    handler._terminal_form_pending = False
    totals = StreamTotals()
    blocks: list[dict[str, Any]] = _initial_replay_blocks(
        request.replay_context,
    )
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
            runtime=runtime,
        )
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
            replay_context=_build_replay_context(
                prepared.messages,
                blocks,
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
) -> None:
    while not prepared.budget.stop_reason:
        if runtime:
            runtime.set_state(ConversationState.RUNNING_MODEL)
            await runtime.safe_point(
                SafePoint.BEFORE_MODEL,
                replay_payload=_build_replay_context(
                    prepared.messages,
                    blocks,
                    prepared.budget.turns_used,
                ),
            )
            _inject_subtask_completions(
                prepared.messages,
                runtime.consume_subtask_completions(),
            )
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
        turn_text, turn_thinking, calls, previewed_call_ids = await _read_turn(
            prepared,
            tools,
            cancellation_event,
            sink,
            totals,
            blocks,
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
            return
        await _execute_tools(
            handler=handler,
            request=request,
            prepared=prepared,
            turn=turn,
            turn_text=turn_text,
            calls=calls,
            previewed_call_ids=previewed_call_ids,
            cancellation_event=cancellation_event,
            sink=sink,
            blocks=blocks,
            runtime=runtime,
        )
        # FormBlockResult 是一个完整的交付物，不再发起额外的模型回合。
        # 这样既避免重复文案，也保证表单是该消息唯一的确认入口。
        if getattr(handler, "_terminal_form_pending", False):
            return


async def _read_turn(
    prepared: Any,
    tools: list[dict[str, Any]],
    cancellation_event: asyncio.Event,
    sink: ExecutionSink,
    totals: StreamTotals,
    blocks: list[dict[str, Any]],
    runtime: ConversationTurnRuntime | None,
) -> tuple[str, str, list[dict[str, Any]], set[str]]:
    turn_text = ""
    turn_thinking = ""
    calls: dict[int, dict[str, Any]] = {}
    previewed_indices: set[int] = set()
    preview_ids: dict[int, str] = {}
    stream = prepared.adapter.stream_chat(
        messages=prepared.messages,
        tools=tools,
        **prepared.stream_kwargs,
    )
    stream_iterator = stream.__aiter__()
    while True:
        next_chunk = asyncio.create_task(stream_iterator.__anext__())
        command_waiter = (
            asyncio.create_task(runtime.wait_for_command())
            if runtime is not None else None
        )
        wait_set = {next_chunk}
        if command_waiter is not None:
            wait_set.add(command_waiter)
        done, _ = await asyncio.wait(
            wait_set,
            return_when=asyncio.FIRST_COMPLETED,
        )

        # 命令先到时主动结束 provider stream；随后 safe_point 会刷盘并
        # 把 PAUSE/CANCEL 归约为控制流异常。不会每个 token 查询数据库。
        if command_waiter is not None and command_waiter in done and next_chunk not in done:
            next_chunk.cancel()
            await asyncio.gather(next_chunk, return_exceptions=True)
            await runtime.safe_point(SafePoint.MODEL_CHUNK)
            continue

        if command_waiter is not None and not command_waiter.done():
            command_waiter.cancel()
            await asyncio.gather(command_waiter, return_exceptions=True)

        try:
            chunk = next_chunk.result()
        except StopAsyncIteration:
            break
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
            if runtime is not None:
                for index, call in calls.items():
                    if index in previewed_indices or not call.get("name"):
                        continue
                    preview_id = _actor_tool_preview_id(runtime.turn_id, index)
                    call["id"] = preview_id
                    preview_ids[index] = preview_id
                    previewed_indices.add(index)
                    preview_block = {
                        "type": "tool_step",
                        "tool_name": call["name"],
                        "tool_call_id": preview_id,
                        "status": "running",
                    }
                    if not any(
                        block.get("tool_call_id") == preview_id
                        for block in blocks
                    ):
                        blocks.append(preview_block)
                        await sink.on_block(preview_block)
        _accumulate_usage(totals, chunk)
    ordered_indices = sorted(calls)
    ordered_calls = [calls[index] for index in ordered_indices]
    previewed_call_ids: set[str] = set()
    if runtime:
        for index, call in zip(ordered_indices, ordered_calls):
            if index in preview_ids:
                call["id"] = preview_ids[index]
                previewed_call_ids.add(call["id"])
            else:
                call["id"] = _stable_actor_tool_call_id(
                    runtime.turn_id,
                    index,
                    call.get("name", ""),
                    call.get("arguments", ""),
                )
    else:
        ordered_calls.sort(key=lambda call: call.get("id", ""))
    return turn_text, turn_thinking, ordered_calls, previewed_call_ids


def _actor_tool_preview_id(turn_id: str, index: int) -> str:
    """Return an ID shared by preview/final steps; args hash stays in the ledger."""
    return f"actor-call:{turn_id}:{index}"


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


def _actor_tool_completion_command_id(
    task_id: str,
    turn_id: str,
    tool_call_ids: list[str],
) -> str:
    """生成有界且稳定的工具批次去重键；原始 ID 仍保存在事件 payload。"""
    encoded_ids = json.dumps(
        tool_call_ids,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    fingerprint = hashlib.sha256(encoded_ids).hexdigest()[:32]
    return f"tool-batch:{task_id}:{turn_id}:{len(tool_call_ids)}:{fingerprint}"


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
    previewed_call_ids: set[str],
    cancellation_event: asyncio.Event,
    sink: ExecutionSink,
    blocks: list[dict[str, Any]],
    runtime: ConversationTurnRuntime | None,
) -> None:
    prepared.messages.append(_assistant_tool_message(turn_text, calls))
    start_times: dict[str, float] = {}
    for call in calls:
        block = build_running_step(call)
        start_times[call["id"]] = time.monotonic()
        if call["id"] in previewed_call_ids:
            for index, existing in enumerate(blocks):
                if existing.get("tool_call_id") == call["id"]:
                    blocks[index] = block
                    break
            else:
                blocks.append(block)
            await sink.on_block_update(block)
        else:
            blocks.append(block)
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
        command_id = _actor_tool_completion_command_id(
            request.task_id,
            runtime.turn_id,
            tool_call_ids,
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
    if runtime:
        await runtime.safe_point(
            SafePoint.AFTER_TOOL,
            replay_payload=_build_replay_context(
                prepared.messages,
                blocks,
                turn,
                tool_call_ids=[call["id"] for call in calls],
            ),
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


def _build_replay_context(
    messages: list[dict[str, Any]],
    blocks: list[dict[str, Any]],
    turn_index: int,
    *,
    tool_call_ids: list[str] | None = None,
) -> dict[str, Any]:
    """构造模型可重放上下文；不把 token 级 DeliveryProgress 当 checkpoint。"""
    return {
        "messages": json.loads(
            json.dumps(messages, ensure_ascii=False, default=str),
        ),
        "content_blocks": json.loads(
            json.dumps(blocks, ensure_ascii=False, default=str),
        ),
        "turn_index": turn_index,
        "tool_call_ids": list(tool_call_ids or []),
    }


def _inject_subtask_completions(
    messages: list[dict[str, Any]],
    completions: list[dict[str, Any]],
) -> None:
    """把安全点归约后的子任务结果注入下一次模型输入。

    子任务不是模型 tool_call，因此使用受控 system 事件承载结果，
    避免伪造未发生的 tool_call/tool_result 配对；消费列表由 Runtime
    一次性清空，重复 Redis/控制事件不会重复注入。
    """
    for completion in completions:
        result = json.dumps(
            completion.get("result") or {},
            ensure_ascii=False,
            default=str,
        )
        status = completion.get("status") or "failed"
        error_message = completion.get("error_message") or ""
        suffix = f"；错误：{error_message}" if error_message else ""
        messages.append({
            "role": "system",
            "content": (
                "[子任务完成回传] "
                f"child_task_id={completion.get('child_task_id')} "
                f"status={status} result={result}{suffix}"
            ),
        })


def _initial_replay_blocks(
    replay_context: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if replay_context is None:
        return []
    raw_blocks = replay_context.get("content_blocks", [])
    if not isinstance(raw_blocks, list) or not all(
        isinstance(block, dict) for block in raw_blocks
    ):
        raise RuntimeError("ACTOR_REPLAY_CONTEXT_BLOCKS_INVALID")
    return [dict(block) for block in raw_blocks]


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
