"""
媒体生成工具 Mixin

图片/视频生成 + 积分 lock/confirm 原子模式。
从 ToolExecutor 拆分出来，通过 Mixin 继承组合。

依赖宿主类提供：self.db, self.user_id, self.org_id
积分方法通过 CreditMixin 继承获得：self._lock_credits, self._confirm_deduct, self._refund_credits
"""

import json
from datetime import datetime, timezone
from typing import Any, Dict
from uuid import uuid4

from loguru import logger


class MediaToolMixin:
    """图片/视频生成工具 Mixin"""

    async def _generate_image(self, args: Dict[str, Any]) -> "AgentResult":
        """生成图片。

        Web Chat 有 assistant message 上下文时，统一复用 ImageHandler 的
        异步任务生命周期。没有消息上下文的旧调用方暂时走兼容实现，避免
        定时任务等非聊天入口被无意改变。
        """
        if getattr(self, "_message_id", None) and getattr(self, "_task_id", None):
            return await self._generate_image_async_task(args)
        return await self._generate_image_sync_compat(args)

    async def _generate_image_async_task(self, args: Dict[str, Any]) -> "AgentResult":
        """将 generate_image 接入 ImageHandler 的异步图片任务链路。"""
        from services.agent.agent_result import AgentResult
        from services.handlers.base import TaskMetadata
        from services.handlers.image_handler import ImageHandler
        from schemas.message import ImagePart, TextPart

        prompt = str(args.get("prompt", "")).strip()
        if not prompt:
            return AgentResult(
                summary="提示词不能为空",
                status="error",
                error_message="Validation: prompt is required",
                metadata={"retryable": True},
            )

        image_urls = [
            str(url).strip()
            for url in (args.get("image_urls") or [])
            if str(url).strip()
        ]
        try:
            num_images = max(1, min(4, int(args.get("num_images", 1))))
        except (TypeError, ValueError):
            num_images = 1

        model_id = args.get("model")
        if not model_id:
            from config.smart_model_config import DEFAULT_IMAGE_MODEL
            model_id = DEFAULT_IMAGE_MODEL
        aspect_ratio = args.get("aspect_ratio") or "1:1"
        resolution = args.get("resolution")
        output_format = args.get("output_format") or "png"
        child_task_id = str(uuid4())

        params = {
            "model": model_id,
            "aspect_ratio": aspect_ratio,
            "resolution": resolution,
            "output_format": output_format,
            "num_images": num_images,
            "_from_generate_image_tool": True,
        }
        content = [TextPart(text=prompt)]
        content.extend(ImagePart(url=url) for url in image_urls)

        placeholder_snapshot = self._prepare_async_image_placeholder(
            message_id=self._message_id,
            model_id=model_id,
            aspect_ratio=aspect_ratio,
            num_images=num_images,
        )

        handler = ImageHandler(self.db)
        handler.org_id = self.org_id
        handler.request_ctx = getattr(self, "request_ctx", None)
        metadata = TaskMetadata(
            client_task_id=child_task_id,
            placeholder_created_at=datetime.now(timezone.utc),
            input_message_id=getattr(self, "_input_message_id", None),
            turn_id=getattr(self, "_turn_id", None),
            execution_mode=getattr(self, "_execution_mode", "serial"),
        )

        try:
            await handler.start(
                message_id=self._message_id,
                conversation_id=self.conversation_id,
                user_id=self.user_id,
                content=content,
                params=params,
                metadata=metadata,
            )
        except Exception as error:
            self._restore_async_image_placeholder(
                self._message_id, placeholder_snapshot,
            )
            logger.error(
                "Async image task start failed | "
                f"message_id={self._message_id} | error={error}"
            )
            return AgentResult(
                summary=f"图片生成失败：{error}",
                status="error",
                error_message=str(error),
                metadata={"retryable": True},
            )

        pending_payloads = [
            {
                "kind": "image",
                "url": None,
                "pending": True,
                "width": 1024,
                "height": 1024,
                "alt": "正在生成图片",
            }
            for _ in range(num_images)
        ]
        return AgentResult(
            summary="图片已开始生成，完成后会自动展示。",
            status="success",
            metadata={
                "async_media": True,
                "media_task_id": child_task_id,
                "media_task_type": "image",
            },
            emit_payloads=pending_payloads,
        )

    def _prepare_async_image_placeholder(
        self,
        *,
        message_id: str,
        model_id: str,
        aspect_ratio: str,
        num_images: int,
    ) -> Dict[str, Any]:
        """保存异步图片任务的 pending 内容，供刷新恢复使用。"""
        try:
            response = (
                self.db.table("messages")
                .select("content, generation_params")
                .eq("id", message_id)
                .maybe_single()
                .execute()
            )
            row = response.data if response and response.data else {}
            raw_content = row.get("content") or []
            if isinstance(raw_content, str):
                raw_content = json.loads(raw_content)
            original_content = list(raw_content) if isinstance(raw_content, list) else []
            original_params = row.get("generation_params") or {}
            if isinstance(original_params, str):
                original_params = json.loads(original_params)
            original_params = dict(original_params) if isinstance(original_params, dict) else {}

            pending_parts = [
                {
                    "type": "image",
                    "url": None,
                    "pending": True,
                    "width": 1024,
                    "height": 1024,
                }
                for _ in range(num_images)
            ]
            content = [
                part for part in original_content
                if not (isinstance(part, dict) and part.get("pending"))
            ] + pending_parts
            generation_params = {
                **original_params,
                "type": "image",
                "model": model_id,
                "aspect_ratio": aspect_ratio,
                "num_images": num_images,
            }
            self.db.table("messages").update({
                "content": content,
                "generation_params": generation_params,
            }).eq("id", message_id).execute()
            return {
                "content": original_content,
                "generation_params": original_params,
            }
        except Exception as error:
            logger.warning(
                f"Failed to save async image placeholder | message_id={message_id} | error={error}"
            )
            return {}

    def _restore_async_image_placeholder(
        self,
        message_id: str,
        snapshot: Dict[str, Any],
    ) -> None:
        if not snapshot:
            return
        try:
            self.db.table("messages").update({
                "content": snapshot.get("content", []),
                "generation_params": snapshot.get("generation_params") or {},
            }).eq("id", message_id).execute()
        except Exception as error:
            logger.warning(
                f"Failed to restore image placeholder | message_id={message_id} | error={error}"
            )

    async def _generate_image_sync_compat(self, args: Dict[str, Any]) -> "AgentResult":
        """非聊天入口兼容实现；聊天入口不再调用此路径。"""
        from config.kie_models import calculate_image_cost
        from core.exceptions import InsufficientCreditsError
        from services.adapters.factory import create_image_adapter
        from services.agent.agent_result import AgentResult

        prompt = args.get("prompt", "").strip()
        if not prompt:
            return AgentResult(
                summary="提示词不能为空",
                status="error",
                error_message="Validation: prompt is required",
                metadata={"retryable": True},
            )

        aspect_ratio = args.get("aspect_ratio", "1:1")
        image_urls = args.get("image_urls") or []

        # 根据有无参考图片选择模型：图生图 vs 文生图
        if image_urls:
            model_id = "gpt-image-2-image-to-image"
        else:
            from config.smart_model_config import DEFAULT_IMAGE_MODEL
            model_id = DEFAULT_IMAGE_MODEL

        # 1. 计算积分
        try:
            cost_result = calculate_image_cost(model_name=model_id, image_count=1)
            credits_needed = cost_result["user_credits"]
        except Exception as e:
            return AgentResult(
                summary=f"积分计算失败：{e}",
                status="error",
                error_message=str(e),
                metadata={"retryable": False},
            )

        # 2. 锁定积分（原子预扣）
        task_id = str(uuid4())
        try:
            tx_id = self._lock_credits(
                task_id=task_id, user_id=self.user_id,
                amount=credits_needed, reason=f"Image: {prompt[:30]}",
                org_id=self.org_id,
            )
        except InsufficientCreditsError as e:
            return AgentResult(
                summary=str(e),
                status="error",
                error_message=str(e),
                metadata={"retryable": False},
            )

        adapter = create_image_adapter(model_id)
        try:
            return await self._run_image_generation(
                adapter=adapter,
                tx_id=tx_id,
                task_id=task_id,
                prompt=prompt,
                image_urls=image_urls,
                aspect_ratio=aspect_ratio,
                model_id=model_id,
            )
        except Exception as e:
            self._refund_credits(tx_id)
            logger.error(f"Image generation error | error={e}")
            return self._image_failure_result(
                str(e), prompt, aspect_ratio, model_id,
            )
        finally:
            await adapter.close()

    async def _run_image_generation(
        self,
        adapter: Any,
        tx_id: str,
        task_id: str,
        prompt: str,
        image_urls: list[str],
        aspect_ratio: str,
        model_id: str,
    ) -> "AgentResult":
        from services.agent.agent_result import AgentResult
        from services.file_upload import persist_media_urls_to_workspace

        result = await adapter.generate(
            prompt=prompt,
            image_urls=image_urls or None,
            size=aspect_ratio,
            wait_for_result=True,
            max_wait_time=90.0,
            poll_interval=2.0,
        )
        if not result.image_urls:
            self._refund_credits(tx_id)
            return self._image_failure_result(
                result.fail_msg or "未知错误",
                prompt,
                aspect_ratio,
                model_id,
            )
        self._confirm_deduct(tx_id)
        emit_payloads = await persist_media_urls_to_workspace(
            urls=result.image_urls,
            user_id=getattr(self, "workspace_user_id", self.user_id),
            org_id=self.org_id,
            media_type="image",
            meta={
                "prompt": prompt,
                "model": model_id,
                "aspect_ratio": aspect_ratio,
                "task_id": task_id,
                "reference_images": image_urls,
            },
        )
        urls = "\n".join(result.image_urls)
        return AgentResult(
            summary=f"图片已生成：\n{urls}",
            status="success",
            emit_payloads=emit_payloads,
        )

    @staticmethod
    def _image_failure_result(
        error: str,
        prompt: str,
        aspect_ratio: str,
        model_id: str,
    ) -> "AgentResult":
        from services.agent.agent_result import AgentResult

        return AgentResult(
            summary=f"图片生成失败：{error}",
            status="error",
            error_message=error,
            metadata={"retryable": True},
            emit_payloads=[{
                "kind": "image",
                "url": None,
                "failed": True,
                "error": error,
                "retry_context": {
                    "prompt": prompt,
                    "aspect_ratio": aspect_ratio,
                    "model_id": model_id,
                },
            }],
        )

    async def _generate_video(self, args: Dict[str, Any]) -> "AgentResult":
        """生成视频：锁积分 → adapter 同步等待 → confirm/refund"""
        from config.kie_models import calculate_video_cost
        from core.exceptions import InsufficientCreditsError
        from services.adapters.factory import create_video_adapter
        from services.agent.agent_result import AgentResult

        prompt = args.get("prompt", "").strip()
        if not prompt:
            return AgentResult(
                summary="视频描述不能为空",
                status="error",
                error_message="Validation: prompt is required",
                metadata={"retryable": True},
            )

        duration = 10  # 默认10秒

        # 1. 计算积分
        try:
            cost_result = calculate_video_cost(model_name=None, duration_seconds=duration)
            credits_needed = cost_result["user_credits"]
        except Exception as e:
            return AgentResult(
                summary=f"积分计算失败：{e}",
                status="error",
                error_message=str(e),
                metadata={"retryable": False},
            )

        # 2. 锁定积分（原子预扣）
        task_id = str(uuid4())
        try:
            tx_id = self._lock_credits(
                task_id=task_id, user_id=self.user_id,
                amount=credits_needed, reason=f"Video: {prompt[:30]}",
                org_id=self.org_id,
            )
        except InsufficientCreditsError as e:
            return AgentResult(
                summary=str(e),
                status="error",
                error_message=str(e),
                metadata={"retryable": False},
            )

        # 3. 调用 adapter 同步等待
        adapter = create_video_adapter()
        try:
            result = await adapter.generate(
                prompt=prompt,
                duration_seconds=duration,
                wait_for_result=True,
                max_wait_time=300.0,
                poll_interval=5.0,
            )

            if result.video_url:
                self._confirm_deduct(tx_id)
                return AgentResult(
                    summary=f"视频已生成：\n{result.video_url}",
                    status="success",
                )
            else:
                self._refund_credits(tx_id)
                return AgentResult(
                    summary=f"视频生成失败：{result.fail_msg or '未知错误'}",
                    status="error",
                    error_message=result.fail_msg or "Unknown error",
                    metadata={"retryable": True},
                )
        except Exception as e:
            self._refund_credits(tx_id)
            logger.error(f"Video generation error | error={e}")
            return AgentResult(
                summary=f"视频生成失败：{e}",
                status="error",
                error_message=str(e),
                metadata={"retryable": False},
            )
        finally:
            await adapter.close()
