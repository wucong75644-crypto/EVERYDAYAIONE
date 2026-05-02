"""
聊天消息处理器

处理流式聊天消息生成 + 工具循环执行。
"""

import asyncio
import json
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
    build_content_block_add,
)
from services.adapters.factory import DEFAULT_MODEL_ID
from services.handlers.base import BaseHandler, TaskMetadata
from services.handlers.chat_context_mixin import ChatContextMixin
from services.handlers.chat_generate_mixin import ChatGenerateMixin
from services.handlers.chat_stream_support_mixin import ChatStreamSupportMixin
from services.handlers.chat_tool_mixin import ChatToolMixin, accumulate_tool_call_delta
from services.websocket_manager import ws_manager

# 优雅降级提示消息（去领域化，有 Final Synthesis Turn 后仅作兜底）
_STOP_MESSAGES = {
    "wrap_up_budget": "接近执行上限，正在总结当前进展。",
    "max_turns": "已达到单次对话工具调用上限。",
    "max_tokens": "本次任务消耗的资源过大，请缩小范围或分步进行。",
    "wall_timeout": "任务耗时过长，请稍后重试。",
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
                permission_mode=params.get("permission_mode", "auto"),
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

    # ================================================================
    # ask_user 冻结/恢复（设计文档：TECH_AI主动沟通与打断机制.md §4.2）
    # ================================================================

    async def _freeze_for_ask_user(
        self,
        ask_info: Dict[str, Any],
        messages: List[Dict[str, Any]],
        task_id: str,
        conversation_id: str,
        message_id: str,
        user_id: str,
        model_id: str,
        content_blocks: List[Dict[str, Any]],
        tool_context_state: Dict[str, Any],
        budget_snapshot: Dict[str, Any],
    ) -> None:
        """冻结当前工具循环状态到 DB，WS 推送追问给前端"""
        import json as _json

        interaction_id = str(uuid.uuid4())
        question = ask_info["message"]

        # 序列化 messages 和循环快照
        # file_registry / dag_progress 为新增字段（§15.2 老格式兼容：
        # _restore_from_pending 用 .get() 取，老格式返回空列表/None）
        loop_snapshot = {
            "content_blocks": content_blocks,
            "tool_context_state": tool_context_state,
            "model_id": model_id,
            "budget_snapshot": budget_snapshot,
            "file_registry": [],  # 由 ToolLoopExecutor._file_registry.to_snapshot() 填充
            "dag_progress": None,  # DAG 模式由 ERPAgent 填充
        }

        try:
            self.db.table("pending_interaction").insert({
                "id": interaction_id,
                "conversation_id": conversation_id,
                "user_id": user_id,
                "org_id": self.org_id,
                "frozen_messages": _json.dumps(messages, ensure_ascii=False),
                "question": question,
                "source": "chat",
                "tool_call_id": ask_info["tool_call_id"],
                "loop_snapshot": _json.dumps(loop_snapshot, ensure_ascii=False),
                "status": "pending",
            }).execute()
        except Exception as e:
            logger.error(f"Failed to freeze pending_interaction | error={e}")
            return

        # WS 推送追问请求给前端
        from schemas.websocket_builders import build_ask_user_request
        await ws_manager.send_to_task_or_user(
            task_id, user_id,
            build_ask_user_request(
                task_id=task_id,
                conversation_id=conversation_id,
                message_id=message_id,
                interaction_id=interaction_id,
                question=question,
                source="chat",
            ),
        )
        logger.info(
            f"ask_user frozen | interaction={interaction_id} | "
            f"conv={conversation_id} | question={question[:50]}"
        )

    def _check_pending_interaction(
        self, conversation_id: str,
    ) -> Optional[Dict[str, Any]]:
        """检查是否有待恢复的 pending interaction"""
        try:
            result = self.db.table("pending_interaction") \
                .select("*") \
                .eq("conversation_id", conversation_id) \
                .eq("status", "pending") \
                .maybe_single() \
                .execute()
            data = result.data
            # 类型校验：排除 MagicMock 等非法数据
            if data and isinstance(data, dict) and "frozen_messages" in data:
                return data
            return None
        except Exception as e:
            logger.warning(f"Failed to check pending_interaction | error={e}")
            return None

    def _restore_from_pending(
        self,
        pending: Dict[str, Any],
        user_answer: str,
    ) -> tuple:
        """从 pending interaction 恢复冻结状态

        Returns:
            (messages, content_blocks, tool_context_state, budget_snapshot,
             file_registry, dag_progress)
        """
        import json as _json

        # 反序列化 frozen_messages
        frozen = pending["frozen_messages"]
        messages = _json.loads(frozen) if isinstance(frozen, str) else frozen

        # 用户回答作为 tool_result 注入（和 Claude 一致）
        messages.append({
            "role": "tool",
            "tool_call_id": pending["tool_call_id"],
            "content": f"用户回答: {user_answer}",
        })

        # 恢复循环快照
        snapshot_raw = pending.get("loop_snapshot", "{}")
        snapshot = _json.loads(snapshot_raw) if isinstance(snapshot_raw, str) else snapshot_raw

        content_blocks = snapshot.get("content_blocks", [])
        tool_context_state = snapshot.get("tool_context_state", {})
        budget_snapshot = snapshot.get("budget_snapshot", {})

        # 恢复 SessionFileRegistry（§15.2 老格式兼容：空列表 → 空 Registry）
        from services.agent.session_file_registry import SessionFileRegistry
        file_registry = SessionFileRegistry.from_snapshot(
            snapshot.get("file_registry", []),
        )

        # 恢复 DAG 进度（§15.2 老格式兼容：None → 无 DAG 进度）
        dag_progress = snapshot.get("dag_progress")

        # 原子标记为已恢复（防并发：只有 status=pending 才能抢到）
        try:
            res = self.db.table("pending_interaction") \
                .update({"status": "resumed"}) \
                .eq("id", pending["id"]) \
                .eq("status", "pending") \
                .execute()
            if not res.data:
                logger.warning(f"Pending already resumed by another request | id={pending['id']}")
        except Exception as e:
            logger.warning(f"Failed to mark pending as resumed | error={e}")

        return (
            messages, content_blocks, tool_context_state,
            budget_snapshot, file_registry, dag_progress,
        )

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
        permission_mode: str = "auto",
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
        _thinking_start_time: Optional[float] = None  # 首个 thinking chunk 的时间戳
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
                get_tools_by_names, get_tool_system_prompt,
            )
            tool_prompt = get_tool_system_prompt()
            if tool_prompt:
                messages.append({"role": "system", "content": tool_prompt})

            # 4.5 权限模式初始化（对齐 Claude Code ToolPermissionContext）
            # 兼容旧参数：plan_mode=True → permission_mode="plan"
            if permission_mode is True or permission_mode == "true":
                permission_mode = "plan"
            elif permission_mode is False or permission_mode == "false" or not permission_mode:
                permission_mode = "auto"

            from services.handlers.permission_mode import PermissionMode
            perm = PermissionMode(mode=permission_mode)
            logger.info(f"Permission mode | mode={perm.mode.value}")

            # 首轮模式提示词注入（full reminder）
            _mode_prompt = perm.get_reminder(turn=0)
            if _mode_prompt:
                messages.append({"role": "system", "content": _mode_prompt})

            # 5. 按模式加载工具（plan 模式移除执行类工具）
            from config.chat_tools import get_tools_for_mode
            core_tools = get_tools_for_mode(perm.mode.value, org_id=self.org_id)

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

            # 6.5 trace_id + Langfuse trace（v6 可观测性）
            from services.agent.observability import set_trace_id
            set_trace_id(task_id)
            logger.bind(trace_id=task_id)
            from services.agent.observability.langfuse_integration import create_trace
            create_trace(
                name="chat_request", user_id=user_id,
                session_id=conversation_id,
            )

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

            # 设置 staging 分流目录（用户级隔离，工具结果超阈值时落盘到此目录）
            from services.agent.tool_result_envelope import set_staging_dir
            from core.workspace import resolve_staging_dir
            set_staging_dir(resolve_staging_dir(
                _s.file_workspace_root, user_id, self.org_id, conversation_id,
            ))

            # ── ask_user 恢复检查：有 pending 则替换 messages ──
            _pending = self._check_pending_interaction(conversation_id)
            if _pending:
                text_content = self._extract_text_content(content)
                _restored = self._restore_from_pending(_pending, text_content)
                messages, _content_blocks, _tc_state, _bs, _file_reg, _dag_prog = _restored
                # 恢复 tool_context discovered_tools
                tool_context.discovered_tools = set(_tc_state.get("discovered_tools", []))
                # 恢复预算消耗
                _budget.use_tokens(_bs.get("tokens_used", 0))
                for _ in range(_bs.get("turns_used", 0)):
                    _budget.use_turn()
                # 上下文压缩（frozen_messages 可能较大）
                from services.handlers.context_compressor import (
                    enforce_tool_budget, enforce_history_budget_sync,
                )
                enforce_tool_budget(messages, _s.context_tool_token_budget)
                enforce_history_budget_sync(messages, _s.context_history_token_budget)
                logger.info(
                    f"Resumed from pending | conv={conversation_id} | "
                    f"frozen_msgs={len(messages)} | budget_turns={_bs.get('turns_used', 0)}"
                )

                # ── plan 模式恢复：用户确认后解锁执行工具 ──
                if perm.is_plan:
                    restored_mode = perm.exit_plan()
                    # 重建 core_tools（现在包含 erp_agent）
                    core_tools = get_tools_for_mode(perm.mode.value, org_id=self.org_id)
                    # Google Search 重新追加
                    if needs_google_search and hasattr(self._adapter, 'supports_google_search') and self._adapter.supports_google_search:
                        core_tools.append(self._adapter.create_google_search_tool())
                    logger.info(
                        f"Plan confirmed → execution unlocked | "
                        f"restored_mode={restored_mode.value} | "
                        f"tools={[t['function']['name'] for t in core_tools]}"
                    )

            # ── 打断监听注册 ──
            ws_manager.register_steer_listener(task_id)

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

                # 权限模式：exit attachment（plan 确认后一次性注入）
                if perm.need_exit_attachment:
                    messages.append({"role": "system", "content": perm.consume_exit_attachment()})

                # 权限模式：周期性 sparse/full reminder
                if turn > 0:
                    _mode_reminder = perm.get_reminder(turn)
                    if _mode_reminder:
                        messages.append({"role": "system", "content": _mode_reminder})

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
                        if _thinking_start_time is None:
                            _thinking_start_time = _time.monotonic()
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

                # 有工具调用 → 中间叙述作 TextPart 按时序插入 content_blocks
                # "让我查看文件..."等中间文字进正文流，thinking 只留纯推理
                if turn_text:
                    _text_block = {"type": "text", "text": turn_text}
                    _content_blocks.append(_text_block)
                    try:
                        await ws_manager.send_to_task_or_user(
                            task_id, user_id,
                            build_content_block_add(
                                task_id=task_id,
                                conversation_id=conversation_id,
                                message_id=message_id,
                                block=_text_block,
                            ),
                        )
                    except Exception as _text_err:
                        logger.warning(f"text block push failed | task={task_id} | {_text_err}")

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

                # 推送 tool_step(running) 内容块 + 记录开始时间
                # thinking 只留纯推理，工具调用详情通过 ToolStepCard 内联展示
                _tool_step_start_times: Dict[str, float] = {}
                for tc in completed_calls:
                    _tool_step: Dict[str, Any] = {
                        "type": "tool_step",
                        "tool_name": tc["name"],
                        "tool_call_id": tc["id"],
                        "status": "running",
                    }
                    if tc["name"] == "code_execute":
                        try:
                            _ce_args = json.loads(tc.get("arguments", "{}"))
                            _ce_code = _ce_args.get("code", "")[:2000]
                            if _ce_code:
                                _tool_step["code"] = _ce_code
                        except Exception:
                            pass
                    _content_blocks.append(_tool_step)
                    _tool_step_start_times[tc["id"]] = _time.monotonic()
                    try:
                        await ws_manager.send_to_task_or_user(
                            task_id, user_id,
                            build_content_block_add(
                                task_id=task_id,
                                conversation_id=conversation_id,
                                message_id=message_id,
                                block=_tool_step,
                            ),
                        )
                    except Exception as _step_err:
                        logger.warning(f"tool_step push failed | tc={tc['id']} | {_step_err}")

                # 执行工具（安全检查 + 并行/串行分批 + 传 messages 给 erp_agent）
                tool_results = await self._execute_tool_calls(
                    completed_calls, task_id, conversation_id, message_id,
                    user_id, turn + 1, messages=messages, budget=_budget,
                )

                # 工具结果塞进 messages + 更新上下文 + 更新 tool_step 状态
                from services.agent.agent_result import AgentResult
                from services.file_executor import FileReadResult
                _pending_image_urls: List[str] = []  # 图片多模态：收集待注入的 image_url
                for tc, result, is_error in tool_results:
                    if isinstance(result, AgentResult):
                        content = result.to_message_content()
                        tool_context.update_from_result(
                            tc["name"], result.summary, is_error,
                        )
                        # 子Agent thinking持久化：追加到accumulated_thinking
                        if result.thinking_text:
                            accumulated_thinking += result.thinking_text
                    elif isinstance(result, FileReadResult):
                        # 图片多模态：text 作为 tool result，image_url 延迟注入
                        content = result.text
                        tool_context.update_from_result(
                            tc["name"], result.text, is_error,
                        )
                        if result.type == "image" and result.image_url:
                            _pending_image_urls.append(result.image_url)
                    else:
                        content = result
                        tool_context.update_from_result(
                            tc["name"], result, is_error,
                        )
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": content,
                    })
                    # 更新 _content_blocks 中对应 tool_step 的状态（持久化用）
                    _tc_id = tc["id"]
                    _tc_start = _tool_step_start_times.get(_tc_id)
                    _elapsed = int((_time.monotonic() - _tc_start) * 1000) if _tc_start else 0
                    for _blk in _content_blocks:
                        if _blk.get("type") == "tool_step" and _blk.get("tool_call_id") == _tc_id:
                            _blk["status"] = "error" if is_error else "completed"
                            _blk["elapsed_ms"] = _elapsed
                            if isinstance(result, AgentResult):
                                _blk["summary"] = (result.summary or "")[:500]
                            elif isinstance(result, FileReadResult):
                                _blk["summary"] = result.text[:500]
                            elif isinstance(result, str):
                                _blk["summary"] = result[:500]
                            break

                    # 工具结果日志已通过 ToolStepCard 的 summary 字段展示，
                    # 不再重复写入 thinking（保持 thinking 只含纯 AI 推理）

                # ── 图片多模态注入 ──
                # OpenAI 兼容 API 的 tool result content 只能是 string，
                # 图片需要通过追加 user 消息的方式让模型"看到"。
                if _pending_image_urls:
                    img_parts: List[Dict[str, Any]] = [
                        {"type": "text", "text": "[系统：以下是 file_read 返回的图片]"},
                    ]
                    for img_url in _pending_image_urls:
                        img_parts.append({
                            "type": "image_url",
                            "image_url": {"url": img_url},
                        })
                    messages.append({"role": "user", "content": img_parts})

                # ── 文件块嵌入 content 流 + 实时推送前端 ──
                # 设计文档：TECH_内容块混排渲染架构.md §6.2
                # 通过 content_block_add 立即推送，前端流式阶段即可渲染占位符
                if self._pending_file_parts:
                    _dims = getattr(self, "_image_dims", {})
                    for _fp in self._pending_file_parts:
                        if _fp.mime_type.startswith("image/"):
                            _w, _h = _dims.get(_fp.name, (None, None))
                            _block = {
                                "type": "image", "url": _fp.url,
                                "alt": _fp.name,
                                **({"width": _w, "height": _h} if _w else {}),
                            }
                        else:
                            _block = {
                                "type": "file", "url": _fp.url,
                                "name": _fp.name, "mime_type": _fp.mime_type,
                                "size": _fp.size,
                            }
                        _content_blocks.append(_block)
                        # 实时推送：前端立即渲染（图片→骨架屏，文件→卡片）
                        await ws_manager.send_to_task_or_user(
                            task_id, user_id,
                            build_content_block_add(
                                task_id=task_id,
                                conversation_id=conversation_id,
                                message_id=message_id,
                                block=_block,
                            ),
                        )
                    logger.info(
                        f"File blocks pushed to frontend | "
                        f"count={len(self._pending_file_parts)} | task={task_id}"
                    )
                    self._pending_file_parts.clear()

                # ── FormBlock 推送（复用 _pending_file_parts 模式） ──
                _pending_form = getattr(self, "_pending_form_block", None)
                if _pending_form:
                    self._pending_form_block = None
                    _content_blocks.append(_pending_form)
                    await ws_manager.send_to_task_or_user(
                        task_id, user_id,
                        build_content_block_add(
                            task_id=task_id,
                            conversation_id=conversation_id,
                            message_id=message_id,
                            block=_pending_form,
                        ),
                    )
                    # 追加提示文字（消息不能为空）
                    _form_hint = "请在上方表单中确认信息后点击提交。"
                    accumulated_text += _form_hint
                    await ws_manager.send_to_task_or_user(
                        task_id, user_id,
                        build_message_chunk(
                            task_id=task_id,
                            conversation_id=conversation_id,
                            message_id=message_id,
                            chunk=_form_hint,
                        ),
                    )
                    logger.info(f"FormBlock pushed + persisted | task={task_id}")
                    break  # 停止工具循环，等用户确认表单

                # ── ask_user 冻结检测 ──
                _ask_info = getattr(self, "_ask_user_pending", None)
                if _ask_info:
                    self._ask_user_pending = None
                    # 冻结当前状态到 DB
                    await self._freeze_for_ask_user(
                        ask_info=_ask_info,
                        messages=messages,
                        task_id=task_id,
                        conversation_id=conversation_id,
                        message_id=message_id,
                        user_id=user_id,
                        model_id=model_id,
                        content_blocks=_content_blocks,
                        tool_context_state={
                            "discovered_tools": list(tool_context.discovered_tools),
                        },
                        budget_snapshot={
                            "turns_used": _budget.turns_used,
                            "tokens_used": _budget.tokens_used,
                        },
                    )
                    # 追问文本作为本轮输出
                    _ask_text = _ask_info["message"]
                    _separator = "\n\n" if accumulated_text else ""
                    accumulated_text += _separator + _ask_text
                    await ws_manager.send_to_task_or_user(
                        task_id, user_id,
                        build_message_chunk(
                            task_id=task_id,
                            conversation_id=conversation_id,
                            message_id=message_id,
                            chunk=_separator + _ask_text,
                        ),
                    )
                    break

                # ── 打断检查点：用户在工具执行期间发了新消息 ──
                _steer_msg = ws_manager.check_steer(task_id)
                if _steer_msg:
                    logger.info(
                        f"Steer detected | task={task_id} | msg={_steer_msg[:50]}"
                    )
                    # 注入用户新消息，让 AI 下一轮看到并切换话题
                    messages.append({"role": "user", "content": _steer_msg})
                    # 不 break — 继续循环让 AI 基于新消息决策

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

            # 优雅降级：预算耗尽时 Final Synthesis Turn
            _stop = _budget.stop_reason
            _budget_error_sent = False
            if _stop:
                logger.warning(
                    f"Budget exhausted | task={task_id} | reason={_stop} | "
                    f"turns={_budget.turns_used} | tokens={_budget.tokens_used}"
                )

                # Final Synthesis Turn — 调 LLM 生成总结
                from services.agent.stop_policy import synthesize_wrap_up
                _synthesis = await synthesize_wrap_up(
                    adapter=self._adapter,
                    messages=messages,
                    content_blocks=_content_blocks,
                    reason=_STOP_MESSAGES.get(_stop, _stop),
                )

                if _synthesis:
                    accumulated_text = _synthesis
                    # 多块模式下需要追加到 _content_blocks 才能被渲染
                    if _content_blocks:
                        _content_blocks.append({"type": "text", "text": _synthesis})
                elif accumulated_text:
                    # 合成失败但有部分结果 → 追加提示
                    accumulated_text += f"\n\n> ⚠️ 已达到执行上限（{_STOP_MESSAGES.get(_stop, _stop)}），以上为部分结果。"
                else:
                    # 完全无结果 → hard_fail
                    await self.on_error(
                        task_id=task_id,
                        error_code="BUDGET_EXCEEDED",
                        error_message=_STOP_MESSAGES.get(_stop, "执行超限，请稍后重试"),
                    )
                    _budget_error_sent = True

            # 6. 收割最后一轮文本 + 构建多块 result
            credits_consumed = self._calculate_credits(final_usage)

            # 最后一轮的 turn_text（循环 break 后未被收割）
            # 中间轮文本已作 TextPart 插入 _content_blocks，这里只取最后一轮的实际回答
            _final_turn_text = turn_text

            if _content_blocks:
                # 多块模式：有工具结果插入（text / image / file 混排）
                if _final_turn_text:
                    _content_blocks.append({"type": "text", "text": _final_turn_text})
                # 从 blocks 构建 result_parts
                # 设计文档：TECH_内容块混排渲染架构.md §6.3
                from schemas.message import (
                    ToolResultPart, ToolStepPart as _ToolStepPart,
                    ImagePart, FilePart,
                )
                from services.handlers.media_extractor import extract_media_parts
                result_parts: list = []
                for block in _content_blocks:
                    if block["type"] == "text":
                        result_parts.extend(extract_media_parts(block["text"]))
                    elif block["type"] == "tool_step":
                        result_parts.append(_ToolStepPart(
                            tool_name=block["tool_name"],
                            tool_call_id=block["tool_call_id"],
                            status=block.get("status", "completed"),
                            summary=block.get("summary"),
                            code=block.get("code"),
                            output=block.get("output"),
                            elapsed_ms=block.get("elapsed_ms"),
                        ))
                    elif block["type"] == "tool_result":
                        result_parts.append(ToolResultPart(
                            tool_name=block["tool_name"],
                            text=block["text"],
                            files=block.get("files", []),
                        ))
                    elif block["type"] == "image":
                        result_parts.append(ImagePart(
                            url=block["url"], alt=block.get("alt"),
                            width=block.get("width"),
                            height=block.get("height"),
                        ))
                    elif block["type"] == "file":
                        result_parts.append(FilePart(
                            url=block["url"], name=block["name"],
                            mime_type=block["mime_type"],
                            size=block.get("size"),
                        ))
                    elif block["type"] == "form":
                        from schemas.message import FormPart
                        result_parts.append(FormPart(**block))
            else:
                # 单块模式（无工具调用）：兼容原逻辑
                from services.handlers.media_extractor import extract_media_parts
                result_parts = extract_media_parts(accumulated_text)
                self._pending_file_parts = []

            # 7. Thinking 持久化：作为 content 首元素（不再仅存 generation_params）
            if accumulated_thinking:
                from schemas.message import ThinkingPart
                _thinking_duration = (
                    int((_time.monotonic() - _thinking_start_time) * 1000)
                    if _thinking_start_time else None
                )
                result_parts.insert(0, ThinkingPart(
                    text=accumulated_thinking,
                    duration_ms=_thinking_duration,
                ))

            # 标记 LLM 阶段成功，持久化在 try 外执行
            # budget 超限已走 on_error 的不再走 on_complete
            _llm_succeeded = not _budget_error_sent

            # 构建工具执行摘要（turns > 1 = 至少执行过一轮工具调用）
            # 不能用 _content_blocks：LLM 可能调工具时不输出文字（遵守"禁止输出思考过程"规则）
            _tool_digest = None
            if _budget.turns_used > 1:
                from services.handlers.tool_digest import build_tool_digest
                try:
                    _tool_digest = build_tool_digest(messages, conversation_id)
                except Exception as _digest_err:
                    logger.warning(f"Tool digest build failed | error={_digest_err}")

            _completion_args = {
                "task_id": task_id,
                "result": result_parts,
                "credits_consumed": credits_consumed,
                "tool_digest": _tool_digest,
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
                    permission_mode=permission_mode,
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
            # 清理打断监听
            ws_manager.unregister_steer_listener(task_id)
            # 清理截断暂存的大结果（请求级生命周期）
            from services.agent.tool_result_envelope import clear_persisted, clear_staging_dir
            clear_persisted()
            clear_staging_dir()
            # Staging 文件清理（fire-and-forget，文件级 TTL + 容量兜底）
            asyncio.create_task(
                _async_cleanup_staging(conversation_id, user_id, self.org_id)
            )

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
        """转换 ContentPart 为字典

        所有 Pydantic BaseModel 子类统一用 model_dump()，
        不再逐类型手写字段映射——新增类型不会被静默跳过。
        """
        from pydantic import BaseModel
        dicts = []
        for p in result:
            if isinstance(p, BaseModel):
                dicts.append(p.model_dump(exclude_none=True))
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
        tool_digest: Optional[dict] = None,
    ) -> Message:
        """完成回调（调用基类通用流程）"""
        return await self._handle_complete_common(
            task_id, result, credits_consumed,
            tool_digest=tool_digest,
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


async def _async_cleanup_staging(
    conversation_id: str,
    user_id: str = "",
    org_id: str | None = None,
) -> None:
    """会话级 staging 文件清理（fire-and-forget，文件级 TTL + 容量兜底）

    设计文档：docs/document/TECH_data_query工具设计.md §九
    - 不传 registry → 纯 TTL 模式（24h 孤儿清理 + _tmp_ 残留清理）
    - 文件 IO 在 executor 中执行，不阻塞事件循环
    """
    from core.config import get_settings
    from core.workspace import resolve_staging_dir
    from services.staging_cleaner import cleanup_staging

    try:
        settings = get_settings()
        staging_dir = resolve_staging_dir(
            settings.file_workspace_root, user_id, org_id, conversation_id,
        )
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            lambda: cleanup_staging(
                staging_dir,
                registry=None,
                ttl_seconds=settings.staging_file_ttl_seconds,
                max_size_mb=settings.staging_max_size_mb,
            ),
        )
    except Exception as e:
        logger.debug(f"Staging cleanup failed | conv={conversation_id} | error={e}")

