"""通道无关的 Chat 模型流与工具循环执行内核。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from loguru import logger

from schemas.message import ContentPart, TextPart
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
    thinking_effort: str | None = None
    thinking_mode: str | None = None
    steer_reader: Callable[[], str | None] | None = None
    on_cancel: Callable[
        [list[dict[str, Any]], list[dict[str, Any]], str, str, str],
        Awaitable[None],
    ] | None = None


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
    model_gateway = _get_model_gateway(prepared)
    handler._adapter = model_gateway
    previous_sink = getattr(handler, "_execution_sink", None)
    handler._execution_sink = output
    handler._pending_emit_payloads = []
    handler._pending_form_block = None
    handler._terminal_form_pending = False
    totals = StreamTotals()
    blocks: list[dict[str, Any]] = _initial_replay_blocks(
        request.replay_context,
    )
    try:
        await output.start()
        form_hint = await _run_loop(
            handler=handler,
            request=request,
            prepared=prepared,
            cancellation_event=event,
            sink=output,
            totals=totals,
            blocks=blocks,
            runtime=runtime,
            model_round=_initial_model_round(request.replay_context),
        )
        await _apply_budget_stop(prepared, totals, blocks, output)
        await _consume_emit_payloads(handler, blocks, output)
        await output.flush()
        parts = build_content_parts(
            blocks,
            fallback_text=totals.text,
            fallback_thinking=totals.thinking,
        )
        if form_hint:
            parts.append(TextPart(text=form_hint))
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
        await model_gateway.close()
        if getattr(handler, "_adapter", None) is model_gateway:
            handler._adapter = None
        if getattr(handler, "_execution_sink", None) is output:
            handler._execution_sink = previous_sink


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
    model_round: int = 0,
) -> str | None:
    thinking_mode = request.thinking_mode
    empty_output_retried = False
    while not prepared.budget.stop_reason:
        if runtime:
            runtime.set_state(ConversationState.RUNNING_MODEL)
            await runtime.safe_point(
                SafePoint.BEFORE_MODEL,
                replay_payload=_build_replay_context(
                    prepared.messages,
                    blocks,
                    prepared.budget.turns_used,
                    next_model_round=model_round,
                ),
            )
            _inject_subtask_completions(
                prepared.messages,
                runtime.consume_subtask_completions(),
            )
            _inject_steer_messages(
                prepared.messages,
                runtime.consume_steer_messages(),
            )
        await _check_cancelled(
            cancellation_event, request, prepared.messages, blocks,
            totals, "loop_top",
        )
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
        current_model_round = model_round
        turn_text, turn_thinking, calls, previewed_call_ids = await _read_turn(
            prepared,
            tools,
            cancellation_event,
            sink,
            totals,
            blocks,
            runtime,
            request.thinking_effort,
            thinking_mode,
            request,
            model_round=current_model_round,
        )
        model_round += 1
        if runtime:
            await runtime.safe_point(SafePoint.AFTER_MODEL)
        await _append_turn_blocks(
            blocks,
            sink,
            thinking=turn_thinking,
            text=turn_text,
        )
        if not calls:
            if not turn_text and prepared.budget.turns_used > 1:
                if not empty_output_retried:
                    empty_output_retried = True
                    thinking_mode = None
                    prepared.messages.append({
                        "role": "user",
                        "content": "请根据刚才的工具执行结果，直接告诉我结论。",
                    })
                    continue
                fallback = _last_tool_output(blocks)
                fallback_text = (
                    "抱歉，我在整理回复时遇到了问题。以下是工具返回的原始结果：\n\n"
                    + (fallback or "（无工具输出）")
                )
                totals.text += fallback_text
                blocks.append({"type": "text", "text": fallback_text})
                await sink.on_block(blocks[-1])
            return None
        form_hint = await _execute_tools(
            handler=handler,
            request=request,
            prepared=prepared,
            turn=turn,
            turn_text=turn_text,
            calls=calls,
            previewed_call_ids=previewed_call_ids,
            next_model_round=model_round,
            cancellation_event=cancellation_event,
            sink=sink,
            blocks=blocks,
            runtime=runtime,
            totals=totals,
        )
        # FormBlockResult 是一个完整的交付物，不再发起额外的模型回合。
        # 这样既避免重复文案，也保证表单是该消息唯一的确认入口。
        if getattr(handler, "_terminal_form_pending", False):
            return None
        if form_hint:
            return form_hint
    return None


async def _read_turn(
    prepared: Any,
    tools: list[dict[str, Any]],
    cancellation_event: asyncio.Event,
    sink: ExecutionSink,
    totals: StreamTotals,
    blocks: list[dict[str, Any]],
    runtime: ConversationTurnRuntime | None,
    thinking_effort: str | None = None,
    thinking_mode: str | None = None,
    request: ChatExecutionRequest | None = None,
    model_round: int = 0,
) -> tuple[str, str, list[dict[str, Any]], set[str]]:
    turn_text = ""
    turn_thinking = ""
    calls: dict[int, dict[str, Any]] = {}
    previewed_indices: set[int] = set()
    preview_ids: dict[int, str] = {}
    model_gateway = _get_model_gateway(prepared)
    stream = model_gateway.stream_chat(
        messages=prepared.messages,
        tools=tools,
        reasoning_effort=thinking_effort,
        thinking_mode=thinking_mode,
        **prepared.stream_kwargs,
    )
    stream_iterator = stream.__aiter__()
    while True:
        next_chunk = asyncio.create_task(stream_iterator.__anext__())
        cancel_waiter = asyncio.create_task(cancellation_event.wait())
        command_waiter = (
            asyncio.create_task(runtime.wait_for_command())
            if runtime is not None else None
        )
        wait_set = {next_chunk}
        wait_set.add(cancel_waiter)
        if command_waiter is not None:
            wait_set.add(command_waiter)
        done, _ = await asyncio.wait(
            wait_set,
            return_when=asyncio.FIRST_COMPLETED,
        )

        # 命令先到时主动结束 provider stream；随后 safe_point 会刷盘并
        # 把 PAUSE/CANCEL 归约为控制流异常。不会每个 token 查询数据库。
        if cancel_waiter in done and next_chunk not in done:
            next_chunk.cancel()
            await asyncio.gather(next_chunk, return_exceptions=True)
            if command_waiter is not None:
                command_waiter.cancel()
                await asyncio.gather(command_waiter, return_exceptions=True)
            await _check_cancelled(
                cancellation_event, request, prepared.messages, blocks,
                totals, "stream",
            )

        if command_waiter is not None and command_waiter in done and next_chunk not in done:
            next_chunk.cancel()
            await asyncio.gather(next_chunk, return_exceptions=True)
            cancel_waiter.cancel()
            await asyncio.gather(cancel_waiter, return_exceptions=True)
            await runtime.safe_point(SafePoint.MODEL_CHUNK)
            continue

        if command_waiter is not None and not command_waiter.done():
            command_waiter.cancel()
            await asyncio.gather(command_waiter, return_exceptions=True)
        if not cancel_waiter.done():
            cancel_waiter.cancel()
        await asyncio.gather(cancel_waiter, return_exceptions=True)

        try:
            chunk = next_chunk.result()
        except StopAsyncIteration:
            break
        if runtime:
            await runtime.safe_point(SafePoint.MODEL_CHUNK)
        await _check_cancelled(
            cancellation_event, request, prepared.messages, blocks,
            totals, "stream",
        )
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
                    preview_id = _actor_tool_preview_id(
                        runtime.turn_id, model_round, index,
                    )
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
                    model_round,
                    index,
                    call.get("name", ""),
                    call.get("arguments", ""),
                )
    else:
        ordered_calls.sort(key=lambda call: call.get("id", ""))
    return turn_text, turn_thinking, ordered_calls, previewed_call_ids


def _get_model_gateway(prepared: Any) -> Any:
    """读取 Gateway 会话；兼容旧测试注入的 adapter-shaped doubles。"""
    model_gateway = getattr(prepared, "model_gateway", None)
    if model_gateway is not None:
        return model_gateway
    return prepared.adapter


def _initial_model_round(replay_context: dict[str, Any] | None) -> int:
    """恢复 Actor 时从 checkpoint 继续使用稳定的模型回合号。"""
    if not replay_context:
        return 0
    next_model_round = replay_context.get("next_model_round")
    if (
        isinstance(next_model_round, int)
        and not isinstance(next_model_round, bool)
    ):
        return max(0, next_model_round)
    turn_index = replay_context.get("turn_index")
    if isinstance(turn_index, int) and not isinstance(turn_index, bool):
        # 兼容旧 checkpoint：旧 payload 只记录已完成的本地回合索引。
        return max(0, turn_index + 1)
    return 0


def _actor_tool_preview_id(
    turn_id: str,
    model_round: int,
    index: int,
) -> str:
    """Return one ID shared by a streamed call's preview and final step."""
    return f"actor-call:{turn_id}:round:{model_round}:index:{index}"


def _stable_actor_tool_call_id(
    turn_id: str,
    model_round: int,
    index: int,
    tool_name: str,
    arguments: str,
) -> str:
    """为 Actor 重试生成稳定调用 ID，避免供应商 ID 变化导致重复副作用。"""
    fingerprint = hashlib.sha256(
        f"{tool_name}\n{arguments}".encode("utf-8")
    ).hexdigest()[:24]
    return f"actor-call:{turn_id}:round:{model_round}:index:{index}:{fingerprint}"


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
    if text and not thinking and getattr(sink, "emit_empty_thinking", False):
        block = {"type": "thinking", "text": "", "duration_ms": 0}
        blocks.append(block)
        await sink.on_block(block)
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
    next_model_round: int | None = None,
    totals: StreamTotals | None = None,
) -> str | None:
    totals = totals or StreamTotals()
    prepared.messages.append(_assistant_tool_message(turn_text, calls))
    await _sink_tool_calls(sink, calls, turn + 1)
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
    await _check_cancelled(
        cancellation_event, request, prepared.messages, blocks,
        totals, "before_tool",
    )
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
    await _check_cancelled(
        cancellation_event, request, prepared.messages, blocks,
        totals, "post_tool",
    )
    image_urls = apply_tool_results(
        tool_results=results,
        messages=prepared.messages,
        content_blocks=blocks,
        start_times=start_times,
        tool_context=prepared.tool_context,
    )
    append_tool_images(prepared.messages, image_urls)
    if runtime:
        await runtime.safe_point(SafePoint.AFTER_TOOL)
        _inject_steer_messages(
            prepared.messages,
            runtime.consume_steer_messages(),
        )
    elif getattr(request, "steer_reader", None) is not None:
        steer_message = request.steer_reader()
        if steer_message:
            prepared.messages.append({"role": "user", "content": steer_message})
    form_hint = await _consume_emit_payloads(handler, blocks, sink)
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
                next_model_round=next_model_round,
            ),
        )
    logger.info(
        f"Headless tool turn complete | task={request.task_id} | "
        f"turn={turn + 1} | tools={[call['name'] for call in calls]}"
    )
    return form_hint


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
    next_model_round: int | None = None,
) -> dict[str, Any]:
    """构造模型可重放上下文；不把 token 级 DeliveryProgress 当 checkpoint。"""
    payload = {
        "messages": json.loads(
            json.dumps(messages, ensure_ascii=False, default=str),
        ),
        "content_blocks": json.loads(
            json.dumps(blocks, ensure_ascii=False, default=str),
        ),
        "turn_index": turn_index,
        "tool_call_ids": list(tool_call_ids or []),
    }
    if next_model_round is not None:
        payload["next_model_round"] = next_model_round
    return payload


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
) -> str | None:
    from services.handlers.emit_payloads import build_block_from_payload

    for payload in handler._pending_emit_payloads:
        block = build_block_from_payload(payload)
        if block:
            blocks.append(block)
            await sink.on_block(block)
    handler._pending_emit_payloads = []
    form = getattr(handler, "_pending_form_block", None)
    form_hint = None
    if form:
        blocks.append(form)
        await sink.on_block(form)
        handler._pending_form_block = None
        form_hint = "请在上方表单中确认信息后点击提交。"
        await sink.on_text(form_hint)
    return form_hint


async def _apply_budget_stop(
    prepared: Any,
    totals: StreamTotals,
    blocks: list[dict[str, Any]],
    sink: ExecutionSink,
) -> None:
    if not prepared.budget.stop_reason:
        return
    from services.agent.stop_policy import synthesize_wrap_up
    from services.handlers.chat.stream_finalize import stop_message

    synthesis = await synthesize_wrap_up(
        model_gateway=_get_model_gateway(prepared),
        messages=prepared.messages,
        content_blocks=blocks,
        reason=stop_message(prepared.budget.stop_reason),
    )
    if synthesis:
        totals.text = synthesis
        block = {"type": "text", "text": synthesis}
        blocks.append(block)
        await sink.on_block(block)
    elif not totals.text:
        raise RuntimeError("CHAT_BUDGET_EXHAUSTED_WITHOUT_OUTPUT")
    else:
        warning = (
            f"\n\n> ⚠️ 已达到执行上限（{stop_message(prepared.budget.stop_reason)}），"
            "以上为部分结果。"
        )
        totals.text += warning
        block = {"type": "text", "text": warning}
        blocks.append(block)
        await sink.on_block(block)


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


async def _check_cancelled(
    event: asyncio.Event,
    request: ChatExecutionRequest | None,
    messages: list[dict[str, Any]],
    blocks: list[dict[str, Any]],
    totals: StreamTotals,
    location: str,
) -> None:
    if not event.is_set():
        return
    if request is not None and request.on_cancel is not None:
        await request.on_cancel(
            messages,
            blocks,
            totals.text,
            totals.thinking,
            location,
        )
    raise asyncio.CancelledError


async def _sink_tool_calls(
    sink: ExecutionSink,
    calls: list[dict[str, Any]],
    turn: int,
) -> None:
    callback = getattr(sink, "on_tool_calls", None)
    if callback is not None:
        await callback(calls, turn)


def _inject_steer_messages(
    messages: list[dict[str, Any]],
    steer_messages: list[str],
) -> None:
    for message in steer_messages:
        if message:
            messages.append({"role": "user", "content": message})


def _last_tool_output(blocks: list[dict[str, Any]]) -> str:
    for block in reversed(blocks):
        text = block.get("output") or block.get("text")
        if block.get("type") in {"tool_result", "tool_step"} and text:
            return str(text)[:2000]
    return ""
