"""旧 Web Chat 流入口的执行协调器。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from schemas.websocket import build_message_start
from services.handlers.chat.stream_finalize import (
    StreamFinalizationInput,
    finalize_stream_result,
)
from services.handlers.chat.stream_lifecycle import (
    cleanup_stream_resources,
    handle_stream_error,
    persist_stream_completion,
)
from services.handlers.chat.stream_loop import ChatStreamLoop
from services.handlers.chat.stream_session import StreamDelivery
from services.handlers.chat.stream_setup import prepare_chat_stream


@dataclass(frozen=True)
class LegacyStreamRequest:
    task_id: str
    message_id: str
    conversation_id: str
    user_id: str
    content: list[Any]
    model_id: str
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
    delivery = _delivery(request, handler.org_id)
    await websocket.send_to_task_or_user(
        request.task_id,
        request.user_id,
        build_message_start(
            task_id=request.task_id,
            conversation_id=request.conversation_id,
            message_id=request.message_id,
            model=request.model_id,
        ),
    )
    prepared = await prepare_chat_stream(
        handler=handler,
        content=request.content,
        user_id=request.user_id,
        conversation_id=request.conversation_id,
        task_id=request.task_id,
        model_id=request.model_id,
        permission_mode=request.permission_mode,
        needs_google_search=request.needs_google_search,
        params=request.params or {},
        context_anchor=request.context_anchor,
    )
    handler._adapter = prepared.adapter
    result.permission_mode = prepared.permission_mode
    result.text_content = prepared.text_content
    websocket.register_steer_listener(request.task_id)
    websocket.register_cancel_listener(request.task_id)

    loop = ChatStreamLoop(
        handler=handler,
        prepared=prepared,
        delivery=delivery,
        websocket=websocket,
        thinking_effort=request.thinking_effort,
        thinking_mode=request.thinking_mode,
    )
    await loop.run()
    finalized = await finalize_stream_result(
        handler=handler,
        adapter=prepared.adapter,
        budget=prepared.budget,
        delivery=delivery,
        state=StreamFinalizationInput(
            messages=prepared.messages,
            content_blocks=loop.content_blocks,
            accumulated_text=loop.totals.text,
            accumulated_thinking=loop.totals.thinking,
            turn_text=loop.turn_result.text,
            turn_thinking=loop.turn_result.thinking,
            thinking_committed=loop.turn_result.thinking_committed,
            thinking_started_at=loop.turn_result.thinking_started_at,
            usage=loop.totals.usage,
            safety_blocked=loop.runtime_state.guard_blocked,
        ),
        websocket=websocket,
        save_blocks=handler._save_accumulated_blocks,
    )
    result.accumulated_text = finalized.accumulated_text
    result.usage = loop.totals.usage
    result.completion_args = finalized.completion_args
    if finalized.clear_pending_emit_payloads:
        handler._pending_emit_payloads = []


def _delivery(
    request: LegacyStreamRequest,
    org_id: str | None,
) -> StreamDelivery:
    return StreamDelivery(
        task_id=request.task_id,
        conversation_id=request.conversation_id,
        message_id=request.message_id,
        user_id=request.user_id,
        org_id=org_id,
    )
