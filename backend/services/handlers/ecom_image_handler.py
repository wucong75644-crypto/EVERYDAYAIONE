"""
电商图模式处理器

专用 Handler，不经过 ChatHandler/LLM 工具循环。
直接遍历 image_task_meta，每张图调 KIE adapter 生成。
和 ImageHandler 同级，复用相同的积分/回调/WebSocket 模式。

设计文档：docs/document/TECH_电商图片Agent.md
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Dict, List, Optional

from loguru import logger

from schemas.message import GenerationType
from services.handlers.image_handler import ImageHandler

# 复用 ImageHandler 已有的默认模型
from config.smart_model_config import DEFAULT_IMAGE_MODEL


class EcomImageHandler(ImageHandler):
    """电商图模式处理器 — 继承 ImageHandler，覆盖 start 入口。

    与 ImageHandler 的区别：
    - 从 params.image_task_meta 获取每张图的描述（而非单一 prompt）
    - 每张图的 prompt 来自 enhance API 的结构化拆分
    - 其余逻辑（积分/KIE调用/回调/WebSocket）完全复用 ImageHandler
    """

    @property
    def handler_type(self) -> GenerationType:
        return GenerationType.IMAGE_ECOM

    async def start(
        self,
        message_id: str,
        conversation_id: str,
        user_id: str,
        content: List[Any],
        params: Dict[str, Any],
        metadata: Any,
    ) -> str:
        """启动电商图批量生成。

        核心差异：从 image_task_meta 获取每张图描述，
        构建 _batch_prompts 后委托给 ImageHandler.start。
        """
        # 从 params 提取 image_task_meta
        image_task_meta = params.get("image_task_meta")
        if not image_task_meta or not isinstance(image_task_meta, list):
            # 没有结构化拆分 → 当作普通单图处理
            logger.warning("EcomImageHandler: no image_task_meta, fallback to single image")
            return await super().start(
                message_id, conversation_id, user_id, content, params, metadata,
            )

        # 构建 _batch_prompts（ImageHandler 已支持的批量模式）
        batch_prompts = []
        for item in image_task_meta:
            desc = item.get("description", "")
            ratio = item.get("aspect_ratio", "1:1")
            if desc:
                batch_prompts.append({
                    "prompt": desc,
                    "aspect_ratio": ratio,
                })

        if not batch_prompts:
            logger.warning("EcomImageHandler: empty batch_prompts after parsing")
            return await super().start(
                message_id, conversation_id, user_id, content, params, metadata,
            )

        # 注入 _batch_prompts 到 params（ImageHandler.start 会读取）
        params["_batch_prompts"] = batch_prompts

        # 确保有模型（默认用文生图模型）
        if not params.get("model"):
            image_urls = self._extract_image_urls(content)
            if image_urls:
                params["model"] = DEFAULT_IMAGE_MODEL.replace(
                    "text-to-image", "image-to-image"
                ) if "text-to-image" in DEFAULT_IMAGE_MODEL else DEFAULT_IMAGE_MODEL
            else:
                params["model"] = DEFAULT_IMAGE_MODEL

        logger.info(
            f"EcomImageHandler start | message_id={message_id} | "
            f"images={len(batch_prompts)} | model={params.get('model')}"
        )

        # 委托给 ImageHandler.start（它已支持 _batch_prompts 批量模式）
        return await super().start(
            message_id, conversation_id, user_id, content, params, metadata,
        )
