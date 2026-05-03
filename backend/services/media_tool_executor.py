"""
媒体生成工具 Mixin

图片/视频生成 + 积分 lock/confirm 原子模式。
从 ToolExecutor 拆分出来，通过 Mixin 继承组合。

依赖宿主类提供：self.db, self.user_id, self.org_id
积分方法通过 CreditMixin 继承获得：self._lock_credits, self._confirm_deduct, self._refund_credits
"""

from typing import Any, Dict
from uuid import uuid4

from loguru import logger


class MediaToolMixin:
    """图片/视频生成工具 Mixin"""

    async def _generate_image(self, args: Dict[str, Any]) -> "AgentResult":
        """生成图片：锁积分 → adapter 同步等待 → confirm/refund"""
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

        # 3. 调用 adapter 同步等待
        adapter = create_image_adapter(model_id)
        try:
            result = await adapter.generate(
                prompt=prompt,
                image_urls=image_urls if image_urls else None,
                size=aspect_ratio,
                wait_for_result=True,
                max_wait_time=90.0,
                poll_interval=2.0,
            )

            if result.image_urls:
                self._confirm_deduct(tx_id)
                urls = "\n".join(result.image_urls)
                return AgentResult(
                    summary=f"图片已生成：\n{urls}",
                    status="success",
                )
            else:
                self._refund_credits(tx_id)
                return AgentResult(
                    summary=f"图片生成失败：{result.fail_msg or '未知错误'}",
                    status="error",
                    error_message=result.fail_msg or "Unknown error",
                    metadata={"retryable": True},
                )
        except Exception as e:
            self._refund_credits(tx_id)
            logger.error(f"Image generation error | error={e}")
            return AgentResult(
                summary=f"图片生成失败：{e}",
                status="error",
                error_message=str(e),
                metadata={"retryable": False},
            )
        finally:
            await adapter.close()

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
