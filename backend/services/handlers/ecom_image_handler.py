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

from schemas.message import GenerationType, MessageStatus
from schemas.websocket_builders import build_message_done
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
        """后台异步：调 ImageAgent.ecom_plan → 写消息 → WS 推送。"""
        from services.agent.image.image_agent import ImageAgent

        try:
            # 提取用户文本和图片
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

            # 根据结果构建消息内容
            if result.status == "plan" and result.metadata.get("ecom_plan"):
                # 方案成功 → 写 ecom_plan 消息
                plan_data = result.metadata["ecom_plan"]
                content_dicts = [{
                    "type": "ecom_plan",
                    "product_insight": plan_data.get("product_insight", ""),
                    "visual_strategy": plan_data.get("visual_strategy", ""),
                    "images": plan_data.get("images", []),
                    "cost_estimate": plan_data.get("cost_estimate"),
                }]
            else:
                # 信息不足或失败 → 写文本消息
                content_dicts = [{"type": "text", "text": result.summary}]

            # 写消息到 DB + 推送 WS
            _, msg_data = self._upsert_assistant_message(
                message_id=message_id,
                conversation_id=conversation_id,
                content_dicts=content_dicts,
                status=MessageStatus.COMPLETED,
                credits_cost=0,
                client_task_id=task_id,
                generation_type="image_ecom",
                model_id="qwen-vl-max",
            )

            # 推送 message_done（前端替换占位消息为方案卡片或文本）
            from services.websocket_manager import ws_manager
            ws_msg = build_message_done(
                task_id=task_id,
                conversation_id=conversation_id,
                message=msg_data,
                credits_consumed=0,
            )
            await ws_manager.send_to_task_or_user(task_id, user_id, ws_msg)

            logger.info(
                f"EcomImageHandler Phase1 done | message_id={message_id} "
                f"| status={result.status} | user={user_id}"
            )

        except Exception as e:
            logger.opt(exception=True).error(
                f"EcomImageHandler Phase1 failed | message_id={message_id} | error={e}"
            )
            # 写错误消息
            try:
                _, msg_data = self._upsert_assistant_message(
                    message_id=message_id,
                    conversation_id=conversation_id,
                    content_dicts=[{"type": "text", "text": f"方案生成失败：{e}"}],
                    status=MessageStatus.COMPLETED,
                    credits_cost=0,
                    client_task_id=task_id,
                    generation_type="image_ecom",
                    model_id="qwen-vl-max",
                    is_error=True,
                )
                from services.websocket_manager import ws_manager
                ws_msg = build_message_done(
                    task_id=task_id, conversation_id=conversation_id,
                    message=msg_data, credits_consumed=0,
                )
                await ws_manager.send_to_task_or_user(task_id, user_id, ws_msg)
            except Exception as push_err:
                logger.error(f"Phase1 error push failed: {push_err}")

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
