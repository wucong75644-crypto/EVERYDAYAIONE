"""ERP Agent 工具循环执行器

从 erp_agent.py 拆出（V2.2 §三 500 行红线），承担：
- 工具循环主逻辑（原 _run_tool_loop）
- 单工具执行（原 _execute_tools）
- 进度推送（原 _notify_progress）
- 审计日志（原 _emit_tool_audit）
- 结果缓存家族（原 _cache_*）

设计原则：通过 ToolLoopContext 传入 Agent 环境，零反向依赖。
每次 ToolLoopExecutor 实例对应一次 ERPAgent.execute() 调用。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from services.agent.erp_agent_types import (
    MAX_ERP_TURNS,
    MAX_TOTAL_TOKENS as _MAX_TOTAL_TOKENS,
    TOOL_TIMEOUT as _TOOL_TIMEOUT,
    is_context_length_error as _is_context_length_error,
)
from services.agent.erp_tool_cache import ToolResultCache


@dataclass
class ToolLoopContext:
    """ToolLoopExecutor 运行上下文（Agent 注入的环境状态）"""
    db: Any
    user_id: str
    conversation_id: str
    org_id: str
    task_id: Optional[str]
    request_ctx: Any  # 时间事实层 SSOT (utils.time_context.RequestContext)


class ToolLoopExecutor:
    """ERP Agent 工具循环执行器

    每次 ERPAgent.execute() 构造一个新实例，对应一次完整的工具循环生命周期。
    内部维护一个 ToolResultCache（不跨调用复用）。
    """

    def __init__(
        self,
        adapter: Any,
        executor: Any,
        all_tools: List[Dict[str, Any]],
        ctx: ToolLoopContext,
    ) -> None:
        self.adapter = adapter
        self.executor = executor
        self.all_tools = all_tools
        self.ctx = ctx
        # [B4] 会话级读工具缓存（key=tool_name+args_hash → result, TTL 5 分钟）
        self._cache = ToolResultCache()

    # ========================================
    # 工具循环主逻辑
    # ========================================

    async def run(
        self,
        messages: List[Dict[str, Any]],
        selected_tools: List[Dict[str, Any]],
        tools_called: List[str],
        budget: Any = None,
    ) -> Tuple[str, int, int]:
        """运行工具循环，返回 (accumulated_text, total_tokens, turns)"""
        accumulated_text = ""
        total_tokens = 0
        is_llm_synthesis = False  # 标记最终结果是否为 LLM 合成
        exit_via_ask_user = False  # L4 不校验追问消息
        empty_turns = 0  # 连续空响应计数
        # [Fix G] 循环检测
        recent_calls: List[str] = []
        # [B6] 上下文恢复最多尝试 1 次，防止反复压缩浪费 API 调用
        context_recovery_used = False

        # [B2/B6] 提前导入压缩函数（避免循环内重复 import 语句）
        from services.handlers.context_compressor import enforce_budget

        for turn in range(MAX_ERP_TURNS):
            should_continue = await self._pre_turn_checks(
                turn, total_tokens, messages, selected_tools,
                tools_called, budget,
            )
            if not should_continue:
                break

            try:
                tc_acc, turn_text, turn_tokens = await self._stream_one_turn(
                    messages, selected_tools,
                )
            except Exception as stream_err:
                if _is_context_length_error(stream_err) and not context_recovery_used:
                    context_recovery_used = True
                    logger.warning(
                        f"ERPAgent context_length_exceeded | turn={turn + 1} | "
                        f"attempting recovery (one-shot)"
                    )
                    enforce_budget(messages, int(_MAX_TOTAL_TOKENS * 0.5))
                    messages.append({
                        "role": "user",
                        "content": "上下文过长已自动压缩。请直接继续当前任务，不要重复已完成的步骤。",
                    })
                    continue  # 重试当前轮（仅此一次）
                raise  # 非上下文错误 或 已恢复过一次，正常冒泡

            total_tokens += turn_tokens

            if not tc_acc:
                if not tools_called:
                    # 还没调过任何工具就想输出文字 — 强制继续循环
                    empty_turns += 1
                    logger.info(
                        f"ERPAgent skip empty turn #{empty_turns} | "
                        f"text={turn_text[:50] if turn_text else '(empty)'}"
                    )
                    if empty_turns >= 2:
                        # 连续 2 次空响应，有文字则作为最终输出
                        if turn_text:
                            accumulated_text = turn_text
                            is_llm_synthesis = True
                        break
                    if turn_text:
                        messages.append({"role": "assistant", "content": turn_text})
                    continue
                # 调过工具后输出纯文字 — 这是干净的合成结果
                accumulated_text = turn_text
                is_llm_synthesis = True
                break

            completed = sorted(tc_acc.values(), key=lambda x: x.get("id", ""))

            # [Fix G] 循环检测：连续 3 次相同调用中止
            call_key = "|".join(
                f"{tc['name']}:{hashlib.md5(tc['arguments'].encode()).hexdigest()[:6]}"
                for tc in completed
            )
            recent_calls.append(call_key)
            if len(recent_calls) >= 3 and len(set(recent_calls[-3:])) == 1:
                logger.warning(f"ERPAgent loop detected | call={call_key}")
                break

            accumulated_text = await self._execute_tools(
                completed, messages, selected_tools,
                tools_called, turn_text, turn + 1, budget=budget,
            )

            # route_to_chat / ask_user 是退出信号
            if any(tc["name"] in ("route_to_chat", "ask_user") for tc in completed):
                # ask_user 的结论在 accumulated（args.message），不依赖 turn_text
                has_ask_user = any(tc["name"] == "ask_user" for tc in completed)
                is_llm_synthesis = has_ask_user or bool(turn_text)
                exit_via_ask_user = has_ask_user  # L4 跳过追问消息
                break

            logger.info(f"ERPAgent turn {turn + 1} | tools={[tc['name'] for tc in completed]}")

        # 非正常退出（token超限/循环检测/max turns）且结果不是 LLM 合成的
        if not is_llm_synthesis:
            logger.warning(
                f"ERPAgent exited without synthesis | "
                f"raw_len={len(accumulated_text)} | turns={turn + 1}"
            )
            # 不尝试再调 LLM（上下文过长可能产出错误结论），
            # 返回明确提示让主 Agent 告知用户
            accumulated_text = "ERP 查询过程中未能生成完整结论，请重新提问或缩小查询范围。"

        # ─────────────────────────────────────────────────────────
        # L4 TemporalValidator — 事实正确性兜底（Phase 7）
        # 只校验 LLM 合成的文字，不校验 ask_user 追问 / 原始工具数据
        # 设计文档：docs/document/TECH_ERP时间准确性架构.md §14
        # ─────────────────────────────────────────────────────────
        if is_llm_synthesis and not exit_via_ask_user and accumulated_text:
            accumulated_text = await self._apply_temporal_validation(
                accumulated_text, turn,
            )

        return accumulated_text, total_tokens, min(turn + 1, MAX_ERP_TURNS)

    async def _pre_turn_checks(
        self,
        turn: int,
        total_tokens: int,
        messages: List[Dict[str, Any]],
        selected_tools: List[Dict[str, Any]],
        tools_called: List[str],
        budget: Any,
    ) -> bool:
        """循环开头的护栏 + 上下文压缩 + 进度推送。

        返回 True 表示当前 turn 可以继续；False 表示应 break 出循环
        （时间预算耗尽 / token 预算耗尽）。
        """
        from services.handlers.context_compressor import estimate_tokens, enforce_budget

        # [B3] 全局时间预算检查
        if budget and not budget.check_or_log(f"turn={turn + 1}"):
            return False

        # [Fix H] Token 预算检查
        if total_tokens >= _MAX_TOTAL_TOKENS:
            logger.warning(f"ERPAgent token budget exceeded | used={total_tokens}")
            return False

        # [B2] 上下文压缩：超 70% token 预算时主动压缩 messages
        estimated = estimate_tokens(messages)
        budget_70 = int(_MAX_TOTAL_TOKENS * 0.7)
        if estimated > budget_70:
            logger.info(
                f"ERPAgent context compress | tokens={estimated} | "
                f"budget_70={budget_70}"
            )
            enforce_budget(messages, budget_70)

        # [E1+E2] 推送进度（含已完成工具列表 + 耗时 + 预估）
        _estimated = len(selected_tools) * 3 if turn == 0 else None  # 首轮粗估
        await self._notify_progress(
            turn + 1, "thinking",
            tools_called=tools_called, budget=budget,
            estimated_s=_estimated,
        )
        return True

    async def _stream_one_turn(
        self,
        messages: List[Dict[str, Any]],
        selected_tools: List[Dict[str, Any]],
    ) -> Tuple[Dict[int, Dict[str, Any]], str, int]:
        """流式调用 LLM 一轮，聚合 tool_calls + content + tokens。

        返回：(tc_acc, turn_text, turn_tokens)。异常由调用方捕获处理。
        """
        tc_acc: Dict[int, Dict[str, Any]] = {}
        turn_text = ""
        turn_tokens = 0

        async for chunk in self.adapter.stream_chat(
            messages=messages, tools=selected_tools,
            temperature=0.1,  # [Fix I] 显式低温度
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
                turn_tokens = (chunk.prompt_tokens or 0) + (chunk.completion_tokens or 0)

        return tc_acc, turn_text, turn_tokens

    async def _apply_temporal_validation(
        self, accumulated_text: str, turn: int,
    ) -> str:
        """L4 TemporalValidator + L5 偏离日志（fire-and-forget）"""
        try:
            from services.agent.guardrails import (
                emit_deviation_records,
                validate_and_patch,
            )
            patched_text, deviations = validate_and_patch(
                accumulated_text, ctx=self.ctx.request_ctx,
            )
            if deviations:
                emit_deviation_records(
                    db=self.ctx.db,
                    deviations=deviations,
                    task_id=self.ctx.task_id or "",
                    conversation_id=self.ctx.conversation_id,
                    user_id=self.ctx.user_id,
                    org_id=self.ctx.org_id,
                    turn=min(turn + 1, MAX_ERP_TURNS),
                    patched=True,  # 当前策略：一律自动 patch
                )
                accumulated_text = patched_text
        except Exception as e:
            # 校验失败不阻塞主流程
            logger.warning(f"L4 temporal_validator skipped | error={e}")
        return accumulated_text

    # ========================================
    # 单轮工具执行
    # ========================================

    async def _execute_tools(
        self,
        completed: List[Dict[str, Any]],
        messages: List[Dict[str, Any]],
        selected_tools: List[Dict[str, Any]],
        tools_called: List[str],
        turn_text: str,
        turn: int,
        budget: Any = None,
    ) -> str:
        """执行一轮工具调用"""
        asst_msg: Dict[str, Any] = {"role": "assistant", "content": turn_text or None}
        asst_msg["tool_calls"] = [
            {"id": tc["id"], "type": "function",
             "function": {"name": tc["name"], "arguments": tc["arguments"]}}
            for tc in completed
        ]
        messages.append(asst_msg)

        accumulated = turn_text
        for tc in completed:
            tool_name = tc["name"]
            tools_called.append(tool_name)

            if tool_name in ("route_to_chat", "ask_user"):
                try:
                    args = json.loads(tc["arguments"])
                except json.JSONDecodeError:
                    args = {}
                if tool_name == "route_to_chat":
                    # 优先用 LLM 本轮合成的文字，而不是 system_prompt（可能是原始数据）
                    accumulated = turn_text if turn_text else ""
                else:
                    accumulated = args.get("message", turn_text)
                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": "OK"})
                break

            await self._notify_progress(turn, tool_name, tools_called=tools_called, budget=budget)

            try:
                args = json.loads(tc["arguments"]) if tc["arguments"] else {}
            except json.JSONDecodeError as e:
                logger.warning(f"ERPAgent bad JSON | tool={tool_name} | error={e}")
                result = f"工具参数JSON格式错误: {e}，请检查参数格式"
                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})
                accumulated = result
                continue

            result, _audit_status, _is_cached, _audit_elapsed = (
                await self._invoke_tool_with_cache(tool_name, args, budget)
            )

            # ERP Agent 内部截断+信号（防止单条结果撑爆上下文）
            from services.agent.tool_result_envelope import wrap_for_erp_agent
            _is_truncated = len(result) > 3000 if result else False
            result = wrap_for_erp_agent(tool_name, result)

            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})
            accumulated = result

            # [C1] 审计日志（fire-and-forget）
            self._emit_tool_audit(
                tool_name, tc["id"], turn, args, len(result),
                _audit_elapsed, _audit_status, _is_cached, _is_truncated,
            )

            # [A2] 失败反思：工具返回错误时，注入 system message 引导模型分析原因
            # 只匹配工具错误框架生成的固定前缀，不匹配业务数据中的"错误"/"失败"
            _error_prefixes = (
                "工具执行失败:", "工具执行超时", "工具参数JSON格式错误:",
                "❌", "Traceback",
            )
            if result.startswith(_error_prefixes) or "Error:" in result[:100]:
                messages.append({
                    "role": "system",
                    "content": (
                        f"工具 {tool_name} 返回了错误。请分析原因后选择："
                        f"1) 换参数重试 2) 换工具 3) 用 ask_user 向用户确认"
                    ),
                })

            # 自动扩展：千问调了隐藏的远程工具 → 从全量列表动态注入（去重）
            current = {t["function"]["name"] for t in selected_tools}
            if tool_name not in current and tool_name not in ("route_to_chat", "ask_user"):
                all_map = {t["function"]["name"]: t for t in self.all_tools}
                if tool_name in all_map:
                    selected_tools.append(all_map[tool_name])
                    logger.info(f"ERPAgent tool injected | {tool_name}")
                    current.add(tool_name)  # 防止多轮重复注入
                else:
                    # 不在 ERP 全量列表中（可能是其他域工具），尝试从 chat_tools 获取
                    from config.chat_tools import get_tools_by_names
                    extra = get_tools_by_names({tool_name}, org_id=self.ctx.org_id)
                    selected_tools.extend(extra)
                logger.info(f"ERPAgent tool expansion | added={tool_name}")

        return accumulated

    async def _invoke_tool_with_cache(
        self, tool_name: str, args: Dict[str, Any], budget: Any,
    ) -> Tuple[str, str, bool, int]:
        """缓存命中检查 → 否则执行工具（含超时控制）。

        返回：(result, audit_status, is_cached, elapsed_ms)
        """
        import time as _audit_time
        _audit_start = _audit_time.monotonic()
        _audit_status = "success"

        cached = self._cache.get(tool_name, args)
        if cached is not None:
            logger.info(f"ERPAgent cache hit | tool={tool_name}")
            elapsed_ms = int((_audit_time.monotonic() - _audit_start) * 1000)
            return cached, _audit_status, True, elapsed_ms

        # [Fix F + B3] 超时控制（动态：min(单工具上限, 剩余预算)）
        tool_timeout = (
            budget.tool_timeout(_TOOL_TIMEOUT) if budget
            else _TOOL_TIMEOUT
        )
        try:
            result = await asyncio.wait_for(
                self.executor.execute(tool_name, args),
                timeout=tool_timeout,
            )
            # [B4] 只缓存读工具的成功结果
            self._cache.put(tool_name, args, result)
        except asyncio.TimeoutError:
            logger.warning(
                f"ERPAgent tool timeout | tool={tool_name} | "
                f"timeout={tool_timeout:.1f}s"
            )
            result = f"工具执行超时（{int(tool_timeout)}秒），请缩小查询范围"
            _audit_status = "timeout"
        except Exception as e:
            logger.error(f"ERPAgent tool error | tool={tool_name} | error={e}")
            result = f"工具执行失败: {e}"
            _audit_status = "error"

        elapsed_ms = int((_audit_time.monotonic() - _audit_start) * 1000)
        return result, _audit_status, False, elapsed_ms

    # ========================================
    # 进度推送 + 审计
    # ========================================

    async def _notify_progress(
        self, turn: int, tool_name: str,
        tools_called: Optional[List[str]] = None,
        budget: Optional[Any] = None,
        estimated_s: Optional[int] = None,
    ) -> None:
        """通过 WebSocket 发送进度通知（含进度比例/耗时/已完成工具）"""
        if not self.ctx.task_id:
            return
        try:
            from schemas.websocket import build_agent_step
            from services.task_stream import publish as stream_publish
            msg = build_agent_step(
                conversation_id=self.ctx.conversation_id,
                tool_name=tool_name,
                status="running",
                turn=turn,
                task_id=self.ctx.task_id,
                max_turns=MAX_ERP_TURNS,
                elapsed_s=budget.elapsed if budget else None,
                tools_completed=list(dict.fromkeys(tools_called)) if tools_called else None,
                estimated_s=estimated_s,
            )
            await stream_publish(self.ctx.task_id, self.ctx.user_id, msg)
        except Exception as e:
            logger.debug(f"ERPAgent progress notify failed | turn={turn} | error={e}")

    def _emit_tool_audit(
        self, tool_name: str, tool_call_id: str, turn: int,
        args: Dict[str, Any], result_length: int, elapsed_ms: int,
        status: str, is_cached: bool = False, is_truncated: bool = False,
    ) -> None:
        """[C1] fire-and-forget 审计日志"""
        from services.agent.tool_audit import (
            ToolAuditEntry, build_args_hash, record_tool_audit,
        )
        asyncio.create_task(record_tool_audit(self.ctx.db, ToolAuditEntry(
            task_id=self.ctx.task_id or "", conversation_id=self.ctx.conversation_id,
            user_id=self.ctx.user_id, org_id=self.ctx.org_id or "",
            tool_name=tool_name, tool_call_id=tool_call_id,
            turn=turn, args_hash=build_args_hash(args),
            result_length=result_length, elapsed_ms=elapsed_ms,
            status=status, is_cached=is_cached, is_truncated=is_truncated,
        )))

