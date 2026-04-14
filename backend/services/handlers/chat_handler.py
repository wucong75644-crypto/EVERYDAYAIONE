"""
聊天消息处理器

处理流式聊天消息生成 + 工具循环执行。
"""

import asyncio
import uuid
from typing import Any, Dict, List, Optional

from loguru import logger


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
    build_tool_call,
)
from services.adapters.factory import DEFAULT_MODEL_ID
from services.handlers.base import BaseHandler, TaskMetadata
from services.handlers.chat_context_mixin import ChatContextMixin
from services.handlers.chat_generate_mixin import ChatGenerateMixin
from services.handlers.chat_stream_support_mixin import ChatStreamSupportMixin
from services.handlers.chat_tool_mixin import ChatToolMixin, accumulate_tool_call_delta
from services.websocket_manager import ws_manager

# 优雅降级提示消息
_STOP_MESSAGES = {
    "max_turns": "查询涉及多个步骤，已达到单次对话工具调用上限。请缩小查询范围或分步提问。",
    "max_tokens": "本次查询消耗的数据量过大，请缩小查询范围。",
    "wall_timeout": "查询耗时过长，请稍后重试。",
}


class ChatHandler(ChatGenerateMixin, ChatToolMixin, ChatStreamSupportMixin, ChatContextMixin, BaseHandler):
    """聊天消息处理器：流式生成 + WebSocket 推送 + 多模态输入"""

    def __init__(self, db):
        super().__init__(db)
        self._adapter = None
        self._pending_file_parts: list = []  # 沙盒 upload_file 生成的 FilePart 暂存

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

        # 4. 启动流式生成（单循环工具编排，不再有异步路由分支）
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
            f"message_id={message_id} | model={model_id}"
        )

        return task_id

    async def _stream_direct_reply(self, task_id, message_id, conversation_id, user_id, text):
        """Agent Loop ask_user：大脑直接回复，跳过 LLM 调用"""
        try:
            await ws_manager.send_to_task_or_user(task_id, user_id, build_message_start(
                task_id=task_id, conversation_id=conversation_id,
                message_id=message_id, model="agent",
            ))
            await ws_manager.send_to_task_or_user(task_id, user_id, build_message_chunk(
                task_id=task_id, conversation_id=conversation_id,
                message_id=message_id, chunk=text, accumulated=text,
            ))
            await self.on_complete(task_id=task_id, result=[TextPart(text=text)], credits_consumed=0)
            logger.info(f"Direct reply sent | task_id={task_id} | len={len(text)}")
        except Exception as e:
            logger.error(f"Direct reply error | task_id={task_id} | error={e}")
            await self.on_error(task_id=task_id, error_code="DIRECT_REPLY_FAILED", error_message=str(e))

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
        """流式生成主逻辑（支持工具循环 + smart_mode 自动重试）"""
        import time as _time

        # Agent Loop ask_user：大脑主动回复，跳过 LLM 调用
        direct_reply = (_params or {}).get("_direct_reply")
        if direct_reply:
            await self._stream_direct_reply(
                task_id=task_id,
                message_id=message_id,
                conversation_id=conversation_id,
                user_id=user_id,
                text=direct_reply,
            )
            return

        _start_time = _time.monotonic()
        accumulated_text = ""
        accumulated_thinking = ""
        final_usage: Dict[str, Any] = {"prompt_tokens": 0, "completion_tokens": 0}
        chunk_count = 0
        _llm_succeeded = False
        _completion_args: Optional[Dict[str, Any]] = None
        # 多内容块追踪：每轮 LLM 文本 = 独立 TextPart，工具结果 = ToolResultPart
        _content_blocks: List[Dict[str, Any]] = []

        try:
            # 1. 推送开始消息
            start_msg = build_message_start(
                task_id=task_id,
                conversation_id=conversation_id,
                message_id=message_id,
                model=model_id,
            )
            await ws_manager.send_to_task_or_user(task_id, user_id, start_msg)

            # 2. 组装消息列表（记忆未预取时并行预取）
            text_content = self._extract_text_content(content)
            prefetched_summary = (_params or {}).get("_prefetched_summary")
            prefetched_memory = (_params or {}).get("_prefetched_memory")
            user_location = (_params or {}).get("_user_location")

            if prefetched_memory is None:
                # 非 smart mode 路径：记忆未预取，在此并行获取
                memory_result = await asyncio.gather(
                    self._build_memory_prompt(user_id, text_content),
                    return_exceptions=True,
                )
                mem = memory_result[0]
                if isinstance(mem, BaseException):
                    logger.warning(f"Memory prefetch failed | task={task_id} | error={mem}")
                else:
                    prefetched_memory = mem

            messages = await self._build_llm_messages(
                content, user_id, conversation_id, text_content,
                router_system_prompt=router_system_prompt,
                router_search_context=router_search_context,
                prefetched_summary=prefetched_summary,
                prefetched_memory=prefetched_memory,
                user_location=user_location,
            )

            # 3. 创建适配器
            from services.adapters.factory import create_chat_adapter

            self._adapter = create_chat_adapter(
                model_id, org_id=self.org_id, db=self.db,
            )
            logger.info(
                f"Stream generate starting | model={model_id} | "
                f"adapter={type(self._adapter).__name__} | task={task_id}"
            )

            # 4. 注入全局工具使用指引
            from config.chat_tools import (
                get_core_tools, get_tools_by_names, get_tool_system_prompt,
            )
            tool_prompt = get_tool_system_prompt()
            if tool_prompt:
                messages.append({"role": "system", "content": tool_prompt})

            # 5. 加载核心工具（ToolSearch 模式：9 个核心直接加载）
            core_tools = get_core_tools(org_id=self.org_id)

            # 按需启用 Google Search Grounding
            stream_kwargs: Dict[str, Any] = {}
            if needs_google_search and hasattr(self._adapter, 'supports_google_search') and self._adapter.supports_google_search:
                google_tool = self._adapter.create_google_search_tool()
                core_tools.append(google_tool)
                logger.info(f"Google Search Grounding enabled | model={model_id} | task={task_id}")

            # 6. 工具循环上下文（主 Agent = general 域）
            from services.handlers.tool_loop_context import ToolLoopContext
            tool_context = ToolLoopContext(org_id=self.org_id, agent_domain="general")

            # Phase 5: 初始化增量记忆（必须在主协程中调用）
            from services.handlers.session_memory import init_session_memory
            init_session_memory()

            # 7. 工具循环：流式生成 → 检测工具调用 → 执行 → 结果塞回 → 继续
            # 多维预算：轮次为主控，token 为安全网，时间纯兜底
            from services.agent.execution_budget import ExecutionBudget
            from core.config import get_settings as _get_settings
            _s = _get_settings()
            _budget = ExecutionBudget(
                max_turns=_s.budget_max_turns,
                max_tokens=_s.budget_max_tokens,
                max_wall_time=_s.budget_max_wall_time,
            )

            while not _budget.stop_reason:
                _budget.use_turn()
                turn = _budget.turns_used - 1  # 0-based for logging
                # 每轮动态构建工具列表：核心工具 + 已发现的工具（域过滤兜底）
                current_tools = list(core_tools)
                if tool_context.discovered_tools:
                    from config.tool_domains import filter_tools_for_domain
                    discovered = get_tools_by_names(
                        tool_context.discovered_tools, org_id=self.org_id,
                    )
                    # 域过滤兜底：即使 discovered_tools 含 ERP 工具，也会被拦截
                    discovered = filter_tools_for_domain(discovered, "general")
                    # 去重（核心工具里可能已包含）
                    core_names = {t["function"]["name"] for t in core_tools}
                    current_tools.extend(
                        t for t in discovered
                        if t["function"]["name"] not in core_names
                    )
                    logger.info(
                        f"Dynamic tools injected | turn={turn + 1} | "
                        f"discovered={sorted(tool_context.discovered_tools)} | "
                        f"total={len(current_tools)}"
                    )
                stream_kwargs["tools"] = current_tools

                # 注入上一轮的上下文提示（先去重旧的，再追加新的）
                if turn > 0:
                    from services.handlers.context_compressor import deduplicate_system_prompts
                    deduplicate_system_prompts(messages)
                    ctx_prompt = tool_context.build_context_prompt()
                    if ctx_prompt:
                        messages.append({"role": "system", "content": ctx_prompt})

                turn_text = ""
                turn_thinking = ""
                tool_calls_acc: Dict[int, Dict[str, Any]] = {}  # index → {id, name, arguments}

                async for chunk in self._adapter.stream_chat(
                    messages=messages,
                    reasoning_effort=thinking_effort,
                    thinking_mode=thinking_mode,
                    **stream_kwargs,
                ):
                    # 思考内容
                    if chunk.thinking_content:
                        turn_thinking += chunk.thinking_content
                        accumulated_thinking += chunk.thinking_content
                        thinking_msg = build_thinking_chunk(
                            task_id=task_id,
                            conversation_id=conversation_id,
                            message_id=message_id,
                            chunk=chunk.thinking_content,
                            accumulated=accumulated_thinking,
                        )
                        await ws_manager.send_to_task_or_user(task_id, user_id, thinking_msg)

                    # 正文内容
                    if chunk.content:
                        turn_text += chunk.content
                        accumulated_text += chunk.content
                        chunk_count += 1
                        chunk_msg = build_message_chunk(
                            task_id=task_id,
                            conversation_id=conversation_id,
                            message_id=message_id,
                            chunk=chunk.content,
                        )
                        await ws_manager.send_to_task_or_user(task_id, user_id, chunk_msg)
                        if chunk_count % 20 == 0:
                            asyncio.create_task(
                                self._save_accumulated_content(task_id, accumulated_text)
                            )

                    # 工具调用增量累积
                    if chunk.tool_calls:
                        accumulate_tool_call_delta(tool_calls_acc, chunk.tool_calls)

                    # Token 使用量（累加到 budget + final_usage）
                    if chunk.prompt_tokens or chunk.completion_tokens:
                        _turn_tokens = (chunk.prompt_tokens or 0) + (chunk.completion_tokens or 0)
                        final_usage["prompt_tokens"] += chunk.prompt_tokens or 0
                        final_usage["completion_tokens"] += chunk.completion_tokens or 0
                        _budget.use_tokens(_turn_tokens)
                    if chunk.credits_consumed is not None:
                        final_usage["api_credits"] = chunk.credits_consumed

                # --- 流结束，判断是否有工具调用 ---
                if not tool_calls_acc:
                    break  # 无工具调用，输出完成

                # 有工具调用 → 收割本轮文本为独立 block
                if turn_text:
                    _content_blocks.append({"type": "text", "text": turn_text})

                # 有工具调用 → 执行工具循环
                completed_calls = sorted(tool_calls_acc.values(), key=lambda x: x.get("id", ""))
                logger.info(
                    f"Tool calls detected | task={task_id} | turn={turn + 1} | "
                    f"tools={[c['name'] for c in completed_calls]}"
                )

                # 记录追加前的位置（供 Phase 5 增量提取精确切片，包含 assistant + tool results）
                _msg_pos_before_turn = len(messages)
                # 将 assistant 消息（含 tool_calls）塞进 messages
                assistant_tool_msg: Dict[str, Any] = {"role": "assistant", "content": turn_text or None}
                assistant_tool_msg["tool_calls"] = [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {"name": tc["name"], "arguments": tc["arguments"]},
                    }
                    for tc in completed_calls
                ]
                messages.append(assistant_tool_msg)

                # 通知前端：工具调用开始
                await ws_manager.send_to_task_or_user(
                    task_id, user_id,
                    build_tool_call(
                        task_id=task_id,
                        conversation_id=conversation_id,
                        message_id=message_id,
                        tool_calls=[{"name": tc["name"], "id": tc["id"]} for tc in completed_calls],
                        turn=turn + 1,
                    ),
                )

                # 执行工具（安全检查 + 并行/串行分批 + 传 messages 给 erp_agent）
                tool_results = await self._execute_tool_calls(
                    completed_calls, task_id, conversation_id, message_id,
                    user_id, turn + 1, messages=messages, budget=_budget,
                )

                # 工具结果塞进 messages + 更新上下文
                # 对 erp_agent: 提取原始结论推送 content_block_add
                from schemas.websocket import build_content_block_add
                for tc, result_text, is_error in tool_results:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": result_text,
                    })
                    tool_context.update_from_result(tc["name"], result_text, is_error)

                    # erp_agent 结果作为独立 content block 推送前端
                    if tc["name"] == "erp_agent" and not is_error:
                        _erp_display_text = getattr(
                            self, "_last_erp_display_text", None,
                        )
                        if _erp_display_text:
                            _erp_files = getattr(
                                self, "_last_erp_display_files", [],
                            )
                            tool_block = {
                                "type": "tool_result",
                                "tool_name": "erp_agent",
                                "text": _erp_display_text,
                                "files": _erp_files,
                            }
                            _content_blocks.append(tool_block)
                            await ws_manager.send_to_task_or_user(
                                task_id, user_id,
                                build_content_block_add(
                                    task_id=task_id,
                                    conversation_id=conversation_id,
                                    message_id=message_id,
                                    block=tool_block,
                                ),
                            )
                            self._last_erp_display_text = None
                            self._last_erp_display_files = []

                # Phase 5: fire-and-forget 增量记忆提取（精确切片：从 _msg_pos_before_turn 开始，含 assistant + tool results）
                import asyncio as _asyncio
                from services.handlers.session_memory import extract_incremental
                _new_turn_msgs = messages[_msg_pos_before_turn:]
                if _new_turn_msgs:
                    _asyncio.create_task(extract_incremental(_new_turn_msgs))

                # 层4+5+6: 旧工具结果归档 + 循环内摘要 + 分桶预算控制
                # 设计文档：docs/document/TECH_上下文工程重构.md §五
                from services.handlers.context_compressor import (
                    compact_stale_tool_results, compact_loop_with_summary,
                    enforce_tool_budget, enforce_history_budget_sync,
                )
                from core.config import get_settings as _get_settings
                _s = _get_settings()
                compact_stale_tool_results(messages, _s.context_tool_keep_turns)
                enforce_tool_budget(messages, _s.context_tool_token_budget)
                enforce_history_budget_sync(messages, _s.context_history_token_budget)
                if turn >= 3:
                    await compact_loop_with_summary(
                        messages, _s.context_max_tokens,
                        _s.context_loop_summary_trigger,
                    )

                # 继续循环，让 AI 看到工具结果
                logger.info(f"Tool turn {turn + 1} complete | task={task_id} | continuing loop")

            # 优雅降级：预算耗尽时追加提示或报错
            _stop = _budget.stop_reason
            _budget_error_sent = False
            if _stop:
                logger.warning(
                    f"Budget exhausted | task={task_id} | reason={_stop} | "
                    f"turns={_budget.turns_used} | tokens={_budget.tokens_used}"
                )
                if accumulated_text:
                    # 有部分结果 → 追加提示后正常返回
                    accumulated_text += f"\n\n> ⚠️ 已达到执行上限（{_STOP_MESSAGES.get(_stop, _stop)}），以上为部分结果。"
                else:
                    # 无结果 → 走 error 路径，阻止后续 on_complete
                    await self.on_error(
                        task_id=task_id,
                        error_code="BUDGET_EXCEEDED",
                        error_message=_STOP_MESSAGES.get(_stop, "执行超限，请稍后重试"),
                    )
                    _budget_error_sent = True

            # 6. 收割最后一轮文本 + 构建多块 result
            credits_consumed = self._calculate_credits(final_usage)

            # 最后一轮的 turn_text（循环 break 后未被收割）
            _final_turn_text = accumulated_text
            # 从 accumulated_text 中减去已收割到 _content_blocks 的部分
            _harvested = sum(
                len(b["text"]) for b in _content_blocks if b["type"] == "text"
            )
            _final_turn_text = accumulated_text[_harvested:]

            if _content_blocks:
                # 多块模式：有工具结果插入
                if _final_turn_text:
                    _content_blocks.append({"type": "text", "text": _final_turn_text})
                # 从 blocks 构建 result_parts
                from schemas.message import ToolResultPart
                from services.handlers.media_extractor import extract_media_parts
                result_parts: list = []
                for block in _content_blocks:
                    if block["type"] == "text":
                        result_parts.extend(extract_media_parts(block["text"]))
                    elif block["type"] == "tool_result":
                        result_parts.append(ToolResultPart(
                            tool_name=block["tool_name"],
                            text=block["text"],
                            files=block.get("files", []),
                        ))
            else:
                # 单块模式（无工具调用或工具未触发 content block）：兼容原逻辑
                from services.handlers.media_extractor import extract_media_parts
                result_parts = extract_media_parts(accumulated_text)

            # 合并工具执行过程中积累的 FilePart（沙盒 upload_file 生成）
            if self._pending_file_parts:
                result_parts.extend(self._pending_file_parts)
                self._pending_file_parts = []

            # 标记 LLM 阶段成功，持久化在 try 外执行
            # budget 超限已走 on_error 的不再走 on_complete
            _llm_succeeded = not _budget_error_sent
            _completion_args = {
                "task_id": task_id,
                "result": result_parts,
                "credits_consumed": credits_consumed,
                "thinking_content": accumulated_thinking or None,
            }

        except Exception as e:
            logger.error(
                f"Chat stream error | task_id={task_id} | "
                f"model={model_id} | error={str(e)}"
            )
            from core.error_classifier import classify_error
            classified = classify_error(e)

            # 只有模型相关错误才记入熔断器
            if classified.should_record_breaker:
                self._record_breaker_result(model_id, success=False, error=e)

            # 只有可重试错误才尝试换模型
            if classified.is_retryable:
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
            else:
                # 非可重试错误（DB/业务/未知）→ 直接报错，不触发换模型
                logger.warning(
                    f"Non-retryable error, skipping model retry | "
                    f"task_id={task_id} | category={classified.category.value} | "
                    f"error_code={classified.error_code}"
                )
                await self.on_error(
                    task_id=task_id,
                    error_code=classified.error_code,
                    error_message=str(e),
                )

        finally:
            if self._adapter:
                await self._adapter.close()
            # 清理截断暂存的大结果（请求级生命周期）
            from services.agent.tool_result_envelope import clear_persisted
            clear_persisted()

        # ── Boundary 2: 持久化（LLM 成功后执行，错误不触发重试）──
        if _llm_succeeded and _completion_args:
            try:
                await self.on_complete(**_completion_args)

                # 熔断器：记录成功
                self._record_breaker_result(model_id, success=True)

                # Fire-and-forget 后置任务
                elapsed_ms = int((_time.monotonic() - _start_time) * 1000)
                self._dispatch_post_tasks(
                    user_id=user_id, conversation_id=conversation_id,
                    text_content=text_content, accumulated_text=accumulated_text,
                    model_id=model_id, final_usage=final_usage,
                    elapsed_ms=elapsed_ms, retry_context=_retry_context,
                )
            except Exception as persist_err:
                logger.critical(
                    f"Persist phase failed after LLM success | "
                    f"task_id={task_id} | error={persist_err}"
                )
                try:
                    await self.on_error(
                        task_id=task_id,
                        error_code="INTERNAL_ERROR",
                        error_message=f"保存结果失败: {persist_err}",
                    )
                except Exception as err_err:
                    logger.critical(
                        f"on_error also failed | task_id={task_id} | error={err_err}"
                    )

    def _convert_content_parts_to_dicts(self, result):
        """转换 ContentPart 为字典（支持 Text/Image/Video/File/ToolResult）"""
        from schemas.message import FilePart, ImagePart, ToolResultPart, VideoPart
        dicts = []
        for p in result:
            if isinstance(p, TextPart):
                dicts.append({"type": "text", "text": p.text})
            elif isinstance(p, ImagePart):
                dicts.append({"type": "image", "url": p.url, "width": p.width, "height": p.height})
            elif isinstance(p, VideoPart):
                dicts.append({"type": "video", "url": p.url})
            elif isinstance(p, FilePart):
                dicts.append({
                    "type": "file", "url": p.url,
                    "name": p.name, "mime_type": p.mime_type,
                    "size": p.size,
                })
            elif isinstance(p, ToolResultPart):
                dicts.append({
                    "type": "tool_result", "tool_name": p.tool_name,
                    "text": p.text, "files": p.files,
                })
            elif isinstance(p, dict):
                dicts.append(p)
        return dicts

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
                org_id=self.org_id,
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
        self, task_id, message_id, conversation_id, user_id,
        model_id, content, params, metadata,
    ):
        """保存任务到数据库"""
        request_params = {
            "content": self._extract_text_content(content),
            "model_id": model_id,
            **self._serialize_params(params),
        }
        task_data = self._build_task_data(
            task_id=task_id, message_id=message_id,
            conversation_id=conversation_id, user_id=user_id,
            task_type="chat", status="running", model_id=model_id,
            request_params=request_params, metadata=metadata,
        )
        self.db.table("tasks").insert(task_data).execute()

