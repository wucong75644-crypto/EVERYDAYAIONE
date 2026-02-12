"""
图片生成处理器

处理图片生成任务（异步模式）。
"""

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from loguru import logger
from supabase import Client

from schemas.message import (
    ContentPart,
    GenerationType,
    ImagePart,
    Message,
    MessageRole,
    MessageStatus,
)
from services.handlers.base import BaseHandler, TaskMetadata


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
        metadata: TaskMetadata,
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

        # 🔍 日志：追踪 client_task_id
        logger.info(
            f"[image_handler.start] Before API call | "
            f"client_task_id={metadata.client_task_id} | "
            f"message_id={message_id} | model={model_id}"
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
            external_task_id = result.task_id  # KIE 返回的外部任务 ID
        except Exception as e:
            # API 调用失败，退回积分
            await self._refund_credits(transaction_id)
            raise e
        finally:
            await adapter.close()

        # 5. 保存任务到数据库（使用 external_task_id 作为主 ID）
        await self._save_task(
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

        logger.info(
            f"Image task started | external_task_id={external_task_id} | "
            f"client_task_id={metadata.client_task_id} | message_id={message_id} | "
            f"model={model_id} | credits_locked={credits_to_lock}"
        )

        # 返回 client_task_id（与前端订阅匹配）
        return metadata.client_task_id or external_task_id

    # ========================================
    # 基类抽象方法实现
    # ========================================

    def _convert_content_parts_to_dicts(self, result: List[ContentPart]) -> List[Dict[str, Any]]:
        """转换 ImagePart 为字典"""
        content_dicts = []
        for part in result:
            if isinstance(part, ImagePart):
                content_dicts.append({
                    "type": "image",
                    "url": part.url,
                    "width": part.width,
                    "height": part.height,
                })
            elif isinstance(part, dict):
                content_dicts.append(part)
        return content_dicts

    async def _handle_credits_on_complete(
        self,
        task: Dict[str, Any],
        credits_consumed: int,
    ) -> int:
        """Image 完成时确认积分扣除"""
        transaction_id = task.get("credit_transaction_id")
        if transaction_id:
            await self._confirm_deduct(transaction_id)
        # 使用预扣的积分作为实际消耗
        return task.get("credits_locked", credits_consumed)

    async def _handle_credits_on_error(self, task: Dict[str, Any]) -> None:
        """Image 错误时退回积分"""
        transaction_id = task.get("credit_transaction_id")
        if transaction_id:
            await self._refund_credits(transaction_id)

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

    async def _save_task(
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
            task_type="image",
            status="pending",
            model_id=model_id,
            request_params=request_params,
            metadata=metadata,
            credits_locked=credits_locked,
            transaction_id=transaction_id,
        )

        # 🔍 日志：保存任务前
        logger.info(
            f"[image_handler._save_task] Saving task | "
            f"external_task_id={task_id} | "
            f"client_task_id={metadata.client_task_id} | "
            f"message_id={message_id}"
        )

        # 3. 保存到数据库
        self.db.table("tasks").insert(task_data).execute()

        # 🔍 日志：保存任务后（验证）
        verify_task = self.db.table("tasks").select("external_task_id, client_task_id").eq("external_task_id", task_id).execute()
        if verify_task.data:
            saved_data = verify_task.data[0]
            logger.info(
                f"[image_handler._save_task] Task saved verified | "
                f"external_task_id={saved_data.get('external_task_id')} | "
                f"client_task_id={saved_data.get('client_task_id')}"
            )
        else:
            logger.error(f"[image_handler._save_task] Failed to verify saved task | task_id={task_id}")
