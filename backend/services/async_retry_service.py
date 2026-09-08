"""
异步任务智能重试服务

当 Webhook/轮询报告任务失败时，检查是否 smart_mode 并尝试用替代模型重新提交。
从 TaskCompletionService 分离，保持职责单一。
"""

import json
import os
from typing import Any, Dict, Optional, Union

from loguru import logger


from schemas.message import GenerationType
from services.adapters.base import (
    ImageGenerateResult,
    VideoGenerateResult,
)

TaskResult = Union[ImageGenerateResult, VideoGenerateResult]

_KIE_IMAGE_FETCH_FAILURE_CODE = "400"
_KIE_IMAGE_FETCH_FALLBACK_ATTEMPTED_KEY = "_kie_image_fetch_fallback_attempted"
_KIE_IMAGE_FETCH_FALLBACK_FROM_TASK_ID_KEY = "_kie_image_fetch_fallback_from_task_id"
_KIE_IMAGE_FETCH_FALLBACK_ENABLED_ENV = "KIE_IMAGE_FETCH_FALLBACK_ENABLED"


class AsyncRetryService:
    """异步任务 smart_mode 重试"""

    def __init__(self, db):
        self.db = db

    @staticmethod
    def _request_params(task: Dict[str, Any]) -> Dict[str, Any]:
        request_params = task.get("request_params") or {}
        if isinstance(request_params, str):
            return json.loads(request_params)
        return request_params

    @staticmethod
    def _is_kie_image_fetch_fallback_enabled() -> bool:
        return os.getenv(_KIE_IMAGE_FETCH_FALLBACK_ENABLED_ENV, "false").lower() in {
            "1", "true", "yes", "on",
        }

    def has_kie_image_fetch_fallback_attempted(self, task: Dict[str, Any]) -> bool:
        """判断该本地任务是否已经使用临时空间链接重提过一次。"""
        return bool(
            self._request_params(task).get(_KIE_IMAGE_FETCH_FALLBACK_ATTEMPTED_KEY)
        )

    def should_attempt_kie_image_fetch_fallback(
        self,
        task: Dict[str, Any],
        result: TaskResult,
    ) -> bool:
        """仅对 KIE 图片获取失败（failCode=400）触发一次专用重试。"""
        if (
            not self._is_kie_image_fetch_fallback_enabled()
            or task.get("type") != "image"
            or str(result.fail_code or "") != _KIE_IMAGE_FETCH_FAILURE_CODE
            or self.has_kie_image_fetch_fallback_attempted(task)
        ):
            return False

        from services.adapters.kie.configs import IMAGE_MODEL_CONFIGS

        return task.get("model_id") in IMAGE_MODEL_CONFIGS

    async def attempt_kie_image_fetch_fallback(
        self,
        task: Dict[str, Any],
    ) -> bool:
        """用海外旁路临时空间链接，按原参数重提一次同模型 KIE 图片任务。"""
        from services.adapters.kie.shadow_upload_store import (
            get_overseas_shadow_upload_staged_urls,
        )

        original_task_id = task["external_task_id"]
        request_params = self._request_params(task)
        staged_image_urls = await get_overseas_shadow_upload_staged_urls(
            original_task_id
        )
        if not staged_image_urls:
            logger.warning(
                "KIE_IMAGE_FETCH_FALLBACK_SKIPPED | task_id={} | "
                "reason=overseas_shadow_urls_unavailable",
                original_task_id,
            )
            return False

        if not all(isinstance(url, str) and url for url in staged_image_urls):
            logger.warning(
                "KIE_IMAGE_FETCH_FALLBACK_SKIPPED | task_id={} | "
                "reason=overseas_shadow_urls_invalid | source_count={}",
                original_task_id,
                len(staged_image_urls),
            )
            return False

        fallback_params = {
            **request_params,
            "image_urls": staged_image_urls,
            _KIE_IMAGE_FETCH_FALLBACK_ATTEMPTED_KEY: True,
            _KIE_IMAGE_FETCH_FALLBACK_FROM_TASK_ID_KEY: original_task_id,
        }
        try:
            new_task_id = await self._resubmit(
                task=task,
                new_model=task["model_id"],
                request_params=fallback_params,
                retry_count=self._request_params(task).get("_retry_count", 0),
                retry_reason="KieImageFetchFallback",
            )
        except Exception as exc:
            logger.warning(
                "KIE_IMAGE_FETCH_FALLBACK_FAILURE | task_id={} | "
                "stage=resubmit | error_type={}",
                original_task_id,
                type(exc).__name__,
            )
            return False

        if not new_task_id:
            return False

        logger.info(
            "KIE_IMAGE_FETCH_FALLBACK_SUBMITTED | original_task_id={} | "
            "retry_task_id={} | model={} | source_count={}",
            original_task_id,
            new_task_id,
            task["model_id"],
            len(staged_image_urls),
        )
        return True

    async def attempt_retry(
        self,
        task: Dict[str, Any],
        result: TaskResult,
    ) -> bool:
        """
        尝试 smart_mode 异步重试

        Returns:
            True = 重试已提交，False = 不重试
        """
        request_params = self._request_params(task)

        if not request_params.get("_is_smart_mode"):
            return False

        retry_count = request_params.get("_retry_count", 0)
        if retry_count >= 2:
            return False

        task_type = task["type"]
        if task_type not in ("image", "video"):
            return False

        model_id = task.get("model_id", "")
        error_msg = result.fail_msg or "unknown error"

        # 调用千问重路由
        new_model = await self._get_retry_model(
            task, request_params, model_id, error_msg, task_type,
        )
        if not new_model:
            return False

        logger.info(
            f"Async smart retry | task_id={task['external_task_id']} | "
            f"type={task_type} | {model_id} → {new_model} | "
            f"retry_count={retry_count + 1}"
        )

        # 重新提交
        try:
            new_ext_id = await self._resubmit(
                task, new_model, request_params, retry_count,
            )
        except Exception as e:
            logger.warning(
                f"Async retry resubmit failed | "
                f"task_id={task['external_task_id']} | error={e}"
            )
            return False

        if not new_ext_id:
            return False

        logger.info(
            f"Async retry submitted | old={task['external_task_id']} | "
            f"new={new_ext_id} | model={new_model}"
        )
        return True

    async def _get_retry_model(
        self,
        task: Dict[str, Any],
        request_params: Dict[str, Any],
        model_id: str,
        error_msg: str,
        task_type: str,
    ) -> Optional[str]:
        """调用千问重路由获取替代模型"""
        from services.intent_router import IntentRouter, RetryContext

        gen_type = (
            GenerationType.IMAGE if task_type == "image"
            else GenerationType.VIDEO
        )
        prompt = request_params.get("prompt", "")

        # 构建已失败模型列表
        failed_models = [model_id]
        prev_failures = request_params.get("_failed_models", [])
        for prev_model in prev_failures:
            if prev_model not in failed_models:
                failed_models.append(prev_model)

        router = IntentRouter()
        try:
            decision = await router.route_retry(
                original_content=prompt,
                generation_type=gen_type,
                failed_models=failed_models,
                error_message=error_msg,
            )
        except Exception as e:
            logger.warning(
                f"Async retry routing failed | "
                f"task_id={task['external_task_id']} | error={e}"
            )
            return None
        finally:
            await router.close()

        if not decision or not decision.recommended_model:
            return None

        return decision.recommended_model

    async def _resubmit(
        self,
        task: Dict[str, Any],
        new_model: str,
        request_params: Dict[str, Any],
        retry_count: int,
        retry_reason: str = "Retry",
    ) -> Optional[str]:
        """用新模型重新提交生成任务，更新 task 记录"""
        task_type = task["type"]
        user_id = task["user_id"]

        # 1. 创建适配器
        if task_type == "image":
            from services.adapters.factory import create_image_adapter
            adapter = create_image_adapter(new_model)
        else:
            from services.adapters.factory import create_video_adapter
            adapter = create_video_adapter(new_model)

        # 2. 构建生成参数
        prompt = request_params.get("prompt", "")
        from services.handlers.base import BaseHandler
        callback_url = BaseHandler._build_callback_url(
            None, adapter.provider.value
        )

        generate_kwargs: Dict[str, Any] = {
            "prompt": prompt,
            "wait_for_result": False,
        }
        if callback_url:
            generate_kwargs["callback_url"] = callback_url

        if task_type == "image":
            generate_kwargs["size"] = request_params.get("aspect_ratio", "1:1")
            generate_kwargs["output_format"] = request_params.get(
                "output_format", "png"
            )
            resolution = request_params.get("resolution")
            if resolution:
                generate_kwargs["resolution"] = resolution
            image_urls = request_params.get("image_urls")
            if image_urls:
                generate_kwargs["image_urls"] = image_urls
        elif task_type == "video":
            generate_kwargs["aspect_ratio"] = request_params.get(
                "aspect_ratio", "16:9"
            )

        # 3. 先结束旧的积分锁定，再为同一条本地任务创建新的 pending 事务。
        # credit_transactions 允许保留历史事务，但同一任务不能同时存在两个 pending 锁定。
        from services.handlers.mixins import CreditMixin

        credit_helper = CreditMixin()
        credit_helper.db = self.db
        old_tx = task.get("credit_transaction_id")
        if old_tx:
            credit_helper._refund_credits(old_tx)

        # 4. 锁定新积分
        old_credits = task.get("credits_locked", 0)
        new_tx = credit_helper._lock_credits(
            task_id=task["id"],
            user_id=user_id,
            amount=old_credits,
            reason=f"{retry_reason}[{task_type}]: {new_model}",
            org_id=task.get("org_id"),
        )

        # 5. 提交 API 请求
        try:
            api_result = await adapter.generate(**generate_kwargs)
            new_ext_id = api_result.task_id
        except Exception:
            credit_helper._refund_credits(new_tx)
            raise
        finally:
            await adapter.close()

        # 6. 更新 task 记录（复用同一条记录，新 ext_id）
        updated_params = {
            **request_params,
            "_retry_count": retry_count + 1,
            "_failed_models": (
                request_params.get("_failed_models", [])
                + [task.get("model_id", "")]
            ),
        }

        self.db.table("tasks").update({
            "external_task_id": new_ext_id,
            "model_id": new_model,
            "status": "pending",
            "request_params": updated_params,
            "credit_transaction_id": new_tx,
            "credits_locked": old_credits,
            "version": 1,
            "error_message": None,
        }).eq("id", task["id"]).execute()

        return new_ext_id
