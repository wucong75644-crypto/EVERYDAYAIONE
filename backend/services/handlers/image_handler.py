"""
图片生成处理器

处理图片生成任务（异步模式）。
"""

import uuid
from typing import Any, Dict, List, Optional

from loguru import logger
from supabase import Client

from schemas.message import (
    ContentPart,
    GenerationType,
    ImagePart,
    Message,
    MessageStatus,
)
from schemas.websocket import build_task_status_message
from services.handlers.base import BaseHandler
from services.websocket_manager import ws_manager


class ImageHandler(BaseHandler):
    """
    图片生成处理器

    特点：
    - 异步任务模式
    - 支持文生图和图生图
    - 通过 WebSocket 推送完成状态
    """

    def __init__(self, db: Client):
        super().__init__(db)

    @property
    def handler_type(self) -> GenerationType:
        return GenerationType.IMAGE

    async def start(
        self,
        message_id: str,
        conversation_id: str,
        user_id: str,
        content: List[ContentPart],
        params: Dict[str, Any],
    ) -> str:
        """
        启动图片生成任务

        1. 提取 prompt 和参考图
        2. 计算并预扣积分
        3. 调用图片生成 API
        4. 保存任务到数据库（含 transaction_id）
        """
        # 1. 提取参数
        prompt = self._extract_text_content(content)
        image_url = self._extract_image_url(content)
        model_id = params.get("model") or "google/nano-banana"
        aspect_ratio = params.get("aspect_ratio") or "1:1"
        output_format = params.get("output_format") or "png"
        resolution = params.get("resolution") or None  # 确保空字符串也转为 None

        # 2. 计算积分（使用统一入口）
        from config.kie_models import calculate_image_cost

        cost_result = calculate_image_cost(
            model_name=model_id,
            image_count=1,
            resolution=resolution,
        )
        credits_to_lock = cost_result["user_credits"]

        # 3. 检查并预扣积分
        await self._check_balance(user_id, credits_to_lock)

        # 生成临时 task_id 用于积分锁定
        temp_task_id = str(uuid.uuid4())
        transaction_id = await self._lock_credits(
            task_id=temp_task_id,
            user_id=user_id,
            amount=credits_to_lock,
            reason=f"Image: {model_id}",
        )

        # 4. 调用图片生成 API
        from services.adapters.factory import create_image_adapter

        adapter = create_image_adapter(model_id)

        # 只有支持 resolution 的模型才传递该参数
        generate_kwargs = {
            "prompt": prompt,
            "image_urls": [image_url] if image_url else None,
            "size": aspect_ratio,
            "output_format": output_format,
            "callback_url": self._build_callback_url(adapter.provider.value),
            "wait_for_result": False,  # 异步模式
        }
        if resolution and adapter.supports_resolution:
            generate_kwargs["resolution"] = resolution

        try:
            result = await adapter.generate(**generate_kwargs)
            task_id = result.task_id
        except Exception as e:
            # API 调用失败，退回积分
            await self._refund_credits(transaction_id)
            raise e
        finally:
            await adapter.close()

        # 5. 保存任务到数据库（含 transaction_id）
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

        # 6. 更新消息状态
        await self._update_message(message_id, status=MessageStatus.PENDING)

        logger.info(
            f"Image task started | task_id={task_id} | "
            f"message_id={message_id} | model={model_id} | credits_locked={credits_to_lock}"
        )

        return task_id

    async def on_complete(
        self,
        task_id: str,
        result: List[ContentPart],
        credits_consumed: int = 0,
    ) -> Message:
        """完成回调（通常由轮询或 Webhook 触发）"""
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
        image_urls = []
        for part in result:
            if isinstance(part, ImagePart):
                content_dicts.append({
                    "type": "image",
                    "url": part.url,
                    "width": part.width,
                    "height": part.height,
                })
                image_urls.append(part.url)
            elif isinstance(part, dict):
                content_dicts.append(part)
                if part.get("type") == "image":
                    image_urls.append(part.get("url"))

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
            media_type="image",
            urls=image_urls,
            credits_consumed=actual_credits,
            created_message=message,
        )
        await ws_manager.send_to_user(user_id, status_msg)

        # 5. 更新任务状态
        await self._complete_task(task_id)

        logger.info(
            f"Image completed | task_id={task_id} | "
            f"message_id={message_id} | images={len(image_urls)} | credits={actual_credits}"
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
            media_type="image",
            error_message=error_message,
        )
        await ws_manager.send_to_user(user_id, status_msg)

        # 4. 更新任务状态
        await self._fail_task(task_id, error_message)

        logger.error(
            f"Image failed | task_id={task_id} | "
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
            "type": "image",
            "status": "pending",
            "model_id": model_id,
            "placeholder_message_id": message_id,
            "request_params": request_params,
            "credits_locked": credits_locked,
            "credit_transaction_id": transaction_id,
        }).execute()
