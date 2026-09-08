"""
Chat 流式生成支持 Mixin

提供 ChatHandler 的辅助能力：
- 积分计算
- 熔断器记录
- 后置任务分发（记忆提取、摘要更新、指标记录）
- 流式失败处理与智能重试
"""

import asyncio
from typing import Any, Dict, List, Optional

from loguru import logger

from schemas.message import ContentPart


class ChatStreamSupportMixin:
    """流式聊天辅助能力：积分、熔断、后置任务、重试"""

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
    ) -> None:
        """分发 fire-and-forget 后置任务（记忆提取、摘要更新、指标记录）"""
        asyncio.create_task(
            self._extract_memories_async(
                user_id=user_id,
                conversation_id=conversation_id,
                user_text=text_content,
                assistant_text=accumulated_text,
            )
        )
        asyncio.create_task(
            self._update_summary_if_needed(conversation_id)
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

    def _build_model_retry_policy(
        self, *, params, content, task_id, conversation_id, user_id,
        retry_context=None, on_retry=None,
    ):
        """给 Gateway 注入原有策略；模型 attempt 不再递归重跑 Chat。"""
        from services.model_gateway import ModelRetryPolicy

        async def notify_retry(model_id: str, attempt: int) -> None:
            if on_retry is not None:
                # Actor 的 fenced 元数据/通道通知由 Actor executor 自己持有。
                await on_retry(model_id, attempt)
                return
            await self._send_retry_notification(
                task_id, conversation_id, user_id, model_id, attempt,
            )
            try:
                self.db.table("tasks").update(
                    {"model_id": model_id}
                ).eq("external_task_id", task_id).execute()
            except Exception as error:
                logger.warning(f"Failed to update task model | task_id={task_id} | error={error}")

        return ModelRetryPolicy(
            build_context=lambda model_id, error, existing: self._build_retry_context(
                params=params, content=content, model_id=model_id,
                error=str(error), existing_ctx=existing,
            ),
            route=self._route_retry,
            record_breaker=self._record_breaker_result,
            on_retry=notify_retry,
            context=retry_context,
        )

    async def _handle_stream_failure(
        self, *, error: Exception, task_id: str, model_id: str,
        user_id: str, elapsed_ms: int, **_kwargs,
    ) -> None:
        """Gateway 已经结束模型请求；这里只提交旧 Web 失败终态和知识记录。"""
        from core.error_classifier import classify_error
        from services.model_gateway import ModelGatewayError

        if isinstance(error, ModelGatewayError):
            code = error.result.error_code
            model_id = error.result.model_id
        else:
            classified = classify_error(error)
            code = "GENERATION_FAILED" if classified.is_retryable else classified.error_code
        await self.on_error(task_id=task_id, error_code=code, error_message=str(error))
        asyncio.create_task(self._record_knowledge_metric(
            task_type="chat", model_id=model_id, status="failed",
            error_code=code, cost_time_ms=elapsed_ms, user_id=user_id,
            org_id=self.org_id,
        ))
        asyncio.create_task(self._extract_failure_knowledge(
            task_type="chat", model_id=model_id, error_message=str(error),
        ))
