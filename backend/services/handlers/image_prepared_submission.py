"""已准备图片 task 的积分、供应商提交与最终附加。"""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

from schemas.message import GenerationType
from services.generation_lifecycle import GenerationLifecycle


async def submit_prepared_image_task(
    *,
    handler: Any,
    local_task_id: str,
    adapter: Any,
    index: int,
    batch_id: str,
    generate_kwargs: dict[str, Any],
    user_id: str,
    model_id: str,
    per_image_credits: int,
    params: dict[str, Any],
    prompt: str,
) -> str | None:
    """使用稳定本地 task 提交单张图片，最终成功后原子 attach。"""
    transaction_id = handler._lock_credits(
        task_id=local_task_id, user_id=user_id, amount=per_image_credits,
        reason=f"Image[{index}]: {model_id}", org_id=handler.org_id,
    )
    try:
        result = await adapter.generate(**generate_kwargs)
    except Exception as error:
        if _is_submission_unknown(error):
            _mark_submission_unknown(handler, local_task_id, error)
            logger.critical(
                "Image submission outcome unknown | "
                f"task_id={local_task_id} | batch_id={batch_id} | "
                f"transaction_id={transaction_id} | provider={adapter.provider.value} | "
                f"error={error}"
            )
            raise
        _refund_safely(handler, transaction_id, local_task_id)
        retry_result = await _retry_prepared_image_task(
            handler=handler, local_task_id=local_task_id, prompt=prompt,
            model_id=model_id, error=str(error), params=params,
            generate_kwargs=generate_kwargs, user_id=user_id,
            per_image_credits=per_image_credits, index=index, batch_id=batch_id,
        )
        if retry_result:
            return retry_result
        GenerationLifecycle(handler.db).fail_prepared_task(
            task_id=local_task_id, terminal_reason="provider_rejected",
            error_message=str(error), org_id=handler.org_id, user_id=user_id,
        )
        return None

    _attach_success(
        handler=handler, local_task_id=local_task_id,
        external_task_id=result.task_id, transaction_id=transaction_id,
        model_id=model_id, prompt=prompt, params=params,
        provider=adapter.provider.value, user_id=user_id,
    )
    return result.task_id


async def _retry_prepared_image_task(
    *,
    handler: Any,
    local_task_id: str,
    prompt: str,
    model_id: str,
    error: str,
    params: dict[str, Any],
    generate_kwargs: dict[str, Any],
    user_id: str,
    per_image_credits: int,
    index: int,
    batch_id: str,
) -> str | None:
    if not params.get("_is_smart_mode"):
        return None
    from services.intent_router import RetryContext

    context = RetryContext(
        is_smart_mode=True, original_content=prompt,
        generation_type=GenerationType.IMAGE,
    )
    context.add_failure(model_id, error)
    while context.can_retry:
        decision = await handler._route_retry(context)
        if not decision or not decision.recommended_model:
            break
        retry_model = decision.recommended_model
        from services.adapters.factory import create_image_adapter

        retry_adapter = create_image_adapter(retry_model)
        retry_tx = handler._lock_credits(
            task_id=local_task_id, user_id=user_id, amount=per_image_credits,
            reason=f"Image[{index}] retry: {retry_model}", org_id=handler.org_id,
        )
        try:
            retry_kwargs = dict(generate_kwargs)
            retry_kwargs["callback_url"] = handler._build_callback_url(
                retry_adapter.provider.value
            )
            result = await retry_adapter.generate(**retry_kwargs)
        except Exception as retry_error:
            if _is_submission_unknown(retry_error):
                _mark_submission_unknown(handler, local_task_id, retry_error)
                logger.critical(
                    "Image retry submission outcome unknown | "
                    f"task_id={local_task_id} | batch_id={batch_id} | "
                    f"transaction_id={retry_tx} | model={retry_model} | "
                    f"error={retry_error}"
                )
                raise
            _refund_safely(handler, retry_tx, local_task_id)
            context.add_failure(retry_model, str(retry_error))
            continue
        finally:
            await retry_adapter.close()
        retry_params = {
            **params, "_retried": True, "_retry_from_model": model_id,
        }
        _attach_success(
            handler=handler, local_task_id=local_task_id,
            external_task_id=result.task_id, transaction_id=retry_tx,
            model_id=retry_model, prompt=prompt, params=retry_params,
            provider=retry_adapter.provider.value, user_id=user_id,
        )
        return result.task_id
    return None


def _attach_success(
    *,
    handler: Any,
    local_task_id: str,
    external_task_id: str,
    transaction_id: str,
    model_id: str,
    prompt: str,
    params: dict[str, Any],
    provider: str,
    user_id: str,
) -> None:
    request_params = {
        "prompt": prompt, "model": model_id,
        **handler._serialize_params(params),
    }
    GenerationLifecycle(handler.db).attach_external_task(
        task_id=local_task_id, external_task_id=external_task_id,
        credit_transaction_id=transaction_id, org_id=handler.org_id,
        user_id=user_id, provider=provider, actual_model_id=model_id,
        actual_request_params=request_params,
    )


def _refund_safely(handler: Any, transaction_id: str, task_id: str) -> None:
    try:
        handler._refund_credits(transaction_id)
    except Exception as refund_error:
        logger.critical(
            "Image refund failed | "
            f"task_id={task_id} | transaction_id={transaction_id} | "
            f"error={refund_error}"
        )


def _is_submission_unknown(error: Exception) -> bool:
    current: BaseException | None = error
    while current is not None:
        if isinstance(current, (TimeoutError, asyncio.TimeoutError)):
            return True
        current = current.__cause__ or current.__context__
    return False


def _mark_submission_unknown(handler: Any, task_id: str, error: Exception) -> None:
    """保留 preparing 状态并持久化结果未知原因，供补偿流程处理。"""
    try:
        handler.db.table("tasks").update({
            "terminal_reason": "submission_unknown",
            "error_message": str(error)[:1000],
        }).eq("id", task_id).eq("status", "preparing").execute()
    except Exception as persistence_error:
        logger.critical(
            "Image submission_unknown persistence failed | "
            f"task_id={task_id} | error={persistence_error}"
        )
