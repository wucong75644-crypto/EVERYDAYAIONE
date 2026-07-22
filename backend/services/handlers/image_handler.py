"""
图片生成处理器

处理图片生成任务（异步模式）。
统一路径：单图（num_images=1）当作 batch_size=1 的批次处理。
"""

import asyncio
from typing import Any, Dict, List, Optional

from loguru import logger


from schemas.message import (
    ContentPart,
    GenerationType,
    ImagePart,
    Message,
)
from services.handlers.base import BaseHandler, TaskMetadata
from services.handlers.image_request_settings import (
    build_image_generate_kwargs,
    resolve_image_generation_settings,
    resolve_batch_item_kwargs,
    resolve_prepared_batch,
)


class ImageHandler(BaseHandler):
    """
    图片生成处理器

    特点：
    - 异步任务模式，统一批次路径（1~4 张）
    - 支持文生图和图生图
    - 通过 WebSocket 推送完成状态
    """

    def __init__(self, db):
        super().__init__(db)

    @property
    def handler_type(self) -> GenerationType:
        return GenerationType.IMAGE

    def preflight(
        self,
        user_id: str,
        content: List[ContentPart],
        params: Dict[str, Any],
    ) -> None:
        """在消息占位符变更前校验本次图片请求的积分。"""
        settings = resolve_image_generation_settings(
            params=params,
            has_image_urls=bool(self._extract_image_urls(content)),
        )
        self._check_balance(user_id, settings["total_credits"])

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
        settings = resolve_image_generation_settings(
            params=params,
            has_image_urls=bool(image_urls),
        )
        model_id = settings["model_id"]
        aspect_ratio = settings["aspect_ratio"]
        output_format = params.get("output_format") or "png"

        # regenerate_single：仅生成 1 张，使用指定 image_index
        is_regenerate_single = params.get("operation") == "regenerate_single"
        # Agent Loop 批量生图：每张图有独立提示词
        batch_prompts = params.get("_batch_prompts")
        num_images = settings["num_images"]
        if is_regenerate_single:
            single_image_index = int(params.get("image_index", 0))

        # 2. 计算总积分并校验余额
        total_credits = settings["total_credits"]
        per_image_credits = total_credits // num_images

        self._check_balance(user_id, total_credits)

        logger.info(
            f"Image batch start | client_task_id={metadata.client_task_id} | "
            f"message_id={message_id} | model={model_id} | "
            f"num_images={num_images} | total_credits={total_credits}"
        )

        # 3. 统一批次逻辑
        batch_id, prepared_task_ids = resolve_prepared_batch(metadata, num_images)
        tasks_created: List[str] = []

        from services.adapters.factory import create_image_adapter

        adapter = create_image_adapter(model_id)

        # 构建生成参数（所有图片共用）
        generate_kwargs = build_image_generate_kwargs(
            prompt=prompt,
            image_urls=image_urls,
            settings=settings,
            output_format=output_format,
            callback_url=self._build_callback_url(adapter.provider.value),
            supports_resolution=adapter.supports_resolution,
        )

        try:
            for i in range(num_images):
                if i > 0:
                    await asyncio.sleep(0.3)  # 300ms 间隔，尊重 KIE 频率限制

                # regenerate_single 使用指定的 image_index，否则使用循环 index
                actual_index = single_image_index if is_regenerate_single else i

                # Agent Loop / Ecom 批量生图：每张图可覆盖 prompt/aspect_ratio/image_urls/resolution
                task_kwargs = generate_kwargs
                task_prompt = prompt
                if batch_prompts and i < len(batch_prompts):
                    task_kwargs, task_prompt = resolve_batch_item_kwargs(
                        generate_kwargs, prompt, aspect_ratio, batch_prompts[i],
                    )

                ext_task_id = await self._create_single_task(
                    adapter=adapter,
                    index=actual_index,
                    batch_id=batch_id,
                    generate_kwargs=task_kwargs,
                    user_id=user_id,
                    model_id=model_id,
                    per_image_credits=per_image_credits,
                    params=params,
                    prompt=task_prompt,
                    prepared_task_id=prepared_task_ids[i],
                )
                if ext_task_id:
                    tasks_created.append(ext_task_id)
        finally:
            await adapter.close()

        if not tasks_created:
            from core.exceptions import AppException
            raise AppException(
                code="IMAGE_GENERATION_FAILED",
                message="图片生成服务暂时不可用，请稍后重试",
                status_code=502,
            )

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
        user_id: str,
        model_id: str,
        per_image_credits: int,
        params: Dict[str, Any],
        prompt: str,
        prepared_task_id: str,
    ) -> Optional[str]:
        """使用已原子准备的本地 task 锁积分并提交供应商。"""
        from services.handlers.image_prepared_submission import submit_prepared_image_task
        return await submit_prepared_image_task(
            handler=self, local_task_id=prepared_task_id, adapter=adapter,
            index=index, batch_id=batch_id, generate_kwargs=generate_kwargs,
            user_id=user_id, model_id=model_id,
            per_image_credits=per_image_credits, params=params, prompt=prompt,
        )

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
        image_count = sum(1 for p in result if isinstance(p, ImagePart))
        if image_count != 1:
            logger.warning(
                f"IMAGE_COUNT_MISMATCH | task_id={task_id} | "
                f"expected=1 | actual={image_count} | credits_consumed={credits_consumed}"
            )
        return await self._handle_complete_common(task_id, result, credits_consumed)

    async def on_error(
        self,
        task_id: str,
        error_code: str,
        error_message: str,
    ) -> Message:
        """错误回调（调用基类通用流程）"""
        return await self._handle_error_common(task_id, error_code, error_message)
