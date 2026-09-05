"""旧 Web Chat 流入口的执行协调器。"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from services.handlers.chat.stream_lifecycle import (
    cleanup_stream_resources,
    handle_stream_error,
    persist_stream_completion,
)
from services.handlers.chat.execution_engine import (
    ChatExecutionRequest,
    execute_chat,
)
from services.handlers.chat.execution_sink import WebSocketExecutionSink


@dataclass(frozen=True)
class LegacyStreamRequest:
    task_id: str
    message_id: str
    conversation_id: str
    user_id: str
    content: list[Any]
    model_id: str
    model_request_id: str | None = None
    thinking_effort: str | None = None
    thinking_mode: str | None = None
    permission_mode: str = "auto"
    needs_google_search: bool = False
    params: dict[str, Any] | None = None
    retry_context: Any = None
    context_anchor: Any = None


@dataclass
class _RunResult:
    permission_mode: str
    text_content: str = ""
    accumulated_text: str = ""
    usage: dict[str, Any] = field(
        default_factory=lambda: {"prompt_tokens": 0, "completion_tokens": 0}
    )
    completion_args: dict[str, Any] | None = None


async def run_legacy_chat_stream(
    *,
    handler: Any,
    request: LegacyStreamRequest,
    websocket: Any,
) -> None:
    """运行旧 Web 流协议；模型执行与持久化错误保持独立边界。"""
    started_at = time.monotonic()
    result = _RunResult(permission_mode=request.permission_mode)
    try:
        await _execute_stream(
            handler=handler,
            request=request,
            websocket=websocket,
            result=result,
        )
    except Exception as error:
        await handle_stream_error(
            handler=handler,
            error=error,
            started_at=started_at,
            task_id=request.task_id,
            message_id=request.message_id,
            conversation_id=request.conversation_id,
            user_id=request.user_id,
            content=request.content,
            model_id=request.model_id,
            thinking_effort=request.thinking_effort,
            thinking_mode=request.thinking_mode,
            permission_mode=result.permission_mode,
            params=request.params,
            retry_context=request.retry_context,
            context_anchor=request.context_anchor,
        )
    finally:
        await cleanup_stream_resources(
            adapter=handler._adapter,
            task_id=request.task_id,
            websocket=websocket,
        )
    await persist_stream_completion(
        handler=handler,
        completion_args=result.completion_args,
        started_at=started_at,
        task_id=request.task_id,
        user_id=request.user_id,
        conversation_id=request.conversation_id,
        text_content=result.text_content,
        accumulated_text=result.accumulated_text,
        model_id=request.model_id,
        usage=result.usage,
        retry_context=request.retry_context,
    )


async def _execute_stream(
    *,
    handler: Any,
    request: LegacyStreamRequest,
    websocket: Any,
    result: _RunResult,
) -> None:
    cancellation_event = asyncio.Event()
    sink = WebSocketExecutionSink(
        task_id=request.task_id,
        conversation_id=request.conversation_id,
        message_id=request.message_id,
        user_id=request.user_id,
        model_id=request.model_id,
        websocket=websocket,
        save_content=handler._save_accumulated_content,
        save_blocks=handler._save_accumulated_blocks,
    )
    cancellation_monitor = asyncio.create_task(
        _watch_legacy_cancellation(websocket, request.task_id, cancellation_event)
    )

    async def on_cancel(
        messages: list[dict[str, Any]],
        blocks: list[dict[str, Any]],
        partial_text: str,
        partial_thinking: str,
        location: str,
    ) -> None:
        if getattr(on_cancel, "called", False):
            return
        on_cancel.called = True
        await handler._handle_user_cancel(
            request.task_id,
            request.message_id,
            request.conversation_id,
            messages,
            blocks,
            location,
            partial_text=partial_text,
            partial_thinking=partial_thinking,
        )

    try:
        execution = await execute_chat(
            handler=handler,
            request=ChatExecutionRequest(
                content=request.content,
                user_id=request.user_id,
                conversation_id=request.conversation_id,
                task_id=request.task_id,
                message_id=request.message_id,
                model_id=request.model_id,
                model_request_id=request.model_request_id,
                context_anchor=request.context_anchor,
                params=request.params or {},
                permission_mode=request.permission_mode,
                needs_google_search=request.needs_google_search,
                thinking_effort=request.thinking_effort,
                thinking_mode=request.thinking_mode,
                steer_reader=lambda: websocket.check_steer(request.task_id),
                on_cancel=on_cancel,
            ),
            cancellation_event=cancellation_event,
            sink=sink,
        )
    finally:
        cancellation_monitor.cancel()
        await asyncio.gather(cancellation_monitor, return_exceptions=True)

    result.permission_mode = request.permission_mode
    result.text_content = handler._extract_text_content(request.content)
    result.accumulated_text = sink.text
    result.usage = execution.usage
    result.completion_args = {
        "task_id": request.task_id,
        "result": execution.parts,
        "credits_consumed": execution.credits_cost,
        "tool_digest": execution.tool_digest,
    }


async def _watch_legacy_cancellation(
    websocket: Any,
    task_id: str,
    cancellation_event: asyncio.Event,
) -> None:
    while not cancellation_event.is_set():
        if websocket.is_cancelled(task_id):
            cancellation_event.set()
            return
        await asyncio.sleep(0.05)
