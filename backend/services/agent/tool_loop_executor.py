"""通用工具循环执行器（ToolLoopExecutor）

被 ERPAgent 和 ScheduledTaskAgent 共用的单一执行内核。
行为差异通过 LoopConfig / LoopStrategy / LoopHook 注入，零代码重复。

设计参考：OpenAI Agents SDK Runner / LangGraph StateGraph /
Anthropic Claude Code AgentLoop 的"配置 + 策略 + 中间件"模式。

每次 ERPAgent.execute() 或 ScheduledTaskAgent.execute() 构造一个新实例，
对应一次完整的工具循环生命周期，内部维护一个 ToolResultCache。
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

from loguru import logger

from services.agent.erp_agent_types import is_context_length_error
from services.agent.loop_hooks import LoopHook
from services.agent.loop_types import (
    HookContext, LoopConfig, LoopResult, LoopStrategy,
)
from services.agent.session_file_registry import SessionFileRegistry
from services.agent.tool_output import ToolOutput
from services.agent.tool_result_cache import ToolResultCache

# [FILE] 标记正则：沙盒 code_execute 输出的文件引用
# 格式：[FILE]{url}|{filename}|{mime_type}|{size}[/FILE]
_FILE_RE = re.compile(
    r"\[FILE\](?P<url>[^|]+)\|(?P<name>[^|]+)\|(?P<mime>[^|]+)\|(?P<size>\d+)\[/FILE\]"
)


class ToolLoopExecutor:
    """通用工具循环执行器

    构造参数：
        adapter:      LLM 适配器（必须实现 stream_chat）
        executor:     ToolExecutor 实例
        all_tools:    可被自动扩展的全量工具集（仅 strategy.enable_tool_expansion=True 时使用）
        config:       数值配置（max_turns / max_tokens / tool_timeout / ...）
        strategy:     结构性策略（exit_signals / enable_tool_expansion）
        hooks:        Hook 链（progress / audit / temporal validation / failure reflection）
    """

    def __init__(
        self,
        adapter: Any,
        executor: Any,
        all_tools: List[Dict[str, Any]],
        config: LoopConfig,
        strategy: LoopStrategy,
        hooks: List[LoopHook] = None,
        file_registry: SessionFileRegistry | None = None,
    ) -> None:
        self.adapter = adapter
        self.executor = executor
        self.all_tools = all_tools
        self.config = config
        self.strategy = strategy
        self.hooks: List[LoopHook] = list(hooks or [])
        # 会话级读工具缓存（key=tool_name+args_hash → result, TTL 5 分钟）
        self._cache = ToolResultCache()
        # 会话级文件注册表（供 ComputeAgent 按域查找 staging 文件）
        self._file_registry = file_registry or SessionFileRegistry()

    # ========================================
    # 工具循环主逻辑
    # ========================================

    async def run(
        self,
        messages: List[Dict[str, Any]],
        selected_tools: List[Dict[str, Any]],
        tools_called: List[str],
        hook_ctx: HookContext,
        budget: Any = None,
    ) -> LoopResult:
        """运行工具循环

        Args:
            messages: 对话消息列表（运行过程中会被 mutate）
            selected_tools: 当前可见工具集（自动扩展时会被 mutate）
            tools_called: 已调用工具名列表（运行过程中会被 mutate）
            hook_ctx: Hook 上下文（hook_ctx.messages/tools_called/selected_tools 会同步绑定）
            budget: ExecutionBudget 实例（可选）

        Returns:
            LoopResult: 包含合成文本、token 数、轮次、合成标记
        """
        # 绑定可变状态到 hook_ctx，让 hooks 能读到最新状态
        hook_ctx.messages = messages
        hook_ctx.selected_tools = selected_tools
        hook_ctx.tools_called = tools_called
        hook_ctx.budget = budget

        accumulated_text = ""
        total_tokens = 0
        is_llm_synthesis = False
        exit_via_ask_user = False
        empty_turns = 0
        recent_calls: List[str] = []
        context_recovery_used = False
        self._collected_files: List[Dict[str, Any]] = []

        for turn in range(self.config.max_turns):
            hook_ctx.turn = turn + 1

            should_continue = await self._pre_turn_checks(
                turn, total_tokens, hook_ctx,
            )
            if not should_continue:
                break

            try:
                tc_acc, turn_text, turn_tokens, _pt, _ct = await self._stream_one_turn(
                    messages, selected_tools,
                )
            except Exception as stream_err:
                if self._try_recover_from_context_error(
                    stream_err, messages, context_recovery_used, turn,
                ):
                    context_recovery_used = True
                    continue
                raise

            total_tokens += turn_tokens

            if not tc_acc:
                # 没有 tool_calls — 委托给 helper 决定是否中止/继续
                action, new_text, new_synth, empty_turns = (
                    self._handle_empty_response(
                        turn_text, tools_called, empty_turns, messages,
                    )
                )
                if action == "break":
                    accumulated_text = new_text
                    is_llm_synthesis = new_synth
                    break
                # action == "continue"
                continue

            completed = sorted(tc_acc.values(), key=lambda x: x.get("id", ""))

            if self._is_loop_detected(completed, recent_calls):
                break

            accumulated_text = await self._execute_tools(
                completed, selected_tools, turn_text, hook_ctx,
                turn_prompt_tokens=_pt, turn_completion_tokens=_ct,
            )

            # 退出信号工具命中
            if any(tc["name"] in self.strategy.exit_signals for tc in completed):
                has_ask_user = any(tc["name"] == "ask_user" for tc in completed)
                is_llm_synthesis = has_ask_user or bool(turn_text)
                exit_via_ask_user = has_ask_user
                break

            logger.info(
                f"ToolLoop turn {turn + 1} | "
                f"tools={[tc['name'] for tc in completed]}"
            )

        return await self._finalize(
            accumulated_text, total_tokens, turn,
            is_llm_synthesis, exit_via_ask_user, hook_ctx,
        )

    def _is_loop_detected(
        self,
        completed: List[Dict[str, Any]],
        recent_calls: List[str],
    ) -> bool:
        """循环检测：连续 3 次相同调用判定为死循环。

        副作用：往 recent_calls 追加本次调用 key。
        """
        call_key = "|".join(
            f"{tc['name']}:{hashlib.md5(tc['arguments'].encode()).hexdigest()[:6]}"
            for tc in completed
        )
        recent_calls.append(call_key)
        if len(recent_calls) >= 3 and len(set(recent_calls[-3:])) == 1:
            logger.warning(f"ToolLoop loop detected | call={call_key}")
            return True
        return False

    async def _finalize(
        self,
        accumulated_text: str,
        total_tokens: int,
        turn: int,
        is_llm_synthesis: bool,
        exit_via_ask_user: bool,
        hook_ctx: HookContext,
    ) -> LoopResult:
        """循环退出后的兜底文本 + 合成 hook 链 + 打包 LoopResult"""
        if not is_llm_synthesis:
            logger.warning(
                f"ToolLoop exited without synthesis | "
                f"raw_len={len(accumulated_text)} | turns={turn + 1}"
            )
            accumulated_text = self.config.no_synthesis_fallback_text

        # Hook 链：合成阶段（仅 LLM 合成且非 ask_user 退出）
        if is_llm_synthesis and not exit_via_ask_user and accumulated_text:
            for hook in self.hooks:
                accumulated_text = await hook.on_text_synthesis(
                    hook_ctx, accumulated_text,
                )

        return LoopResult(
            text=accumulated_text,
            total_tokens=total_tokens,
            turns=min(turn + 1, self.config.max_turns),
            is_llm_synthesis=is_llm_synthesis,
            exit_via_ask_user=exit_via_ask_user,
            collected_files=self._collected_files,
        )

    def _try_recover_from_context_error(
        self,
        stream_err: Exception,
        messages: List[Dict[str, Any]],
        already_recovered: bool,
        turn: int,
    ) -> bool:
        """上下文超限时尝试一次性压缩恢复。

        返回 True 表示已恢复（调用方应 continue），False 表示无法恢复（应 raise）。
        """
        if not is_context_length_error(stream_err) or already_recovered:
            return False

        from services.handlers.context_compressor import enforce_budget

        logger.warning(
            f"ToolLoop context_length_exceeded | "
            f"turn={turn + 1} | attempting recovery (one-shot)"
        )
        enforce_budget(
            messages,
            int(self.config.max_tokens * self.config.context_recovery_target),
        )
        messages.append({
            "role": "user",
            "content": (
                "上下文过长已自动压缩。请直接继续当前任务，"
                "不要重复已完成的步骤。"
            ),
        })
        return True

    def _handle_empty_response(
        self,
        turn_text: str,
        tools_called: List[str],
        empty_turns: int,
        messages: List[Dict[str, Any]],
    ) -> Tuple[str, str, bool, int]:
        """处理 LLM 没调任何工具的情况。

        返回 (action, accumulated_text, is_llm_synthesis, empty_turns)
        action: "break" → 应中止循环；"continue" → 应继续下一轮
        """
        # strategy.force_tool_use_first=True 且未调过工具 → 强制再走一轮
        if self.strategy.force_tool_use_first and not tools_called:
            empty_turns += 1
            logger.info(
                f"ToolLoop skip empty turn #{empty_turns} | "
                f"text={turn_text[:50] if turn_text else '(empty)'}"
            )
            if empty_turns >= 2:
                # 连续 2 次空响应：有文字就用，没文字就中止
                if turn_text:
                    return "break", turn_text, True, empty_turns
                return "break", "", False, empty_turns
            if turn_text:
                messages.append({"role": "assistant", "content": turn_text})
            return "continue", "", False, empty_turns

        # 允许直接回复 或 已调过工具 → 干净的合成结果
        return "break", turn_text, True, empty_turns

    async def _pre_turn_checks(
        self,
        turn: int,
        total_tokens: int,
        hook_ctx: HookContext,
    ) -> bool:
        """循环开头的护栏 + 上下文压缩 + 进度推送 hook。

        返回 True 表示当前 turn 可以继续；False 表示应 break 出循环。
        """
        from services.handlers.context_compressor import (
            enforce_budget, estimate_tokens,
        )

        # 全局时间预算检查
        if hook_ctx.budget and not hook_ctx.budget.check_or_log(
            f"turn={turn + 1}"
        ):
            return False

        # Token 预算检查
        if total_tokens >= self.config.max_tokens:
            logger.warning(
                f"ToolLoop token budget exceeded | used={total_tokens}"
            )
            return False

        # 上下文压缩：超过阈值时主动压缩 messages
        estimated = estimate_tokens(hook_ctx.messages)
        threshold = int(
            self.config.max_tokens * self.config.context_compression_threshold
        )
        if estimated > threshold:
            logger.info(
                f"ToolLoop context compress | tokens={estimated} | "
                f"threshold={threshold}"
            )
            enforce_budget(hook_ctx.messages, threshold)

        # Hook 链：每轮开始（进度推送等）
        for hook in self.hooks:
            await hook.on_turn_start(hook_ctx)

        return True

    async def _stream_one_turn(
        self,
        messages: List[Dict[str, Any]],
        selected_tools: List[Dict[str, Any]],
    ) -> Tuple[Dict[int, Dict[str, Any]], str, int, int, int]:
        """流式调用 LLM 一轮，聚合 tool_calls + content + tokens。

        返回 (tc_acc, turn_text, turn_tokens, prompt_tokens, completion_tokens)。
        """
        tc_acc: Dict[int, Dict[str, Any]] = {}
        turn_text = ""
        turn_tokens = 0
        _prompt_tokens = 0
        _completion_tokens = 0

        async for chunk in self.adapter.stream_chat(
            messages=messages, tools=selected_tools, temperature=0.1,
            thinking_mode=self.config.thinking_mode,
        ):
            if chunk.content:
                turn_text += chunk.content
            if chunk.tool_calls:
                for tc_delta in chunk.tool_calls:
                    idx = tc_delta.index
                    if idx not in tc_acc:
                        tc_acc[idx] = {"id": "", "name": "", "arguments": ""}
                    entry = tc_acc[idx]
                    if tc_delta.id:
                        entry["id"] = tc_delta.id
                    if tc_delta.name:
                        entry["name"] = tc_delta.name
                    if tc_delta.arguments_delta:
                        entry["arguments"] += tc_delta.arguments_delta
            if chunk.prompt_tokens or chunk.completion_tokens:
                _prompt_tokens = chunk.prompt_tokens or 0
                _completion_tokens = chunk.completion_tokens or 0
                turn_tokens = _prompt_tokens + _completion_tokens

        return tc_acc, turn_text, turn_tokens, _prompt_tokens, _completion_tokens

    # ========================================
    # 写操作确认（Phase 3 B5）
    # ========================================

    async def _request_user_confirm(
        self,
        tool_name: str,
        args: Dict[str, Any],
        tool_call_id: str,
        hook_ctx: HookContext,
    ) -> str | None:
        """向前端发起写操作确认，等待用户响应。

        Returns:
            None = 用户确认（继续执行）
            str = 拒绝/超时的提示文本（跳过执行）
        """
        if not hook_ctx.task_id:
            # headless 模式（无 WS 连接）：直接放行
            return None

        try:
            from schemas.websocket_builders import build_tool_confirm_request
            from services.websocket_manager import ws_manager

            # 构建参数摘要供前端展示
            args_summary = json.dumps(args, ensure_ascii=False)
            if len(args_summary) > 200:
                args_summary = args_summary[:200] + "..."

            await ws_manager.send_to_task_or_user(
                hook_ctx.task_id,
                hook_ctx.user_id,
                build_tool_confirm_request(
                    task_id=hook_ctx.task_id,
                    conversation_id=hook_ctx.conversation_id,
                    message_id="",
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    arguments=args,
                    description=f"AI 要执行写操作: {tool_name}",
                    safety_level="dangerous",
                    timeout=60,
                ),
            )

            approved = await ws_manager.wait_for_confirm(
                tool_call_id, timeout=60.0,
            )
            if approved:
                logger.info(
                    f"Tool confirm approved | tool={tool_name} | "
                    f"tool_call_id={tool_call_id}"
                )
                return None  # 继续执行

            logger.info(
                f"Tool confirm rejected/timeout | tool={tool_name} | "
                f"tool_call_id={tool_call_id}"
            )
            return (
                f"⚠ 用户拒绝或超时未确认写操作 {tool_name}。"
                f"请用 ask_user 告知用户操作未执行，询问是否需要重新确认。"
            )
        except Exception as e:
            logger.warning(
                f"Tool confirm error | tool={tool_name} | error={e}"
            )
            # 确认机制异常时放行（fail-open），不阻塞工具执行
            return None

    # ========================================
    # 单轮工具执行
    # ========================================

    async def _execute_tools(
        self,
        completed: List[Dict[str, Any]],
        selected_tools: List[Dict[str, Any]],
        turn_text: str,
        hook_ctx: HookContext,
        turn_prompt_tokens: int = 0,
        turn_completion_tokens: int = 0,
    ) -> str:
        """执行一轮工具调用（含退出信号短路、缓存、超时、hook 触发、自动扩展）"""
        messages = hook_ctx.messages
        tools_called = hook_ctx.tools_called

        asst_msg: Dict[str, Any] = {
            "role": "assistant", "content": turn_text or None,
        }
        asst_msg["tool_calls"] = [
            {
                "id": tc["id"], "type": "function",
                "function": {"name": tc["name"], "arguments": tc["arguments"]},
            }
            for tc in completed
        ]
        messages.append(asst_msg)

        accumulated = turn_text
        for tc in completed:
            tool_name = tc["name"]
            tools_called.append(tool_name)

            # 退出信号工具：不实际执行，记 OK 后短路
            if tool_name in self.strategy.exit_signals:
                try:
                    args = json.loads(tc["arguments"])
                except json.JSONDecodeError:
                    args = {}
                if tool_name == "route_to_chat":
                    accumulated = turn_text if turn_text else ""
                else:  # ask_user
                    accumulated = args.get("message", turn_text)
                messages.append({
                    "role": "tool", "tool_call_id": tc["id"], "content": "OK",
                })
                break

            # JSON 解析失败 → 错误信息回灌给 LLM
            try:
                args = json.loads(tc["arguments"]) if tc["arguments"] else {}
            except json.JSONDecodeError as e:
                logger.warning(
                    f"ToolLoop bad JSON | tool={tool_name} | error={e}"
                )
                result = f"工具参数JSON格式错误: {e}，请检查参数格式"
                messages.append({
                    "role": "tool", "tool_call_id": tc["id"], "content": result,
                })
                accumulated = result
                continue

            # ── 安全检查：DANGEROUS 工具需用户确认 ──
            from config.chat_tools import SafetyLevel, get_safety_level
            safety = get_safety_level(tool_name)
            if safety == SafetyLevel.DANGEROUS:
                confirm_result = await self._request_user_confirm(
                    tool_name, args, tc["id"], hook_ctx,
                )
                if confirm_result is not None:
                    # 用户拒绝或超时 → 写入拒绝信息，继续下一个工具
                    messages.append({
                        "role": "tool", "tool_call_id": tc["id"],
                        "content": confirm_result,
                    })
                    accumulated = confirm_result
                    continue

            # ── 参数校验网关：过滤幻觉参数 + 必填检查 ──
            from services.agent.tool_args_validator import validate_tool_args
            args, validation_error = validate_tool_args(
                tool_name, args, selected_tools,
            )
            if validation_error:
                messages.append({
                    "role": "tool", "tool_call_id": tc["id"],
                    "content": validation_error,
                })
                accumulated = validation_error
                continue

            # Hook 链：单工具执行前
            for hook in self.hooks:
                await hook.on_tool_start(hook_ctx, tool_name, args)

            from services.agent.tool_loop_helpers import invoke_tool_with_cache
            result, audit_status, is_cached, elapsed_ms = (
                await invoke_tool_with_cache(
                    self.executor, self._cache, tool_name, args,
                    hook_ctx.budget, self.config.tool_timeout,
                )
            )

            # ── 统一后处理：类型归一化 → 文件收集 → 截断 → 入 messages ──
            now_iso = datetime.now(timezone.utc).isoformat()

            # Step 1: 归一化为 content 字符串
            if isinstance(result, ToolOutput):
                _warnings = result.validate()
                if _warnings:
                    logger.warning(
                        f"ToolOutput validation | tool={tool_name} | "
                        f"issues={_warnings}",
                    )
                content = result.to_tool_content()
                is_truncated = False

                # ToolOutput 专有：file_ref 注册 + collected_files 透传
                if result.file_ref:
                    self._file_registry.register(
                        result.source or tool_name, tool_name, result.file_ref,
                    )
                if getattr(result, "collected_files", None):
                    self._collected_files.extend(result.collected_files)
            else:
                content = result or ""
                is_truncated = False

            # Step 2: [FILE] 标记提取（统一处理，不分类型）
            if content and "[FILE]" in content:
                for m in _FILE_RE.finditer(content):
                    self._collected_files.append({
                        "url": m.group("url"),
                        "name": m.group("name"),
                        "mime_type": m.group("mime"),
                        "size": int(m.group("size")),
                    })
                # LLM 上下文不暴露 URL（防止 LLM 幻觉篡改域名）
                # 下载链接由 collected_files → FilePart 文件卡片提供
                content = _FILE_RE.sub(
                    lambda m: f"📎 文件已生成: {m.group('name')}", content,
                )

            # Step 3: 截断防爆（ToolOutput 已结构化不截断，str 需要）
            if not isinstance(result, ToolOutput):
                from services.agent.tool_result_envelope import wrap_for_erp_agent
                is_truncated = len(content) > 3000 if content else False
                _tight = hook_ctx.budget.is_tight if hook_ctx.budget else False
                content = wrap_for_erp_agent(tool_name, content, tight=_tight)

            # Step 4: 入 messages
            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "timestamp": now_iso,
                "content": content,
            })
            accumulated = content

            # Hook 链：单工具执行后（审计 + 失败反思等）
            for hook in self.hooks:
                await hook.on_tool_end(
                    hook_ctx, tool_name, args, result,
                    audit_status, elapsed_ms,
                    is_cached, is_truncated, tc["id"],
                    turn_prompt_tokens=turn_prompt_tokens,
                    turn_completion_tokens=turn_completion_tokens,
                )

            # ── 打断检查点：用户在工具执行期间发了新消息 ──
            if hook_ctx.task_id:
                from services.websocket_manager import ws_manager
                _steer = ws_manager.check_steer(hook_ctx.task_id)
                if _steer:
                    logger.info(
                        f"ToolLoop steer | task={hook_ctx.task_id} | "
                        f"msg={_steer[:50]}"
                    )
                    # 跳过剩余工具，注入用户消息
                    remaining = completed[completed.index(tc) + 1:]
                    for r_tc in remaining:
                        messages.append({
                            "role": "tool",
                            "tool_call_id": r_tc["id"],
                            "content": "⚠ 用户发送了新消息，跳过此工具调用。",
                        })
                    messages.append({"role": "user", "content": _steer})
                    break

            # 自动扩展：模型调了隐藏工具 → 从全量列表动态注入
            if self.strategy.enable_tool_expansion:
                from services.agent.tool_loop_helpers import inject_tool
                inject_tool(
                    tool_name, selected_tools, self.all_tools,
                    self.strategy.exit_signals, hook_ctx.org_id,
                )

        return accumulated
