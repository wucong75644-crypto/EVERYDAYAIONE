"""
电商图模式处理器（v2）

两阶段执行：
  Phase 1（无 image_task_meta）：调 ImageAgent.ecom_plan() 生成方案 → 返回方案卡片消息
  Phase 2（有 image_task_meta）：用方案中的 prompt 批量生图（现有流程）

设计文档：docs/document/TECH_电商图片Agent_v2.md
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

from loguru import logger

from schemas.message import GenerationType
from services.handlers.image_handler import ImageHandler


# gpt-image-2 图生图模型 ID
_I2I_MODEL = "gpt-image-2-image-to-image"


class EcomImageHandler(ImageHandler):
    """电商图模式处理器 v2。

    Phase 1：没有 image_task_meta → 调 ImageAgent.ecom_plan 生成方案 → 方案卡片消息
    Phase 2：有 image_task_meta → 构建 batch_prompts → 批量生图
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
        """电商图入口：根据是否有 image_task_meta 分发到 Phase 1 或 Phase 2。"""
        image_task_meta = params.get("image_task_meta")

        if image_task_meta and isinstance(image_task_meta, list):
            # Phase 2：有方案 → 批量生图
            return await self._phase2_generate(
                message_id, conversation_id, user_id, content, params, metadata,
            )

        # Phase 1：没有方案 → 调 ImageAgent 策划方案
        task_id = metadata.client_task_id if metadata else message_id

        # 先保存 task 到 DB（前端 WS 订阅需要 task 存在）
        task_data = self._build_task_data(
            task_id=task_id, message_id=message_id,
            conversation_id=conversation_id, user_id=user_id,
            task_type="image", status="running",  # DB check 约束只允许 chat/image/video
            model_id="qwen-vl-max",
            request_params={"phase": "plan"},
            metadata=metadata,
        )
        self._insert_task_with_turn_binding(task_data, metadata)

        # 后台异步执行（start 立刻返回，不阻塞 HTTP 响应）
        asyncio.create_task(self._phase1_plan(
            message_id, conversation_id, user_id, content, params, task_id,
        ))
        return task_id

    # ----------------------------------------------------------
    # Phase 1：方案策划
    # ----------------------------------------------------------

    async def _phase1_plan(
        self,
        message_id: str,
        conversation_id: str,
        user_id: str,
        content: List[Any],
        params: Dict[str, Any],
        task_id: str,
    ) -> None:
        """后台异步：调 ImageAgent.ecom_plan → 通过标准 on_complete 走已有消息完成流程。"""
        from services.agent.image.image_agent import ImageAgent

        try:
            user_text = self._extract_text_content(content)
            image_urls = self._extract_image_urls(content)
            platform = params.get("platform", "taobao")

            agent = ImageAgent(
                db=self.db,
                user_id=user_id,
                conversation_id=conversation_id,
            )
            result = await agent.ecom_plan(
                user_text=user_text,
                image_urls=image_urls,
                platform=platform,
            )

            # 构建 content（和 ChatHandler 工具调用返回一样的格式）
            if result.status == "plan" and result.metadata.get("ecom_plan"):
                plan_data = result.metadata["ecom_plan"]
                result_content = [{
                    "type": "ecom_plan",
                    "product_insight": plan_data.get("product_insight", ""),
                    "visual_strategy": plan_data.get("visual_strategy", ""),
                    "images": plan_data.get("images", []),
                    "cost_estimate": plan_data.get("cost_estimate"),
                }]
            else:
                result_content = [{"type": "text", "text": result.summary}]

            # 用标准的 on_complete 流程（和 ImageHandler/ChatHandler 一样）
            # 内部自动：upsert 消息 + WS 推送 message_done + 更新 task 状态
            await self.on_complete(task_id, result_content, credits_consumed=0)

            logger.info(
                f"EcomImageHandler Phase1 done | message_id={message_id} "
                f"| status={result.status} | user={user_id}"
            )

        except Exception as e:
            logger.opt(exception=True).error(
                f"EcomImageHandler Phase1 failed | message_id={message_id} | error={e}"
            )
            try:
                await self.on_error(task_id, "ECOM_PLAN_FAILED", str(e))
            except Exception as err:
                logger.error(f"Phase1 on_error failed: {err}")

    # ----------------------------------------------------------
    # Phase 2：批量生图（现有流程）
    # ----------------------------------------------------------

    async def _phase2_generate(
        self,
        message_id: str,
        conversation_id: str,
        user_id: str,
        content: List[Any],
        params: Dict[str, Any],
        metadata: Any,
    ) -> str:
        """有 image_task_meta → 构建 batch_prompts → 委托给 ImageHandler 批量生图。"""
        image_task_meta = params["image_task_meta"]

        all_image_urls = self._extract_image_urls(content)
        product_urls = params.get("product_image_urls") or all_image_urls
        style_ref_urls = params.get("style_ref_urls") or []

        full_refs = list(product_urls) + list(style_ref_urls)
        primary_ref = [product_urls[0]] if product_urls else []

        batch_prompts = []
        for item in image_task_meta:
            prompt = item.get("prompt") or item.get("description", "")
            if not prompt:
                continue
            image_type = item.get("image_type", "marketing")
            refs = primary_ref if image_type == "white_bg" else full_refs

            batch_prompts.append({
                "prompt": prompt,
                "aspect_ratio": item.get("aspect_ratio", "1:1"),
                "image_urls": refs if refs else None,
            })

        if not batch_prompts:
            logger.warning("EcomImageHandler Phase2: empty batch")
            return await super().start(
                message_id, conversation_id, user_id, content, params, metadata,
            )

        params["_batch_prompts"] = batch_prompts
        params["model"] = _I2I_MODEL

        logger.info(
            f"EcomImageHandler Phase2 start | message_id={message_id} "
            f"| images={len(batch_prompts)} | model={_I2I_MODEL}"
        )

        return await super().start(
            message_id, conversation_id, user_id, content, params, metadata,
        )
