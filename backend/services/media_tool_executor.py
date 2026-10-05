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
        """One persistent child acceptance; completion belongs to its worker."""
        from services.handlers.image_handler import ImageHandler
        from services.handlers.chat_image_request import ChatImageNotAcceptedError
        from services.agent.agent_result import AgentResult
        import json
        try:
            accepted = await ImageHandler(self.db).accept_chat_image(self, args)
        except ChatImageNotAcceptedError as error:
            code = str(error)
            if code.startswith("RESOURCE_") or code in {
                "IMAGE_INPUT_UNAVAILABLE", "IMAGE_ORIGINAL_UNAVAILABLE", "IMAGE_REFERENCE_CHANGED",
                "IMAGE_REFERENCE_LOCATOR_INVALID", "IMAGE_CONTENT_INDEX_INVALID", "IMAGE_ASSET_DENIED",
                "IMAGE_SOURCE_MESSAGE_DENIED", "IMAGE_SOURCE_REVISION_DENIED",
                "IMAGE_SOURCE_MESSAGE_CHANGED", "IMAGE_REFERENCE_AMBIGUOUS", "IMAGE_QUOTED_SOURCE_DENIED",
                "IMAGE_QUOTED_SOURCE_CHANGED", "IMAGE_SOURCE_CATALOG_INVALID",
            }:
                guidance = ("指定参考图无法定位、读取或通过权限/版本校验。"
                            "请用 get_conversation_context 读取用户选定原图的真实 message_id/content_index，"
                            "或使用获准搜索返回的 resource_ref；不要编造 file_id、自动换图或重复提交。")
            elif code in {"IMAGE_REQUEST_FIELDS_INVALID", "IMAGE_MODEL_SELECTION_DISABLED"}:
                guidance = ("此入口仅使用服务器默认模型，不传 model/model_name；"
                            "请使用实际工具参数 mode、prompt、aspect_ratio、resolution、output_format，不能用 size/format。")
            else:
                guidance = "请核对具体错误及当前工具合同，补充必要信息后继续；不要自动改写提示词或重复提交。"
            return AgentResult(
                summary=(f"图片请求未接受：{error}。未创建图片任务，未预扣图片积分。"
                         + guidance),
                status="error", error_message=code,
                metadata={"accepted": False, "completed": False, "retryable": False,
                          "submission_state": "not_accepted"},
            )
        return AgentResult(
            summary="图片任务已接受，尚未完成；结果随后在独立图片消息展示。" + json.dumps(accepted,ensure_ascii=False),
            status="success", metadata={**accepted, "accepted": True, "completed": False},
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
