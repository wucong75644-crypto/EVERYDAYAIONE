"""
视频生成处理器

处理视频生成任务（异步模式）。
"""

import uuid
from typing import Any, Dict, List, Optional

from loguru import logger
from supabase import Client

from schemas.message import (
    ContentPart,
    GenerationType,
    Message,
    MessageStatus,
    VideoPart,
)
from schemas.websocket import build_task_status_message
from services.handlers.base import BaseHandler
from services.websocket_manager import ws_manager


class VideoHandler(BaseHandler):
    """
    视频生成处理器

    特点：
    - 异步任务模式
    - 支持文生视频和图生视频
    - 通过 WebSocket 推送完成状态
    """

    def __init__(self, db: Client):
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
        model_id = params.get("model") or "sora-2-text-to-video"
        aspect_ratio = params.get("aspect_ratio") or "landscape"
        n_frames = params.get("n_frames") or "25"
        remove_watermark = params.get("remove_watermark", True)

        # 2. 根据是否有图片选择模型
        if image_url and "image-to-video" not in model_id:
            model_id = "sora-2-image-to-video"

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
        await self._check_balance(user_id, credits_to_lock)

        # 生成临时 task_id 用于积分锁定
        temp_task_id = str(uuid.uuid4())
        transaction_id = await self._lock_credits(
            task_id=temp_task_id,
            user_id=user_id,
            amount=credits_to_lock,
            reason=f"Video: {model_id}",
        )

        # 5. 调用视频生成 API
        from services.adapters.factory import create_video_adapter

        adapter = create_video_adapter(model_id)

        try:
            result = await adapter.generate(
                prompt=prompt,
                image_urls=[image_url] if image_url else None,
                aspect_ratio=aspect_ratio,
                remove_watermark=remove_watermark,
                callback_url=self._build_callback_url(adapter.provider.value),
                wait_for_result=False,  # 异步模式
            )
            task_id = result.task_id
        except Exception as e:
            # API 调用失败，退回积分
            await self._refund_credits(transaction_id)
            raise e
        finally:
            await adapter.close()

        # 6. 保存任务到数据库（含 transaction_id）
        await self._save_task(
            task_id=task_id,
            message_id=message_id,
            conversation_id=conversation_id,
            user_id=user_id,
            model_id=model_id,
            prompt=prompt,
            params=params,
            credits_locked=credits_to_lock,
            transaction_id=transaction_id,
        )

        # 7. 更新消息状态
        await self._update_message(message_id, status=MessageStatus.PENDING)

        logger.info(
            f"Video task started | task_id={task_id} | "
            f"message_id={message_id} | model={model_id} | credits_locked={credits_to_lock}"
        )

        return task_id

    async def on_complete(
        self,
        task_id: str,
        result: List[ContentPart],
        credits_consumed: int = 0,
    ) -> Message:
        """完成回调"""
        task = await self._get_task(task_id)
        if not task:
            logger.error(f"Task not found | task_id={task_id}")
            raise Exception("任务不存在")

        message_id = task["placeholder_message_id"]
        conversation_id = task["conversation_id"]
        user_id = task["user_id"]
        transaction_id = task.get("credit_transaction_id")

        # 1. 确认积分扣除
        if transaction_id:
            await self._confirm_deduct(transaction_id)

        # 使用预扣的积分作为实际消耗
        actual_credits = task.get("credits_locked", credits_consumed)

        # 2. 转换 ContentPart 为字典
        content_dicts = []
        video_urls = []
        for part in result:
            if isinstance(part, VideoPart):
                content_dicts.append({
                    "type": "video",
                    "url": part.url,
                    "duration": part.duration,
                    "thumbnail": part.thumbnail,
                })
                video_urls.append(part.url)
            elif isinstance(part, dict):
                content_dicts.append(part)
                if part.get("type") == "video":
                    video_urls.append(part.get("url"))

        # 3. 更新消息
        message = await self._update_message(
            message_id=message_id,
            content=content_dicts,
            status=MessageStatus.COMPLETED,
            credits_cost=actual_credits,
        )

        # 4. 推送完成消息
        status_msg = build_task_status_message(
            task_id=task_id,
            conversation_id=conversation_id,
            status="completed",
            media_type="video",
            urls=video_urls,
            credits_consumed=actual_credits,
            created_message=message,
        )
        await ws_manager.send_to_user(user_id, status_msg)

        # 5. 更新任务状态
        await self._complete_task(task_id)

        logger.info(
            f"Video completed | task_id={task_id} | "
            f"message_id={message_id} | videos={len(video_urls)} | credits={actual_credits}"
        )

        return message

    async def on_error(
        self,
        task_id: str,
        error_code: str,
        error_message: str,
    ) -> Message:
        """错误回调"""
        task = await self._get_task(task_id)
        if not task:
            logger.error(f"Task not found | task_id={task_id}")
            raise Exception("任务不存在")

        message_id = task["placeholder_message_id"]
        conversation_id = task["conversation_id"]
        user_id = task["user_id"]
        transaction_id = task.get("credit_transaction_id")

        # 1. 退回积分
        if transaction_id:
            await self._refund_credits(transaction_id)

        # 2. 更新消息为失败状态
        message = await self._update_message(
            message_id=message_id,
            content=[{"type": "text", "text": error_message}],
            status=MessageStatus.FAILED,
            error={"code": error_code, "message": error_message},
        )

        # 3. 推送失败消息
        status_msg = build_task_status_message(
            task_id=task_id,
            conversation_id=conversation_id,
            status="failed",
            media_type="video",
            error_message=error_message,
        )
        await ws_manager.send_to_user(user_id, status_msg)

        # 4. 更新任务状态
        await self._fail_task(task_id, error_message)

        logger.error(
            f"Video failed | task_id={task_id} | "
            f"error_code={error_code} | error={error_message}"
        )

        return message

    async def _save_task(
        self,
        task_id: str,
        message_id: str,
        conversation_id: str,
        user_id: str,
        model_id: str,
        prompt: str,
        params: Dict[str, Any],
        credits_locked: int = 0,
        transaction_id: Optional[str] = None,
    ) -> None:
        """保存任务到数据库"""
        # 构建请求参数
        request_params = {
            "prompt": prompt,
            "model": model_id,
            **{k: v for k, v in params.items() if v is not None},
        }

        self.db.table("tasks").insert({
            "external_task_id": task_id,
            "conversation_id": conversation_id,
            "user_id": user_id,
            "type": "video",
            "status": "pending",
            "model_id": model_id,
            "placeholder_message_id": message_id,
            "request_params": request_params,
            "credits_locked": credits_locked,
            "credit_transaction_id": transaction_id,
        }).execute()
