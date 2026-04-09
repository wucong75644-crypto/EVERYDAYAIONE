"""
视频生成处理器

处理视频生成任务（异步模式）。
"""

import uuid
from typing import Any, Dict, List, Optional

from loguru import logger


from schemas.message import (
    ContentPart,
    GenerationType,
    Message,
    VideoPart,
)
from config.smart_model_config import get_image_to_video_model
from services.adapters.factory import DEFAULT_VIDEO_MODEL_ID
from services.handlers.base import BaseHandler, TaskMetadata


class VideoHandler(BaseHandler):
    """
    视频生成处理器

    特点：
    - 异步任务模式
    - 支持文生视频和图生视频
    - 通过 WebSocket 推送完成状态
    """

    def __init__(self, db):
        super().__init__(db)

    @property
    def handler_type(self) -> GenerationType:
        return GenerationType.VIDEO

    async def start(
        self,
        message_id: str,
        conversation_id: str,
        user_id: str,
        content: List[ContentPart],
        params: Dict[str, Any],
        metadata: TaskMetadata,
    ) -> str:
        """
        启动视频生成任务

        1. 提取 prompt 和参考图
        2. 计算并预扣积分
        3. 调用视频生成 API
        4. 保存任务到数据库（含 transaction_id）
        """
        # 1. 提取参数
        prompt = self._extract_text_content(content)
        image_url = self._extract_image_url(content)
        model_id = params.get("model") or DEFAULT_VIDEO_MODEL_ID
        aspect_ratio = params.get("aspect_ratio") or "landscape"
        n_frames = params.get("n_frames") or "25"
        remove_watermark = params.get("remove_watermark", True)

        # 2. 根据是否有图片选择模型
        if image_url and "image-to-video" not in model_id:
            model_id = get_image_to_video_model()

        # 3. 计算积分（使用统一入口）
        from config.kie_models import calculate_video_cost

        # 帧数 → 时长映射（25帧=10秒，大于125帧=15秒）
        n_frames_int = int(n_frames) if isinstance(n_frames, str) else n_frames
        duration_seconds = 10 if n_frames_int <= 125 else 15

        cost_result = calculate_video_cost(
            model_name=model_id,
            duration_seconds=duration_seconds,
        )
        credits_to_lock = cost_result["user_credits"]

        # 4. 检查并预扣积分
        self._check_balance(user_id, credits_to_lock)

        # 生成临时 task_id 用于积分锁定
        temp_task_id = str(uuid.uuid4())
        transaction_id = self._lock_credits(
            task_id=temp_task_id,
            user_id=user_id,
            amount=credits_to_lock,
            reason=f"Video: {model_id}",
            org_id=self.org_id,
        )

        # 5. 调用视频生成 API
        from services.adapters.factory import create_video_adapter

        adapter = create_video_adapter(model_id)

        generate_kwargs = {
            "prompt": prompt,
            "image_urls": [image_url] if image_url else None,
            "aspect_ratio": aspect_ratio,
            "remove_watermark": remove_watermark,
            "callback_url": self._build_callback_url(adapter.provider.value),
            "wait_for_result": False,
        }

        try:
            result = await adapter.generate(**generate_kwargs)
            external_task_id = result.task_id
        except Exception as e:
            # API 调用失败，退回积分
            try:
                self._refund_credits(transaction_id)
            except Exception as refund_err:
                logger.critical(f"Video refund also failed | tx={transaction_id} | error={refund_err}")

            # Smart mode 重试
            retry_result = await self._attempt_video_sync_retry(
                prompt=prompt, model_id=model_id, error=str(e),
                params=params, generate_kwargs=generate_kwargs,
                user_id=user_id, credits_to_lock=credits_to_lock,
                message_id=message_id, conversation_id=conversation_id,
                metadata=metadata,
            )
            if retry_result:
                return retry_result
            raise e
        finally:
            await adapter.close()

        # 6. 保存任务到数据库（使用 external_task_id 作为主 ID）
        try:
            self._save_task(
                task_id=external_task_id,
                message_id=message_id,
                conversation_id=conversation_id,
                user_id=user_id,
                model_id=model_id,
                prompt=prompt,
                params=params,
                metadata=metadata,
                credits_locked=credits_to_lock,
                transaction_id=transaction_id,
            )
        except Exception as save_err:
            # API 任务已创建，不退积分（回调可能还会回来）
            logger.critical(
                f"Video _save_task failed | external_task_id={external_task_id} | "
                f"transaction_id={transaction_id} | error={save_err}"
            )

        logger.info(
            f"Video task started | external_task_id={external_task_id} | "
            f"client_task_id={metadata.client_task_id} | message_id={message_id} | "
            f"model={model_id} | credits_locked={credits_to_lock}"
        )

        # 返回 client_task_id（与前端订阅匹配）
        return metadata.client_task_id or external_task_id

    async def _attempt_video_sync_retry(
        self,
        prompt: str,
        model_id: str,
        error: str,
        params: Dict[str, Any],
        generate_kwargs: Dict[str, Any],
        user_id: str,
        credits_to_lock: int,
        message_id: str,
        conversation_id: str,
        metadata: TaskMetadata,
    ) -> Optional[str]:
        """Smart mode 同步重试：API 调用失败时尝试替代模型"""
        if not params.get("_is_smart_mode"):
            return None

        from services.intent_router import RetryContext

        ctx = RetryContext(
            is_smart_mode=True,
            original_content=prompt,
            generation_type=GenerationType.VIDEO,
        )
        ctx.add_failure(model_id, error)

        while ctx.can_retry:
            decision = await self._route_retry(ctx)
            if not decision or not decision.recommended_model:
                break

            new_model = decision.recommended_model
            attempt = len(ctx.failed_attempts)
            logger.info(
                f"Video sync retry | attempt={attempt} | "
                f"{model_id} → {new_model}"
            )

            from services.adapters.factory import create_video_adapter

            new_adapter = create_video_adapter(new_model)
            new_tx = self._lock_credits(
                task_id=str(uuid.uuid4()),
                user_id=user_id,
                amount=credits_to_lock,
                reason=f"Video retry: {new_model}",
                org_id=self.org_id,
            )

            try:
                new_kwargs = {**generate_kwargs}
                new_kwargs["callback_url"] = self._build_callback_url(
                    new_adapter.provider.value
                )
                result = await new_adapter.generate(**new_kwargs)
            except Exception as retry_err:
                try:
                    self._refund_credits(new_tx)
                except Exception as refund_err:
                    logger.critical(f"Video retry refund failed | tx={new_tx} | error={refund_err}")
                ctx.add_failure(new_model, str(retry_err))
                logger.warning(
                    f"Video sync retry failed | model={new_model} | "
                    f"error={retry_err}"
                )
                continue
            finally:
                await new_adapter.close()

            # API 成功 → 持久化（在 try 外，DB 错误不触发重试/退积分）
            try:
                retry_params = {
                    **params,
                    "_retried": True,
                    "_retry_from_model": model_id,
                }
                self._save_task(
                    task_id=result.task_id,
                    message_id=message_id,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    model_id=new_model,
                    prompt=prompt,
                    params=retry_params,
                    metadata=metadata,
                    credits_locked=credits_to_lock,
                    transaction_id=new_tx,
                )
            except Exception as save_err:
                logger.critical(
                    f"Video retry _save_task failed | "
                    f"external_task_id={result.task_id} | error={save_err}"
                )

            logger.info(
                f"Video retry succeeded | task_id={result.task_id} | "
                f"model={new_model}"
            )
            return metadata.client_task_id or result.task_id

        return None

    # ========================================
    # 基类抽象方法实现
    # ========================================

    def _convert_content_parts_to_dicts(self, result: List[ContentPart]) -> List[Dict[str, Any]]:
        """转换 VideoPart 为字典"""
        content_dicts = []
        for part in result:
            if isinstance(part, VideoPart):
                content_dicts.append({
                    "type": "video",
                    "url": part.url,
                    "duration": part.duration,
                    "thumbnail": part.thumbnail,
                })
            elif isinstance(part, dict):
                content_dicts.append(part)
        return content_dicts

    async def _handle_credits_on_complete(
        self,
        task: Dict[str, Any],
        credits_consumed: int,
    ) -> int:
        """Video 完成时确认积分扣除"""
        transaction_id = task.get("credit_transaction_id")
        if transaction_id:
            self._confirm_deduct(transaction_id)
        # 使用预扣的积分作为实际消耗
        return task.get("credits_locked", credits_consumed)

    async def _handle_credits_on_error(self, task: Dict[str, Any]) -> None:
        """Video 错误时退回积分"""
        transaction_id = task.get("credit_transaction_id")
        if transaction_id:
            self._refund_credits(transaction_id)

    # ========================================
    # 回调方法（调用基类通用流程）
    # ========================================

    async def on_complete(
        self,
        task_id: str,
        result: List[ContentPart],
        credits_consumed: int = 0,
    ) -> Message:
        """完成回调（调用基类通用流程）

        注意：task_id 是 external_task_id（KIE 返回的），需要查询 client_task_id 用于 WebSocket 推送
        """
        return await self._handle_complete_common(task_id, result, credits_consumed)

    async def on_error(
        self,
        task_id: str,
        error_code: str,
        error_message: str,
    ) -> Message:
        """错误回调（调用基类通用流程）

        注意：task_id 是 external_task_id（KIE 返回的），需要查询 client_task_id 用于 WebSocket 推送
        """
        return await self._handle_error_common(task_id, error_code, error_message)

    def _save_task(
        self,
        task_id: str,
        message_id: str,
        conversation_id: str,
        user_id: str,
        model_id: str,
        prompt: str,
        params: Dict[str, Any],
        metadata: TaskMetadata,
        credits_locked: int = 0,
        transaction_id: Optional[str] = None,
    ) -> None:
        """保存任务到数据库"""
        # 1. 序列化业务参数
        request_params = {
            "prompt": prompt,
            "model": model_id,
            **self._serialize_params(params),
        }

        # 2. 构建标准 task_data（使用基类方法）
        task_data = self._build_task_data(
            task_id=task_id,
            message_id=message_id,
            conversation_id=conversation_id,
            user_id=user_id,
            task_type="video",
            status="pending",
            model_id=model_id,
            request_params=request_params,
            metadata=metadata,
            credits_locked=credits_locked,
            transaction_id=transaction_id,
        )

        # 3. 保存到数据库
        self.db.table("tasks").insert(task_data).execute()
