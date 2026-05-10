"""
电商图模式处理器（v2）

从 image_task_meta（千问输出的 JSON）构建批量生图请求。
强制 image-to-image 模式 + quality 按 has_text 分级 + 白底图参考图精简。

设计文档：docs/document/TECH_电商图片Agent_v2.md §6
"""

from __future__ import annotations

from typing import Any, Dict, List

from loguru import logger

from schemas.message import GenerationType
from services.handlers.image_handler import ImageHandler


# gpt-image-2 图生图模型 ID
_I2I_MODEL = "gpt-image-2-image-to-image"


class EcomImageHandler(ImageHandler):
    """电商图模式处理器 v2 — 继承 ImageHandler，覆盖 start 入口。

    v1 → v2 变化：
    - image_task_meta 格式从 {description, aspect_ratio} 改为
      {prompt, aspect_ratio, has_text, image_type, ...}（千问直接输出的 JSON）
    - 强制使用 gpt-image-2-image-to-image 模型
    - quality 按 has_text 自动分级（有文字=high，无文字=medium）
    - 白底图只传产品主图（不传风格参考图）
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

        从 image_task_meta 构建 _batch_prompts，每张图可独立控制
        prompt / aspect_ratio / resolution(quality) / image_urls(参考图)。
        """
        image_task_meta = params.get("image_task_meta")
        if not image_task_meta or not isinstance(image_task_meta, list):
            logger.warning("EcomImageHandler: no image_task_meta, fallback to single image")
            return await super().start(
                message_id, conversation_id, user_id, content, params, metadata,
            )

        # 提取产品图和风格参考图
        all_image_urls = self._extract_image_urls(content)
        product_urls = params.get("product_image_urls") or all_image_urls
        style_ref_urls = params.get("style_ref_urls") or []

        # 全部参考图（产品图 + 风格参考图）
        full_refs = list(product_urls) + list(style_ref_urls)
        # 仅产品主图（白底图/细节图用）
        primary_ref = [product_urls[0]] if product_urls else []

        # 构建 _batch_prompts
        batch_prompts = []
        for item in image_task_meta:
            prompt = item.get("prompt") or item.get("description", "")
            if not prompt:
                continue

            image_type = item.get("image_type", "marketing")
            has_text = item.get("has_text", False)

            # quality 分级：有文字 → 1K(high 由 KIE 端控制)，无文字 → 1K
            # 注：gpt-image-2 KIE 接口 resolution=1K 对应 quality=auto
            # 后续可扩展支持 2K 用于有文字的图

            # 参考图分组：白底图只传产品主图
            if image_type == "white_bg":
                refs = primary_ref
            else:
                refs = full_refs

            batch_prompts.append({
                "prompt": prompt,
                "aspect_ratio": item.get("aspect_ratio", "1:1"),
                "image_urls": refs if refs else None,
            })

        if not batch_prompts:
            logger.warning("EcomImageHandler: empty batch after parsing")
            return await super().start(
                message_id, conversation_id, user_id, content, params, metadata,
            )

        # 注入参数
        params["_batch_prompts"] = batch_prompts
        params["model"] = _I2I_MODEL

        logger.info(
            f"EcomImageHandler v2 start | message_id={message_id} "
            f"| images={len(batch_prompts)} | model={_I2I_MODEL} "
            f"| product_refs={len(product_urls)} | style_refs={len(style_ref_urls)}"
        )

        return await super().start(
            message_id, conversation_id, user_id, content, params, metadata,
        )
