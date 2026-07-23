"""Handler 智能重试与知识钩子。"""

from typing import Any, Dict, List, Optional

from loguru import logger

from schemas.message import ContentPart


class RetryKnowledgeMixin:
    """提供模型重试路由、前端通知和知识记录能力。"""

    async def _route_retry(
        self,
        retry_ctx: "RetryContext",
    ) -> Optional["RoutingDecision"]:
        """调用千问大脑重新路由，选择替代模型"""
        from services.intent_router import IntentRouter, RoutingDecision

        router = IntentRouter()
        try:
            return await router.route_retry(
                original_content=retry_ctx.original_content,
                generation_type=retry_ctx.generation_type,
                failed_models=retry_ctx.failed_models,
                error_message=retry_ctx.failed_attempts[-1]["error"],
            )
        except Exception as e:
            logger.warning(f"Retry routing failed | error={e}")
            return None
        finally:
            await router.close()

    def _build_retry_context(
        self,
        params: Dict[str, Any],
        content: List[ContentPart],
        model_id: str,
        error: str,
        existing_ctx: Optional["RetryContext"] = None,
    ) -> Optional["RetryContext"]:
        """从 params 构建或更新重试上下文"""
        from services.intent_router import RetryContext

        if not params.get("_is_smart_mode", False):
            return None
        if existing_ctx:
            existing_ctx.add_failure(model_id, error)
            return existing_ctx

        ctx = RetryContext(
            is_smart_mode=True,
            original_content=self._extract_text_content(content),
            generation_type=self.handler_type,
        )
        ctx.add_failure(model_id, error)
        return ctx

    async def _send_retry_notification(
        self,
        task_id: str,
        conversation_id: str,
        user_id: str,
        new_model: str,
        attempt: int,
    ) -> None:
        """推送重试通知给前端"""
        from schemas.websocket import build_message_retry
        from services.websocket_manager import ws_manager

        retry_msg = build_message_retry(
            task_id=task_id,
            conversation_id=conversation_id,
            new_model=new_model,
            attempt=attempt,
        )
        await ws_manager.send_to_task_or_user(
            task_id, user_id, retry_msg, org_id=self.org_id,
        )

    async def _record_knowledge_metric(self, **kwargs) -> None:
        """记录任务指标到知识库（fire-and-forget）"""
        try:
            from services.knowledge_service import record_metric
            await record_metric(**kwargs)
        except Exception as e:
            logger.debug(f"Knowledge metric record skipped | error={e}")

    async def _extract_retry_knowledge(
        self, *, task_type: str, model_id: str, retry_from_model: str,
    ) -> None:
        """重试成功时提取知识（模型对比经验）"""
        try:
            from services.knowledge_extractor import extract_and_save
            await extract_and_save(
                task_type=task_type, model_id=model_id, status="retry_success",
                retry_from_model=retry_from_model,
            )
        except Exception as e:
            logger.debug(f"Knowledge retry extraction skipped | error={e}")

    async def _extract_failure_knowledge(
        self, *, task_type: str, model_id: str, error_message: str,
    ) -> None:
        """任务失败时提取知识（失败模式）"""
        try:
            from services.knowledge_extractor import extract_and_save
            await extract_and_save(
                task_type=task_type, model_id=model_id, status="failed",
                error_message=error_message,
            )
        except Exception as e:
            logger.debug(f"Knowledge failure extraction skipped | error={e}")
