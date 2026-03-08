"""
聊天消息处理器

处理流式聊天消息生成。
"""

import asyncio
import uuid
from typing import Any, Dict, List, Optional

from loguru import logger
from supabase import Client

from schemas.message import (
    ContentPart,
    GenerationType,
    Message,
    TextPart,
)
from schemas.websocket import (
    build_message_start,
    build_message_chunk,
)
from services.handlers.base import BaseHandler, TaskMetadata
from services.handlers.chat_context_mixin import ChatContextMixin
from services.websocket_manager import ws_manager


class ChatHandler(ChatContextMixin, BaseHandler):
    """聊天消息处理器：流式生成 + WebSocket 推送 + 多模态输入"""

    def __init__(self, db: Client):
        super().__init__(db)
        self._adapter = None

    @property
    def handler_type(self) -> GenerationType:
        return GenerationType.CHAT

    async def start(
        self,
        message_id: str,
        conversation_id: str,
        user_id: str,
        content: List[ContentPart],
        params: Dict[str, Any],
        metadata: TaskMetadata,
    ) -> str:
        """启动聊天任务：生成 task_id → 保存到 DB → 启动异步流式生成"""
        # 1. 获取或生成 task_id（优先使用前端提供的 client_task_id）
        task_id = metadata.client_task_id or str(uuid.uuid4())

        # 2. 获取模型配置
        model_id = params.get("model") or "gemini-3-flash"
        thinking_effort = params.get("thinking_effort")
        thinking_mode = params.get("thinking_mode")

        # 3. 保存任务到数据库
        self._save_task(
            task_id=task_id,
            message_id=message_id,
            conversation_id=conversation_id,
            user_id=user_id,
            model_id=model_id,
            content=content,
            params=params,
            metadata=metadata,
        )

        # 4. 提取路由信息
        router_system_prompt = params.get("_router_system_prompt")
        router_search_context = params.get("_router_search_context")

        # 5. 启动异步流式生成
        asyncio.create_task(
            self._stream_generate(
                task_id=task_id,
                message_id=message_id,
                conversation_id=conversation_id,
                user_id=user_id,
                content=content,
                model_id=model_id,
                thinking_effort=thinking_effort,
                thinking_mode=thinking_mode,
                router_system_prompt=router_system_prompt,
                router_search_context=router_search_context,
                _params=params,
            )
        )

        logger.info(
            f"Chat task started | task_id={task_id} | "
            f"message_id={message_id} | model={model_id}"
        )

        return task_id

    async def _save_accumulated_content(self, task_id: str, content: str) -> None:
        """将累积内容写入数据库（供刷新恢复使用）"""
        try:
            self.db.table("tasks").update(
                {"accumulated_content": content}
            ).eq("external_task_id", task_id).execute()
        except Exception as e:
            logger.warning(f"Failed to save accumulated_content | task_id={task_id} | error={e}")

    async def _stream_generate(
        self,
        task_id: str,
        message_id: str,
        conversation_id: str,
        user_id: str,
        content: List[ContentPart],
        model_id: str,
        thinking_effort: Optional[str] = None,
        thinking_mode: Optional[str] = None,
        router_system_prompt: Optional[str] = None,
        router_search_context: Optional[str] = None,
        _params: Optional[Dict[str, Any]] = None,
        _retry_context: Optional[Any] = None,
    ) -> None:
        """流式生成主逻辑（支持 smart_mode 自动重试）"""
        accumulated_text = ""
        final_usage = {"prompt_tokens": 0, "completion_tokens": 0}
        chunk_count = 0

        try:
            # 1. 推送开始消息
            start_msg = build_message_start(
                task_id=task_id,
                conversation_id=conversation_id,
                message_id=message_id,
                model=model_id,
            )
            await ws_manager.send_to_task_subscribers(task_id, start_msg)

            # 2. 组装消息列表（路由人设 + 搜索上下文 + 记忆 + 历史 + 当前消息）
            text_content = self._extract_text_content(content)
            messages = await self._build_llm_messages(
                content, user_id, conversation_id, text_content,
                router_system_prompt=router_system_prompt,
                router_search_context=router_search_context,
            )

            # 3. 创建适配器
            from services.adapters.factory import create_chat_adapter

            self._adapter = create_chat_adapter(model_id)

            # 4. 流式生成（直接使用适配器）
            async for chunk in self._adapter.stream_chat(
                messages=messages,
                reasoning_effort=thinking_effort,
                thinking_mode=thinking_mode,
            ):
                if chunk.content:
                    accumulated_text += chunk.content
                    chunk_count += 1

                    # 推送增量内容
                    chunk_msg = build_message_chunk(
                        task_id=task_id,
                        conversation_id=conversation_id,
                        message_id=message_id,
                        chunk=chunk.content,
                        accumulated=accumulated_text,
                    )
                    await ws_manager.send_to_task_subscribers(task_id, chunk_msg)

                    # 每 20 个 chunk 持久化一次累积内容（供刷新恢复）
                    if chunk_count % 20 == 0:
                        await self._save_accumulated_content(task_id, accumulated_text)

                # 捕获 usage
                if chunk.prompt_tokens or chunk.completion_tokens:
                    final_usage["prompt_tokens"] = chunk.prompt_tokens or 0
                    final_usage["completion_tokens"] = chunk.completion_tokens or 0

            # 5. 计算积分消耗（使用 adapter 的统一方法）
            cost_estimate = self._adapter.estimate_cost_unified(
                input_tokens=final_usage["prompt_tokens"],
                output_tokens=final_usage["completion_tokens"],
            )
            credits_consumed = cost_estimate.estimated_credits

            # 对于 KIE 模型，最少 1 积分；对于免费模型（如 Google），为 0
            if credits_consumed > 0:
                credits_consumed = max(1, credits_consumed)

            # 6. 完成回调
            await self.on_complete(
                task_id=task_id,
                result=[TextPart(text=accumulated_text)],
                credits_consumed=credits_consumed,
            )

            # 7. 异步提取记忆（fire-and-forget，失败不影响主流程）
            asyncio.create_task(
                self._extract_memories_async(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    user_text=text_content,
                    assistant_text=accumulated_text,
                )
            )

            # 8. 异步更新对话摘要（fire-and-forget，失败不影响主流程）
            asyncio.create_task(
                self._update_summary_if_needed(conversation_id)
            )

        except Exception as e:
            logger.error(f"Chat stream error | task_id={task_id} | model={model_id} | error={str(e)}")

            # 智能重试
            retried = await self._attempt_chat_retry(
                error=e, task_id=task_id, message_id=message_id,
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
                    error_message=str(e),
                )

        finally:
            if self._adapter:
                await self._adapter.close()

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

    def _convert_content_parts_to_dicts(self, result: List[ContentPart]) -> List[Dict[str, Any]]:
        """转换 TextPart 为字典"""
        content_dicts = []
        for part in result:
            if isinstance(part, TextPart):
                content_dicts.append({"type": "text", "text": part.text})
            elif isinstance(part, dict):
                content_dicts.append(part)
        return content_dicts

    async def _handle_credits_on_complete(
        self,
        task: Dict[str, Any],
        credits_consumed: int,
    ) -> int:
        """Chat 完成时直接扣除积分"""
        if credits_consumed > 0:
            user_id = task["user_id"]
            model_id = task.get("model_id", "gemini-3-flash")
            self._deduct_directly(
                user_id=user_id,
                amount=credits_consumed,
                reason=f"Chat: {model_id}",
                change_type="conversation_cost",
            )
        return credits_consumed

    async def _handle_credits_on_error(self, task: Dict[str, Any]) -> None:
        pass  # Chat 无预扣，无需退回

    async def on_complete(
        self,
        task_id: str,
        result: List[ContentPart],
        credits_consumed: int = 0,
    ) -> Message:
        """完成回调（调用基类通用流程）"""
        return await self._handle_complete_common(task_id, result, credits_consumed)

    async def on_error(
        self,
        task_id: str,
        error_code: str,
        error_message: str,
    ) -> Message:
        """错误回调（调用基类通用流程）"""
        return await self._handle_error_common(task_id, error_code, error_message)

    def _save_task(
        self,
        task_id: str,
        message_id: str,
        conversation_id: str,
        user_id: str,
        model_id: str,
        content: List[ContentPart],
        params: Dict[str, Any],
        metadata: TaskMetadata,
    ) -> None:
        """保存任务到数据库"""
        # 1. 序列化业务参数
        request_params = {
            "content": self._extract_text_content(content),
            "model_id": model_id,
            **self._serialize_params(params),
        }

        # 2. 构建标准 task_data（使用基类方法）
        task_data = self._build_task_data(
            task_id=task_id,
            message_id=message_id,
            conversation_id=conversation_id,
            user_id=user_id,
            task_type="chat",
            status="running",
            model_id=model_id,
            request_params=request_params,
            metadata=metadata,
        )

        # 3. 保存到数据库
        self.db.table("tasks").insert(task_data).execute()

