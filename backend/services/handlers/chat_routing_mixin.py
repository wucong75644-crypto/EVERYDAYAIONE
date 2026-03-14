"""
Chat 路由 Mixin — Smart mode 异步路由

在 ChatHandler 的异步阶段执行 Agent Loop 路由，
而非阻塞 HTTP 响应。支持路由到 chat/image/video。
"""

import asyncio
from typing import Any, Dict, List, Optional

from loguru import logger

from schemas.message import ContentPart, GenerationType
from services.websocket_manager import ws_manager


class ChatRoutingMixin:
    """Smart mode 异步路由能力：路由 + 记忆并行 + image/video 重路由"""

    async def _route_and_stream(
        self,
        task_id: str,
        message_id: str,
        conversation_id: str,
        user_id: str,
        content: List[ContentPart],
        _params: Dict[str, Any],
        metadata: Any,
    ) -> None:
        """Smart mode 异步路由：执行 Agent Loop，路由完成后分发到对应 Handler"""
        try:
            # 1. 记忆预取与 Agent Loop 并行（记忆仅 chat 时用，image/video 丢弃）
            text_content = self._extract_text_content(content)
            agent_task = self._run_agent_loop(
                content, user_id, conversation_id, _params, task_id=task_id,
            )
            memory_task = self._build_memory_prompt(user_id, text_content)

            agent_result_raw, memory_result_raw = await asyncio.gather(
                agent_task, memory_task, return_exceptions=True,
            )

            # Agent Loop 结果：失败则向上抛出（由 except 降级处理）
            if isinstance(agent_result_raw, BaseException):
                raise agent_result_raw
            agent_result = agent_result_raw

            # 记忆结果：失败降级为 None（不影响主流程）
            prefetched_memory: Optional[str] = None
            if isinstance(memory_result_raw, BaseException):
                logger.warning(
                    f"Memory prefetch failed | task={task_id} | "
                    f"error={memory_result_raw}"
                )
            else:
                prefetched_memory = memory_result_raw

            # 2. 解析路由结果
            gen_type = agent_result.generation_type
            from services.intent_router import resolve_auto_model
            model_id = resolve_auto_model(gen_type, content, agent_result.model)

            # 3. 应用路由结果到 params
            self._apply_agent_result(agent_result, _params, model_id)

            logger.info(
                f"Async routing done | type={gen_type.value} | "
                f"model={model_id} | task={task_id}"
            )

            # 4. 通知前端路由结果
            from schemas.websocket import build_routing_complete
            from services.websocket_manager import ws_manager as _ws

            gen_params: Dict[str, Any] = {"type": gen_type.value, "model": model_id}
            routing_msg = build_routing_complete(
                task_id=task_id,
                conversation_id=conversation_id,
                generation_type=gen_type.value,
                model=model_id,
                message_id=message_id,
                generation_params=gen_params,
            )
            await _ws.send_to_task_subscribers(task_id, routing_msg)

            # 5. 分发到对应 Handler
            if gen_type == GenerationType.CHAT:
                # 将预取的记忆注入 params，供 _build_llm_messages 使用
                if prefetched_memory is not None:
                    _params["_prefetched_memory"] = prefetched_memory

                await self._stream_generate(
                    task_id=task_id,
                    message_id=message_id,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    content=content,
                    model_id=model_id,
                    thinking_effort=_params.get("thinking_effort"),
                    thinking_mode=_params.get("thinking_mode"),
                    router_system_prompt=_params.get("_router_system_prompt"),
                    router_search_context=_params.get("_router_search_context"),
                    needs_google_search=_params.get("_needs_google_search", False),
                    _params=_params,
                )
            else:
                await self._reroute_to_media(
                    task_id=task_id,
                    message_id=message_id,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    content=content,
                    params=_params,
                    metadata=metadata,
                    gen_type=gen_type,
                    model_id=model_id,
                )

        except Exception as e:
            logger.error(
                f"Async routing failed, fallback to default chat | "
                f"task={task_id} | error={e}"
            )
            # 降级：用默认模型直接聊天
            from services.adapters.factory import DEFAULT_MODEL_ID
            await self._stream_generate(
                task_id=task_id,
                message_id=message_id,
                conversation_id=conversation_id,
                user_id=user_id,
                content=content,
                model_id=DEFAULT_MODEL_ID,
                _params=_params,
            )

    async def _run_agent_loop(
        self,
        content: List[ContentPart],
        user_id: str,
        conversation_id: str,
        params: Dict[str, Any],
        task_id: Optional[str] = None,
    ) -> Any:
        """执行 Agent Loop，返回 AgentResult（失败时降级到 IntentRouter）"""
        from core.config import get_settings
        from services.agent_loop import AgentLoop

        settings = get_settings()
        thinking_mode = params.get("thinking_mode")

        if settings.agent_loop_enabled:
            agent = AgentLoop(self.db, user_id, conversation_id)
            try:
                result = await agent.run(
                    content, thinking_mode=thinking_mode, task_id=task_id,
                    user_location=params.get("_user_location"),
                )
                logger.info(
                    f"Agent loop completed | type={result.generation_type.value} | "
                    f"turns={result.turns_used} | tokens={result.total_tokens}"
                )
                return result
            except Exception as e:
                logger.warning(
                    f"Agent loop failed, trying IntentRouter | error={e!r}"
                )
            finally:
                await agent.close()

        # 降级到 IntentRouter
        from services.intent_router import IntentRouter
        router = IntentRouter()
        try:
            decision = await router.route(
                content=content, user_id=user_id,
                conversation_id=conversation_id,
            )
            # 包装为 AgentResult 兼容格式
            from services.agent_types import AgentResult
            return AgentResult(
                generation_type=decision.generation_type,
                model=decision.recommended_model,
                system_prompt=decision.system_prompt,
                tool_params=decision.tool_params,
                turns_used=0,
                total_tokens=0,
            )
        except Exception:
            # 兜底：默认 chat
            from services.agent_types import AgentResult
            return AgentResult(
                generation_type=GenerationType.CHAT,
                turns_used=0,
                total_tokens=0,
            )
        finally:
            await router.close()

    @staticmethod
    def _apply_agent_result(
        agent_result: Any,
        params: Dict[str, Any],
        model_id: str,
    ) -> None:
        """将 Agent Loop 结果注入 params（供 _stream_generate 使用）"""
        params["model"] = model_id

        if agent_result.system_prompt:
            params["_router_system_prompt"] = agent_result.system_prompt
        if agent_result.search_context:
            params["_router_search_context"] = agent_result.search_context
        if agent_result.direct_reply:
            params["_direct_reply"] = agent_result.direct_reply
        if agent_result.render_hints:
            params["_render"] = agent_result.render_hints

        tool_params = agent_result.tool_params or {}
        if tool_params.get("_needs_google_search"):
            params["_needs_google_search"] = True
        for key in ("prompt", "resolution", "aspect_ratio", "output_format"):
            val = tool_params.get(key)
            if val is not None:
                params[key] = val

        # 批量生图（Agent Loop 多图提示词）
        if agent_result.batch_prompts:
            params["_batch_prompts"] = agent_result.batch_prompts
            params["num_images"] = len(agent_result.batch_prompts)
            first_ratio = agent_result.batch_prompts[0].get("aspect_ratio", "1:1")
            if "aspect_ratio" not in params:
                params["aspect_ratio"] = first_ratio

    async def _reroute_to_media(
        self,
        task_id: str,
        message_id: str,
        conversation_id: str,
        user_id: str,
        content: List[ContentPart],
        params: Dict[str, Any],
        metadata: Any,
        gen_type: GenerationType,
        model_id: str,
    ) -> None:
        """重路由到 Image/Video Handler（smart mode 路由结果非 chat 时）"""
        from services.handlers import get_handler
        from services.handlers.base import TaskMetadata

        _PLACEHOLDER = {
            GenerationType.IMAGE: "图片生成中",
            GenerationType.VIDEO: "视频生成中",
        }
        placeholder_text = _PLACEHOLDER.get(gen_type, "生成中")

        # 1. 标记原 chat task 为 completed（rerouted）
        try:
            self.db.table("tasks").update(
                {"status": "completed"}
            ).eq("external_task_id", task_id).execute()
        except Exception as e:
            logger.warning(f"Reroute: failed to complete chat task | error={e}")

        # 2. 插入媒体占位符消息到 DB
        gen_params: Dict[str, Any] = {"type": gen_type.value, "model": model_id}
        render = params.get("_render", {})
        if render:
            gen_params["_render"] = render
        for key in ("aspect_ratio", "resolution", "output_format", "num_images"):
            if key in params:
                gen_params[key] = params[key]

        try:
            self.db.table("messages").insert({
                "id": message_id,
                "conversation_id": conversation_id,
                "role": "assistant",
                "content": [{"type": "text", "text": placeholder_text}],
                "status": "pending",
                "generation_params": gen_params,
                "credits_cost": 0,
            }).execute()
        except Exception as e:
            logger.warning(f"Reroute: media placeholder insert failed | error={e}")

        # 3. 委派给对应 Handler（routing_complete 已在 _route_and_stream 中发送）
        handler = get_handler(gen_type, self.db)
        new_metadata = TaskMetadata(
            client_task_id=metadata.client_task_id
            if hasattr(metadata, "client_task_id")
            else None,
            placeholder_created_at=metadata.placeholder_created_at
            if hasattr(metadata, "placeholder_created_at")
            else None,
        )
        await handler.start(
            message_id=message_id,
            conversation_id=conversation_id,
            user_id=user_id,
            content=content,
            params=params,
            metadata=new_metadata,
        )

        logger.info(
            f"Rerouted to {gen_type.value} | task={task_id} | model={model_id}"
        )
