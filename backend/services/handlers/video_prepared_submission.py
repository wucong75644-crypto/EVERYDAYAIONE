"""视频参数解析与已准备 task 的供应商提交。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from loguru import logger

from config.smart_model_config import get_image_to_video_model
from schemas.message import GenerationType
from services.adapters.factory import DEFAULT_VIDEO_MODEL_ID
from services.generation_lifecycle import GenerationLifecycle


@dataclass(frozen=True)
class VideoSubmissionSettings:
    prompt: str
    image_url: str | None
    model_id: str
    aspect_ratio: str
    remove_watermark: bool
    credits: int


def resolve_video_submission_settings(
    handler: Any,
    content: list[Any],
    params: dict[str, Any],
) -> VideoSubmissionSettings:
    """解析 VideoHandler 与路由原子准备共用的实际模型和计费参数。"""
    prompt = handler._extract_text_content(content)
    image_url = handler._extract_image_url(content)
    model_id = params.get("model") or DEFAULT_VIDEO_MODEL_ID
    if image_url and "image-to-video" not in model_id:
        model_id = get_image_to_video_model()
    n_frames = params.get("n_frames") or "25"
    frame_count = int(n_frames) if isinstance(n_frames, str) else n_frames
    from config.kie_models import calculate_video_cost

    cost = calculate_video_cost(
        model_name=model_id,
        duration_seconds=10 if frame_count <= 125 else 15,
    )["user_credits"]
    return VideoSubmissionSettings(
        prompt=prompt, image_url=image_url, model_id=model_id,
        aspect_ratio=params.get("aspect_ratio") or "landscape",
        remove_watermark=params.get("remove_watermark", True), credits=cost,
    )


async def submit_prepared_video_task(
    *,
    handler: Any,
    local_task_id: str,
    user_id: str,
    params: dict[str, Any],
    settings: VideoSubmissionSettings,
    client_task_id: str | None,
) -> str:
    """使用稳定本地 task 锁积分、提交视频并 attach 最终供应商结果。"""
    transaction_id = handler._lock_credits(
        task_id=local_task_id, user_id=user_id, amount=settings.credits,
        reason=f"Video: {settings.model_id}", org_id=handler.org_id,
    )
    from services.adapters.factory import create_video_adapter

    adapter = create_video_adapter(settings.model_id)
    kwargs = _generate_kwargs(handler, adapter, settings)
    try:
        result = await adapter.generate(**kwargs)
    except Exception as error:
        if _is_submission_unknown(error):
            _mark_submission_unknown(handler, local_task_id, error)
            raise
        _refund_safely(handler, transaction_id, local_task_id)
        retry_result = await _retry_prepared_video_task(
            handler=handler, local_task_id=local_task_id, user_id=user_id,
            params=params, settings=settings, generate_kwargs=kwargs,
            initial_error=str(error),
        )
        if retry_result:
            return client_task_id or retry_result
        GenerationLifecycle(handler.db).fail_prepared_task(
            task_id=local_task_id, terminal_reason="provider_rejected",
            error_message=str(error), org_id=handler.org_id, user_id=user_id,
        )
        raise
    finally:
        await adapter.close()
    _attach_success(
        handler=handler, local_task_id=local_task_id,
        external_task_id=result.task_id, transaction_id=transaction_id,
        model_id=settings.model_id, prompt=settings.prompt, params=params,
        provider=adapter.provider.value, user_id=user_id,
    )
    return client_task_id or result.task_id


async def _retry_prepared_video_task(
    *, handler: Any, local_task_id: str, user_id: str,
    params: dict[str, Any], settings: VideoSubmissionSettings,
    generate_kwargs: dict[str, Any], initial_error: str,
) -> str | None:
    if not params.get("_is_smart_mode"):
        return None
    from services.intent_router import RetryContext
    context = RetryContext(
        is_smart_mode=True, original_content=settings.prompt,
        generation_type=GenerationType.VIDEO,
    )
    context.add_failure(settings.model_id, initial_error)
    while context.can_retry:
        decision = await handler._route_retry(context)
        if not decision or not decision.recommended_model:
            break
        retry_model = decision.recommended_model
        from services.adapters.factory import create_video_adapter
        adapter = create_video_adapter(retry_model)
        transaction_id = handler._lock_credits(
            task_id=local_task_id, user_id=user_id, amount=settings.credits,
            reason=f"Video retry: {retry_model}", org_id=handler.org_id,
        )
        try:
            kwargs = dict(generate_kwargs)
            kwargs["callback_url"] = handler._build_callback_url(adapter.provider.value)
            result = await adapter.generate(**kwargs)
        except Exception as error:
            if _is_submission_unknown(error):
                _mark_submission_unknown(handler, local_task_id, error)
                raise
            _refund_safely(handler, transaction_id, local_task_id)
            context.add_failure(retry_model, str(error))
            continue
        finally:
            await adapter.close()
        retry_params = {
            **params, "_retried": True,
            "_retry_from_model": settings.model_id,
        }
        _attach_success(
            handler=handler, local_task_id=local_task_id,
            external_task_id=result.task_id, transaction_id=transaction_id,
            model_id=retry_model, prompt=settings.prompt, params=retry_params,
            provider=adapter.provider.value, user_id=user_id,
        )
        return result.task_id
    return None


def _generate_kwargs(
    handler: Any, adapter: Any, settings: VideoSubmissionSettings,
) -> dict[str, Any]:
    return {
        "prompt": settings.prompt,
        "image_urls": [settings.image_url] if settings.image_url else None,
        "aspect_ratio": settings.aspect_ratio,
        "remove_watermark": settings.remove_watermark,
        "callback_url": handler._build_callback_url(adapter.provider.value),
        "wait_for_result": False,
    }


def _attach_success(
    *, handler: Any, local_task_id: str, external_task_id: str,
    transaction_id: str, model_id: str, prompt: str,
    params: dict[str, Any], provider: str, user_id: str,
) -> None:
    request_params = {
        "prompt": prompt, "model": model_id, **handler._serialize_params(params),
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
    except Exception as error:
        logger.critical(
            f"Video refund failed | task_id={task_id} | "
            f"transaction_id={transaction_id} | error={error}"
        )


def _is_submission_unknown(error: Exception) -> bool:
    current: BaseException | None = error
    while current is not None:
        if isinstance(current, (TimeoutError, asyncio.TimeoutError)):
            return True
        current = current.__cause__ or current.__context__
    return False


def _mark_submission_unknown(handler: Any, task_id: str, error: Exception) -> None:
    try:
        handler.db.table("tasks").update({
            "terminal_reason": "submission_unknown",
            "error_message": str(error)[:1000],
        }).eq("id", task_id).eq("status", "preparing").execute()
    except Exception as persistence_error:
        logger.critical(
            f"Video submission_unknown persistence failed | "
            f"task_id={task_id} | error={persistence_error}"
        )
