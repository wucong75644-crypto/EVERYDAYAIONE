"""
Chat 流式生成支持 Mixin

提供 ChatHandler 的共享执行辅助能力：
- 积分计算
- 熔断器记录
- 后置任务分发（记忆提取、摘要更新、指标记录）
"""

import asyncio
from typing import Any, Dict, Optional

from loguru import logger

class ChatStreamSupportMixin:
    """聊天执行辅助能力：积分、熔断与后置任务。"""

    def _calculate_credits(self, final_usage: Dict[str, Any]) -> int:
        """根据 API 报告或本地估算计算积分消耗"""
        import math
        api_credits = final_usage.get("api_credits")
        if api_credits is not None:
            credits_consumed = math.ceil(api_credits) + 1
            logger.info(
                f"Credits from API | api={api_credits} | "
                f"charged={credits_consumed} (ceil+1)"
            )
        else:
            cost_estimate = self._adapter.estimate_cost_unified(
                input_tokens=final_usage["prompt_tokens"],
                output_tokens=final_usage["completion_tokens"],
            )
            credits_consumed = cost_estimate.estimated_credits

        if credits_consumed > 0:
            credits_consumed = max(1, credits_consumed)
        return credits_consumed

    @staticmethod
    def _record_breaker_result(
        model_id: str,
        success: bool,
        error: Optional[Exception] = None,
    ) -> None:
        """向熔断器记录成功/失败"""
        from services.adapters.factory import MODEL_REGISTRY
        from services.adapters.types import ProviderUnavailableError
        from services.circuit_breaker import get_breaker

        # ProviderUnavailableError 说明熔断器已经 OPEN，不重复记录
        if not success and isinstance(error, ProviderUnavailableError):
            return

        config = MODEL_REGISTRY.get(model_id)
        if not config:
            return

        breaker = get_breaker(config.provider)
        if success:
            breaker.record_success()
        else:
            breaker.record_failure()

    def _dispatch_post_tasks(
        self,
        user_id: str,
        conversation_id: str,
        text_content: str,
        accumulated_text: str,
        model_id: str,
        final_usage: Dict[str, Any],
        elapsed_ms: int,
        retry_context: Optional[Any],
        input_message_id: Optional[str] = None,
        output_message_id: Optional[str] = None,
        through_revision: Optional[int] = None,
    ) -> None:
        """分发 fire-and-forget 后置任务（记忆提取、摘要更新、指标记录）"""
        asyncio.create_task(
            self._extract_memories_async(
                user_id=user_id,
                conversation_id=conversation_id,
                user_text=text_content,
                assistant_text=accumulated_text,
                input_message_id=input_message_id,
                output_message_id=output_message_id,
                through_revision=through_revision,
            )
        )
        asyncio.create_task(
            self._record_knowledge_metric(
                task_type="chat", model_id=model_id, status="success",
                cost_time_ms=elapsed_ms,
                prompt_tokens=final_usage["prompt_tokens"],
                completion_tokens=final_usage["completion_tokens"],
                retried=bool(retry_context),
                retry_from_model=(
                    retry_context.failed_attempts[-1]["model"]
                    if retry_context and retry_context.failed_attempts
                    else None
                ),
                user_id=user_id,
                org_id=self.org_id,
            )
        )
        if retry_context and retry_context.failed_attempts:
            asyncio.create_task(
                self._extract_retry_knowledge(
                    task_type="chat", model_id=model_id,
                    retry_from_model=retry_context.failed_attempts[-1]["model"],
                )
            )
