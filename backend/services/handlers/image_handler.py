"""
图片生成处理器

处理图片生成任务（异步模式）。
统一路径：单图（num_images=1）当作 batch_size=1 的批次处理。
"""

import asyncio
import uuid
from typing import Any, Dict, List, Optional

from loguru import logger
from supabase import Client

from schemas.message import (
    ContentPart,
    GenerationType,
    ImagePart,
    Message,
)
from services.adapters.factory import DEFAULT_IMAGE_MODEL_ID
from services.handlers.base import BaseHandler, TaskMetadata


class ImageHandler(BaseHandler):
    """
    图片生成处理器

    特点：
    - 异步任务模式，统一批次路径（1~4 张）
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
        启动图片生成任务（统一批次路径）

        num_images=1 也走批次逻辑（batch_size=1），不做 if/else 分流。

        流程：
        1. 提取 prompt 和参考图
        2. 计算总积分并校验余额
        3. 循环创建 N 个任务（锁积分 → API 调用 → 保存 task）
        4. 返回 client_task_id
        """
        # 1. 提取参数
        prompt = self._extract_text_content(content)
        image_urls = self._extract_image_urls(content)
        model_id = params.get("model") or DEFAULT_IMAGE_MODEL_ID
        aspect_ratio = params.get("aspect_ratio") or "1:1"
        output_format = params.get("output_format") or "png"
        resolution = params.get("resolution") or None
        # 支持分辨率的模型（如 nano-banana-pro）未指定时默认 1K
        from config.kie_models import get_model_config
        model_config = get_model_config(model_id)
        if model_config and model_config.get("supports_resolution") and not resolution:
            resolution = "1K"

        # regenerate_single：仅生成 1 张，使用指定 image_index
        is_regenerate_single = params.get("operation") == "regenerate_single"
        if is_regenerate_single:
            num_images = 1
            single_image_index = int(params.get("image_index", 0))
        else:
            num_images = max(1, min(4, int(params.get("num_images", 1))))

        # 2. 计算总积分并校验余额
        from config.kie_models import calculate_image_cost

        cost_result = calculate_image_cost(
            model_name=model_id,
            image_count=num_images,
            resolution=resolution,
        )
        total_credits = cost_result["user_credits"]
        per_image_credits = total_credits // num_images

        self._check_balance(user_id, total_credits)

        logger.info(
            f"Image batch start | client_task_id={metadata.client_task_id} | "
            f"message_id={message_id} | model={model_id} | "
            f"num_images={num_images} | total_credits={total_credits}"
        )

        # 3. 统一批次逻辑
        batch_id = str(uuid.uuid4())
        tasks_created: List[str] = []

        from services.adapters.factory import create_image_adapter

        adapter = create_image_adapter(model_id)

        # 构建生成参数（所有图片共用）
        generate_kwargs = {
            "prompt": prompt,
            "image_urls": image_urls if image_urls else None,
            "size": aspect_ratio,
            "output_format": output_format,
            "callback_url": self._build_callback_url(adapter.provider.value),
            "wait_for_result": False,
        }
        if resolution and adapter.supports_resolution:
            generate_kwargs["resolution"] = resolution

        try:
            for i in range(num_images):
                if i > 0:
                    await asyncio.sleep(0.3)  # 300ms 间隔，尊重 KIE 频率限制

                # regenerate_single 使用指定的 image_index，否则使用循环 index
                actual_index = single_image_index if is_regenerate_single else i

                ext_task_id = await self._create_single_task(
                    adapter=adapter,
                    index=actual_index,
                    batch_id=batch_id,
                    generate_kwargs=generate_kwargs,
                    message_id=message_id,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    model_id=model_id,
                    per_image_credits=per_image_credits,
                    params=params,
                    prompt=prompt,
                    metadata=metadata,
                )
                if ext_task_id:
                    tasks_created.append(ext_task_id)
        finally:
            await adapter.close()

        if not tasks_created:
            raise Exception("所有图片生成请求均失败")

        logger.info(
            f"Image batch created | batch_id={batch_id} | "
            f"created={len(tasks_created)}/{num_images} | "
            f"client_task_id={metadata.client_task_id}"
        )

        return metadata.client_task_id or tasks_created[0]

    async def _create_single_task(
        self,
        adapter: Any,
        index: int,
        batch_id: str,
        generate_kwargs: Dict[str, Any],
        message_id: str,
        conversation_id: str,
        user_id: str,
        model_id: str,
        per_image_credits: int,
        params: Dict[str, Any],
        prompt: str,
        metadata: TaskMetadata,
    ) -> Optional[str]:
        """
        创建单个图片生成任务（锁积分 → API → 保存 task）

        Returns:
            external_task_id 或 None（失败时）
        """
        temp_task_id = str(uuid.uuid4())
        transaction_id = self._lock_credits(
            task_id=temp_task_id,
            user_id=user_id,
            amount=per_image_credits,
            reason=f"Image[{index}]: {model_id}",
        )

        try:
            result = await adapter.generate(**generate_kwargs)
            external_task_id = result.task_id
        except Exception as e:
            self._refund_credits(transaction_id)
            logger.warning(
                f"Image task[{index}] API failed | "
                f"batch_id={batch_id} | error={e}"
            )

            # Smart mode: 尝试用替代模型重试
            retry_result = await self._attempt_image_sync_retry(
                prompt=prompt, model_id=model_id, error=str(e),
                params=params, generate_kwargs=generate_kwargs,
                user_id=user_id, per_image_credits=per_image_credits,
                index=index, batch_id=batch_id, message_id=message_id,
                conversation_id=conversation_id, metadata=metadata,
            )
            return retry_result  # None if retry also failed

        self._save_task(
            task_id=external_task_id,
            message_id=message_id,
            conversation_id=conversation_id,
            user_id=user_id,
            model_id=model_id,
            prompt=prompt,
            params=params,
            metadata=metadata,
            credits_locked=per_image_credits,
            transaction_id=transaction_id,
            image_index=index,
            batch_id=batch_id,
        )

        return external_task_id

    async def _attempt_image_sync_retry(
        self,
        prompt: str,
        model_id: str,
        error: str,
        params: Dict[str, Any],
        generate_kwargs: Dict[str, Any],
        user_id: str,
        per_image_credits: int,
        index: int,
        batch_id: str,
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
            generation_type=GenerationType.IMAGE,
        )
        ctx.add_failure(model_id, error)

        while ctx.can_retry:
            decision = await self._route_retry(ctx)
            if not decision or not decision.recommended_model:
                break

            new_model = decision.recommended_model
            attempt = len(ctx.failed_attempts)
            logger.info(
                f"Image sync retry | index={index} | attempt={attempt} | "
                f"{model_id} → {new_model}"
            )

            from services.adapters.factory import create_image_adapter

            new_adapter = create_image_adapter(new_model)
            new_tx = self._lock_credits(
                task_id=str(uuid.uuid4()),
                user_id=user_id,
                amount=per_image_credits,
                reason=f"Image[{index}] retry: {new_model}",
            )

            try:
                new_kwargs = {**generate_kwargs}
                new_kwargs["callback_url"] = self._build_callback_url(
                    new_adapter.provider.value
                )
                result = await new_adapter.generate(**new_kwargs)

                self._save_task(
                    task_id=result.task_id,
                    message_id=message_id,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    model_id=new_model,
                    prompt=prompt,
                    params=params,
                    metadata=metadata,
                    credits_locked=per_image_credits,
                    transaction_id=new_tx,
                    image_index=index,
                    batch_id=batch_id,
                )
                return result.task_id
            except Exception as retry_err:
                self._refund_credits(new_tx)
                ctx.add_failure(new_model, str(retry_err))
                logger.warning(
                    f"Image sync retry failed | index={index} | "
                    f"model={new_model} | error={retry_err}"
                )
            finally:
                await new_adapter.close()

        return None

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
            self._confirm_deduct(transaction_id)
        return task.get("credits_locked", credits_consumed)

    async def _handle_credits_on_error(self, task: Dict[str, Any]) -> None:
        """Image 错误时退回积分"""
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
        """完成回调（调用基类通用流程）"""
        return await self._handle_complete_common(task_id, result, credits_consumed)

    async def on_error(
        self,
        task_id: str,
        error_code: str,
        error_message: str,
    ) -> Message:
        """错误回调（调用基类通用流程）"""
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
        image_index: Optional[int] = None,
        batch_id: Optional[str] = None,
    ) -> None:
        """保存任务到数据库"""
        request_params = {
            "prompt": prompt,
            "model": model_id,
            **self._serialize_params(params),
        }

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
            image_index=image_index,
            batch_id=batch_id,
        )

        self.db.table("tasks").insert(task_data).execute()

        logger.info(
            f"Task saved | external_task_id={task_id} | "
            f"client_task_id={metadata.client_task_id} | "
            f"image_index={image_index} | batch_id={batch_id}"
        )
