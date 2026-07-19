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
from typing import Any, Dict, List, Tuple

from loguru import logger

from services.agent.erp_agent_types import is_context_length_error
from services.agent.loop_hooks import LoopHook
from services.agent.loop_types import (
    HookContext, LoopConfig, LoopResult, LoopStrategy,
)
from services.agent.tool_loop_execution import ToolLoopExecutionMixin
from services.agent.tool_result_cache import ToolResultCache

# 产物通道:SandboxExecutor.execute 直接产出 AgentResult.emit_payloads
# (流派 2 多字段 IPC,沙盒 IO 统一协议)
# 此处只聚合 result.emit_payloads → self._emit_payloads,见 _register_result_files


class ToolLoopExecutor(ToolLoopExecutionMixin):
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
        file_registry: Any = None,
    ) -> None:
        self.adapter = adapter
        self.executor = executor
        self.all_tools = all_tools
        self.config = config
        self.strategy = strategy
        self.hooks: List[LoopHook] = list(hooks or [])
        # 会话级读工具缓存（key=tool_name+args_hash → result, TTL 5 分钟）
        self._cache = ToolResultCache()
        # file_registry 已废弃（对齐 Claude 模式后不再需要）
        self._file_registry = None
        # 停止策略：本轮所有工具的原始结果（供 run() 中 classify 使用）
        # 每项: (tool_name, result, audit_status)
        self._turn_tool_outcomes: List[Tuple[str, Any, str]] = []

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
        self._start_validation_observation(hook_ctx.task_id or "")

        accumulated_text = ""
        total_tokens = 0
        is_llm_synthesis = False
        empty_turns = 0
        recent_calls: List[str] = []
        context_recovery_used = False
        self._emit_payloads: List[Dict[str, Any]] = []

        # ── 停止策略：初始化追踪器和配置 ──
        from services.agent.stop_policy import (
            FailureTracker, StopPolicyConfig,
        )
        stop_config = self.config.stop_config or StopPolicyConfig()
        tracker = FailureTracker()
        stop_reason = ""
        wrap_up_reason = ""

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
                stop_reason = "loop_detected"
                break

            accumulated_text = await self._execute_tools(
                completed, selected_tools, turn_text, hook_ctx,
                turn_prompt_tokens=_pt, turn_completion_tokens=_ct,
            )

            # 退出信号工具命中
            if any(tc["name"] in self.strategy.exit_signals for tc in completed):
                is_llm_synthesis = bool(turn_text)
                break

            should_wrap, failure_reason = self._evaluate_turn_outcomes(
                tracker,
                stop_config,
                model_step=turn + 1,
                turns_remaining=self.config.max_turns - (turn + 1),
            )
            if should_wrap:
                stop_reason = "wrap_up_failure"
                wrap_up_reason = failure_reason
                break
            # CONTINUE / HARD_FAIL(不在此层处理) → 继续循环

            logger.info(
                f"ToolLoop turn {turn + 1} | "
                f"tools={[tc['name'] for tc in completed]}"
            )

        return await self._finalize(
            accumulated_text, total_tokens, turn,
            is_llm_synthesis, hook_ctx,
            stop_reason=stop_reason, wrap_up_reason=wrap_up_reason,
        )

    def _evaluate_turn_outcomes(
        self,
        tracker: Any,
        stop_config: Any,
        *,
        model_step: int,
        turns_remaining: int,
    ) -> tuple[bool, str]:
        """运行旧StopPolicy并返回是否收尾；观察模式不消费此结果。"""
        from services.agent.agent_result import AgentResult
        from services.agent.stop_policy import (
            ResultClass,
            StopDecision,
            classify_tool_result,
            evaluate,
            most_severe,
        )

        turn_classes: List[ResultClass] = []
        worst_tool_name = ""
        for tool_name, result, audit_status in self._turn_tool_outcomes:
            result_class = classify_tool_result(result, audit_status)
            turn_classes.append(result_class)
            if result_class == ResultClass.SUCCESS:
                tracker.record_success()
                continue
            error_text = ""
            if isinstance(result, AgentResult):
                error_text = result.error_message
            elif isinstance(result, str):
                error_text = result
            tracker.record_failure(tool_name, error_text)
            worst_tool_name = tool_name
        self._turn_tool_outcomes.clear()

        result_class = most_severe(turn_classes)
        decision = evaluate(
            tracker,
            result_class,
            stop_config,
            turns_remaining=turns_remaining,
        )
        self._compare_validation_decision(
            old_decision=decision,
            model_step=model_step,
        )
        if decision != StopDecision.CONTINUE:
            logger.info(
                f"StopPolicy decision | tool={worst_tool_name or 'multi'} | "
                f"result_class={result_class.value} | "
                f"decision={decision.value} | "
                f"consecutive={tracker.consecutive_failures} | "
                f"same_streak={tracker.same_error_streak} | "
                f"turns_remaining={turns_remaining}"
            )
        reason = (
            f"consecutive_failures={tracker.consecutive_failures} | "
            f"result_class={result_class.value}"
        )
        return decision == StopDecision.WRAP_UP, reason

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
        hook_ctx: HookContext,
        stop_reason: str = "",
        wrap_up_reason: str = "",
    ) -> LoopResult:
        """循环退出后的兜底文本 / wrap_up 合成 / hook 链 + 打包 LoopResult"""
        # ── wrap_up 合成（stop_reason 非空） ──
        if stop_reason and not is_llm_synthesis:
            from services.agent.stop_policy import synthesize_wrap_up
            synthesis = await synthesize_wrap_up(
                adapter=self.adapter,
                messages=hook_ctx.messages,
                emit_payloads=self._emit_payloads,
                reason=wrap_up_reason or stop_reason,
            )
            if synthesis:
                accumulated_text = synthesis
                is_llm_synthesis = True
                logger.info(
                    f"ToolLoop wrap_up synthesis OK | reason={stop_reason} | "
                    f"len={len(synthesis)}"
                )
            else:
                logger.warning(
                    f"ToolLoop wrap_up synthesis failed | reason={stop_reason}"
                )

        if not is_llm_synthesis:
            logger.warning(
                f"ToolLoop exited without synthesis | "
                f"raw_len={len(accumulated_text)} | turns={turn + 1}"
            )
            accumulated_text = self.config.no_synthesis_fallback_text

        # Hook 链：合成阶段（LLM 合成时触发）
        if is_llm_synthesis and accumulated_text:
            for hook in self.hooks:
                accumulated_text = await hook.on_text_synthesis(
                    hook_ctx, accumulated_text,
                )

        return LoopResult(
            text=accumulated_text,
            total_tokens=total_tokens,
            turns=min(turn + 1, self.config.max_turns),
            is_llm_synthesis=is_llm_synthesis,
            emit_payloads=self._emit_payloads,
            stop_reason=stop_reason,
            wrap_up_reason=wrap_up_reason,
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
            int(self.config.context_window * self.config.context_recovery_target),
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

    def _register_result_files(self, result: Any, tool_name: str) -> None:
        """统一注册 emit_payloads(ToolOutput / AgentResult 共用)。
        Agent (ImageAgent/media_tool 等) 直接产 result.emit_payloads,
        此处聚合到 self._emit_payloads。
        """
        payloads = getattr(result, "emit_payloads", None)
        if payloads:
            self._emit_payloads.extend(payloads)

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

        # 上下文压缩：基于模型窗口大小（对标大厂：每轮压缩到窗口内）
        estimated = estimate_tokens(hook_ctx.messages)
        threshold = int(
            self.config.context_window * self.config.context_compression_threshold
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
