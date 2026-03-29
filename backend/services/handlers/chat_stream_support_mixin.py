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
from schemas.websocket import build_message_start, build_message_chunk


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

    async def _handle_stream_failure(
        self,
        error: Exception,
        task_id: str,
        message_id: str,
        conversation_id: str,
        user_id: str,
        content: List[ContentPart],
        model_id: str,
        thinking_effort: Optional[str],
        thinking_mode: Optional[str],
        router_system_prompt: Optional[str],
        router_search_context: Optional[str],
        _params: Optional[Dict[str, Any]],
        _retry_context: Optional[Any],
        elapsed_ms: int,
    ) -> None:
        """处理流式生成失败：尝试重试，否则报错 + 记录指标"""
        retried = await self._attempt_chat_retry(
            error=error, task_id=task_id, message_id=message_id,
            conversation_id=conversation_id, user_id=user_id,
            content=content, model_id=model_id,
            thinking_effort=thinking_effort, thinking_mode=thinking_mode,
            router_system_prompt=router_system_prompt,
            router_search_context=router_search_context,
            _params=_params, _retry_context=_retry_context,
        )
        if not retried:
            await self.on_error(
                task_id=task_id,
                error_code="GENERATION_FAILED",
                error_message=str(error),
            )
            asyncio.create_task(
                self._record_knowledge_metric(
                    task_type="chat", model_id=model_id, status="failed",
                    error_code="GENERATION_FAILED", cost_time_ms=elapsed_ms,
                    user_id=user_id, org_id=self.org_id,
                )
            )
            asyncio.create_task(
                self._extract_failure_knowledge(
                    task_type="chat", model_id=model_id,
                    error_message=str(error),
                )
            )

    async def _attempt_chat_retry(
        self,
        error: Exception,
        task_id: str,
        message_id: str,
        conversation_id: str,
        user_id: str,
        content: List[ContentPart],
        model_id: str,
        thinking_effort: Optional[str],
        thinking_mode: Optional[str],
        router_system_prompt: Optional[str],
        router_search_context: Optional[str],
        _params: Optional[Dict[str, Any]],
        _retry_context: Optional[Any],
    ) -> bool:
        """Smart mode 重试：调用千问大脑重新选择模型，成功则递归重试"""
        retry_ctx = self._build_retry_context(
            params=_params or {}, content=content,
            model_id=model_id, error=str(error),
            existing_ctx=_retry_context,
        )
        if not retry_ctx or not retry_ctx.can_retry:
            return False

        new_decision = await self._route_retry(retry_ctx)
        if not new_decision or not new_decision.recommended_model:
            return False

        new_model = new_decision.recommended_model
        attempt = len(retry_ctx.failed_attempts)
        logger.info(
            f"Chat retry | task_id={task_id} | "
            f"failed={model_id} → new={new_model} | attempt={attempt}"
        )

        # WS 通知前端正在重试
        await self._send_retry_notification(
            task_id, conversation_id, new_model, attempt,
        )

        # 关闭旧 adapter
        if self._adapter:
            await self._adapter.close()
            self._adapter = None

        # 更新 DB 中的 model_id
        try:
            self.db.table("tasks").update(
                {"model_id": new_model}
            ).eq("external_task_id", task_id).execute()
        except Exception as e:
            logger.warning(f"Failed to update task model | task_id={task_id} | error={e}")

        # 递归重试（用新模型）
        await self._stream_generate(
            task_id=task_id, message_id=message_id,
            conversation_id=conversation_id, user_id=user_id,
            content=content, model_id=new_model,
            thinking_effort=thinking_effort, thinking_mode=thinking_mode,
            router_system_prompt=router_system_prompt,
            router_search_context=router_search_context,
            _params=_params, _retry_context=retry_ctx,
        )
        return True
