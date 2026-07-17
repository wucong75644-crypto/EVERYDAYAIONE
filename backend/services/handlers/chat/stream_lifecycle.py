"""Chat 流执行的错误分类、资源清理与持久化边界。"""

from __future__ import annotations

import time
from typing import Any

from loguru import logger


async def handle_stream_error(
    *,
    handler: Any,
    error: Exception,
    started_at: float,
    task_id: str,
    message_id: str,
    conversation_id: str,
    user_id: str,
    content: list[Any],
    model_id: str,
    thinking_effort: str | None,
    thinking_mode: str | None,
    permission_mode: str,
    params: dict[str, Any] | None,
    retry_context: Any,
) -> None:
    """区分 Provider 可重试错误和业务错误，不处理持久化阶段异常。"""
    from core.error_classifier import classify_error

    logger.error(
        f"Chat stream error | task_id={task_id} | "
        f"model={model_id} | error={error}"
    )
    classified = classify_error(error)
    if classified.should_record_breaker:
        handler._record_breaker_result(model_id, success=False, error=error)
    if classified.is_retryable:
        await handler._handle_stream_failure(
            error=error,
            task_id=task_id,
            message_id=message_id,
            conversation_id=conversation_id,
            user_id=user_id,
            content=content,
            model_id=model_id,
            thinking_effort=thinking_effort,
            thinking_mode=thinking_mode,
            permission_mode=permission_mode,
            _params=params,
            _retry_context=retry_context,
            elapsed_ms=int((time.monotonic() - started_at) * 1000),
        )
        return
    logger.warning(
        f"Non-retryable error, skipping model retry | task_id={task_id} | "
        f"category={classified.category.value} | "
        f"error_code={classified.error_code}"
    )
    await handler.on_error(
        task_id=task_id,
        error_code=classified.error_code,
        error_message=str(error),
    )


async def cleanup_stream_resources(
    *,
    adapter: Any,
    task_id: str,
    websocket: Any,
) -> None:
    """关闭 Provider、监听器和请求级工具结果资源。"""
    if adapter:
        await adapter.close()
    websocket.unregister_steer_listener(task_id)
    websocket.unregister_cancel_listener(task_id)
    from services.agent.tool_result_envelope import (
        clear_persisted,
        clear_staging_dir,
    )

    clear_persisted()
    clear_staging_dir()


async def persist_stream_completion(
    *,
    handler: Any,
    completion_args: dict[str, Any] | None,
    started_at: float,
    task_id: str,
    user_id: str,
    conversation_id: str,
    text_content: str,
    accumulated_text: str,
    model_id: str,
    usage: dict[str, Any],
    retry_context: Any,
) -> None:
    """在模型执行成功后提交旧链路终态；失败不得触发 Provider 重试。"""
    if completion_args is None:
        return
    try:
        await handler.on_complete(**completion_args)
        handler._record_breaker_result(model_id, success=True)
        handler._dispatch_post_tasks(
            user_id=user_id,
            conversation_id=conversation_id,
            text_content=text_content,
            accumulated_text=accumulated_text,
            model_id=model_id,
            final_usage=usage,
            elapsed_ms=int((time.monotonic() - started_at) * 1000),
            retry_context=retry_context,
        )
    except Exception as persist_error:
        logger.critical(
            f"Persist phase failed after LLM success | "
            f"task_id={task_id} | error={persist_error}"
        )
        try:
            await handler.on_error(
                task_id=task_id,
                error_code="INTERNAL_ERROR",
                error_message=f"保存结果失败: {persist_error}",
            )
        except Exception as error_error:
            logger.critical(
                f"on_error also failed | task_id={task_id} | "
                f"error={error_error}"
            )
