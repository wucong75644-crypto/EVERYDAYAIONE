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
    build_thinking_chunk,
)
from services.adapters.factory import DEFAULT_MODEL_ID
from services.handlers.base import BaseHandler, TaskMetadata
from services.handlers.chat_context_mixin import ChatContextMixin
from services.handlers.chat_routing_mixin import ChatRoutingMixin
from services.handlers.chat_stream_support_mixin import ChatStreamSupportMixin
from services.websocket_manager import ws_manager


class ChatHandler(ChatRoutingMixin, ChatStreamSupportMixin, ChatContextMixin, BaseHandler):
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
        model_id = params.get("model") or DEFAULT_MODEL_ID

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

        # 4. Smart mode 异步路由 vs 常规流式生成
        needs_routing = params.get("_needs_routing", False)

        if needs_routing:
            # Smart mode: 路由在异步阶段执行（不阻塞 HTTP 响应）
            asyncio.create_task(
                self._route_and_stream(
                    task_id=task_id,
                    message_id=message_id,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    content=content,
                    _params=params,
                    metadata=metadata,
                )
            )
        else:
            # 常规路由：路由已在 HTTP 阶段完成
            asyncio.create_task(
                self._stream_generate(
                    task_id=task_id,
                    message_id=message_id,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    content=content,
                    model_id=model_id,
                    thinking_effort=params.get("thinking_effort"),
                    thinking_mode=params.get("thinking_mode"),
                    router_system_prompt=params.get("_router_system_prompt"),
                    router_search_context=params.get("_router_search_context"),
                    needs_google_search=params.get("_needs_google_search", False),
                    _params=params,
                )
            )

        logger.info(
            f"Chat task started | task_id={task_id} | "
            f"message_id={message_id} | model={model_id} | "
            f"routing={'deferred' if needs_routing else 'resolved'}"
        )

        return task_id

    async def _stream_direct_reply(
        self,
        task_id: str,
        message_id: str,
        conversation_id: str,
        text: str,
    ) -> None:
        """Agent Loop ask_user：大脑直接回复，跳过 LLM 调用"""
        try:
            # 推送 start
            start_msg = build_message_start(
                task_id=task_id,
                conversation_id=conversation_id,
                message_id=message_id,
                model="agent",
            )
            await ws_manager.send_to_task_subscribers(task_id, start_msg)

            # 推送完整文字作为一个 chunk
            chunk_msg = build_message_chunk(
                task_id=task_id,
                conversation_id=conversation_id,
                message_id=message_id,
                chunk=text,
                accumulated=text,
            )
            await ws_manager.send_to_task_subscribers(task_id, chunk_msg)

            # 完成回调（0 积分）
            await self.on_complete(
                task_id=task_id,
                result=[TextPart(text=text)],
                credits_consumed=0,
            )

            logger.info(
                f"Direct reply sent | task_id={task_id} | len={len(text)}"
            )
        except Exception as e:
            logger.error(f"Direct reply error | task_id={task_id} | error={e}")
            await self.on_error(
                task_id=task_id,
                error_code="DIRECT_REPLY_FAILED",
                error_message=str(e),
            )

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
        needs_google_search: bool = False,
        _params: Optional[Dict[str, Any]] = None,
        _retry_context: Optional[Any] = None,
    ) -> None:
        """流式生成主逻辑（支持 smart_mode 自动重试）"""
        import time as _time

        # Agent Loop ask_user：大脑主动回复，跳过 LLM 调用
        direct_reply = (_params or {}).get("_direct_reply")
        if direct_reply:
            await self._stream_direct_reply(
                task_id=task_id,
                message_id=message_id,
                conversation_id=conversation_id,
                text=direct_reply,
            )
            return

        _start_time = _time.monotonic()
        accumulated_text = ""
        accumulated_thinking = ""
        final_usage: Dict[str, Any] = {"prompt_tokens": 0, "completion_tokens": 0}
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

            # 2. 组装消息列表
            text_content = self._extract_text_content(content)
            prefetched_summary = (_params or {}).get("_prefetched_summary")
            prefetched_memory = (_params or {}).get("_prefetched_memory")
            messages = await self._build_llm_messages(
                content, user_id, conversation_id, text_content,
                router_system_prompt=router_system_prompt,
                router_search_context=router_search_context,
                prefetched_summary=prefetched_summary,
                prefetched_memory=prefetched_memory,
            )

            # 3. 创建适配器并流式生成
            from services.adapters.factory import create_chat_adapter

            self._adapter = create_chat_adapter(model_id)
            logger.info(
                f"Stream generate starting | model={model_id} | "
                f"adapter={type(self._adapter).__name__} | task={task_id}"
            )

            # 按需启用 Google Search Grounding（能力匹配）
            stream_kwargs: Dict[str, Any] = {}
            if needs_google_search and hasattr(self._adapter, 'supports_google_search') and self._adapter.supports_google_search:
                google_tool = self._adapter.create_google_search_tool()
                stream_kwargs["tools"] = [google_tool]
                logger.info(f"Google Search Grounding enabled | model={model_id} | task={task_id}")

            async for chunk in self._adapter.stream_chat(
                messages=messages,
                reasoning_effort=thinking_effort,
                thinking_mode=thinking_mode,
                **stream_kwargs,
            ):
                # 思考内容（reasoning_content）
                if chunk.thinking_content:
                    accumulated_thinking += chunk.thinking_content
                    thinking_msg = build_thinking_chunk(
                        task_id=task_id,
                        conversation_id=conversation_id,
                        message_id=message_id,
                        chunk=chunk.thinking_content,
                        accumulated=accumulated_thinking,
                    )
                    await ws_manager.send_to_task_subscribers(task_id, thinking_msg)

                # 正文内容
                if chunk.content:
                    accumulated_text += chunk.content
                    chunk_count += 1

                    chunk_msg = build_message_chunk(
                        task_id=task_id,
                        conversation_id=conversation_id,
                        message_id=message_id,
                        chunk=chunk.content,
                    )
                    await ws_manager.send_to_task_subscribers(task_id, chunk_msg)

                    if chunk_count % 20 == 0:
                        asyncio.create_task(self._save_accumulated_content(task_id, accumulated_text))

                if chunk.prompt_tokens or chunk.completion_tokens:
                    final_usage["prompt_tokens"] = chunk.prompt_tokens or 0
                    final_usage["completion_tokens"] = chunk.completion_tokens or 0
                if chunk.credits_consumed is not None:
                    final_usage["api_credits"] = chunk.credits_consumed

            # 4. 计算积分 → 完成回调
            credits_consumed = self._calculate_credits(final_usage)
            await self.on_complete(
                task_id=task_id,
                result=[TextPart(text=accumulated_text)],
                credits_consumed=credits_consumed,
                thinking_content=accumulated_thinking or None,
            )

            # 5. 熔断器：记录成功
            self._record_breaker_result(model_id, success=True)

            # 6. Fire-and-forget 后置任务
            elapsed_ms = int((_time.monotonic() - _start_time) * 1000)
            self._dispatch_post_tasks(
                user_id=user_id, conversation_id=conversation_id,
                text_content=text_content, accumulated_text=accumulated_text,
                model_id=model_id, final_usage=final_usage,
                elapsed_ms=elapsed_ms, retry_context=_retry_context,
            )

        except Exception as e:
            logger.error(
                f"Chat stream error | task_id={task_id} | "
                f"model={model_id} | error={str(e)}"
            )
            # 熔断器：仅在真实 API 失败时记录（ProviderUnavailableError 不算）
            self._record_breaker_result(model_id, success=False, error=e)
            elapsed_ms = int((_time.monotonic() - _start_time) * 1000)
            await self._handle_stream_failure(
                error=e, task_id=task_id, message_id=message_id,
                conversation_id=conversation_id, user_id=user_id,
                content=content, model_id=model_id,
                thinking_effort=thinking_effort, thinking_mode=thinking_mode,
                router_system_prompt=router_system_prompt,
                router_search_context=router_search_context,
                _params=_params, _retry_context=_retry_context,
                elapsed_ms=elapsed_ms,
            )

        finally:
            if self._adapter:
                await self._adapter.close()

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
            model_id = task.get("model_id", DEFAULT_MODEL_ID)
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
        thinking_content: Optional[str] = None,
    ) -> Message:
        """完成回调（调用基类通用流程）"""
        return await self._handle_complete_common(
            task_id, result, credits_consumed,
            thinking_content=thinking_content,
        )

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

