"""Tool Loop Executor Hook 实现

LoopHook 基类（所有方法 no-op default）+ 5 个具体实现：
- ProgressNotifyHook：WebSocket 进度推送（仅 task_id 存在时生效）
- ToolAuditHook：fire-and-forget 工具审计日志
- TemporalValidatorHook：L4 时间事实校验（合成阶段改写文本）
- FailureReflectionHook：[A2] 工具失败时注入分析提示，引导模型自我纠错
- AmbiguityDetectionHook：[A1] 工具返回多条匹配时注入提示，引导模型用 ask_user 确认

设计参考：OpenAI Agents SDK RunHooks / Anthropic Claude Code 中间件链。
每个 hook 单一职责，可独立单测，可任意组合。
"""
from __future__ import annotations

import asyncio
import re
from typing import Any, Dict, Optional

from loguru import logger

from services.agent.loop_types import HookContext


# ============================================================
# Hook 基类
# ============================================================

class LoopHook:
    """工具循环 Hook 基类。子类只需 override 关心的事件。

    所有方法 no-op default，调用顺序由装配方控制。
    """

    async def on_turn_start(self, ctx: HookContext) -> None:
        """每轮 LLM 调用之前触发"""

    async def on_tool_start(
        self, ctx: HookContext, tool_name: str, args: Dict[str, Any],
    ) -> None:
        """单工具执行之前触发"""

    async def on_tool_end(
        self,
        ctx: HookContext,
        tool_name: str,
        args: Dict[str, Any],
        result: str,
        status: str,
        elapsed_ms: int,
        is_cached: bool,
        is_truncated: bool,
        tool_call_id: str,
        **kwargs: Any,
    ) -> None:
        """单工具执行之后触发（含成功/超时/异常/缓存命中）"""

    async def on_text_synthesis(
        self, ctx: HookContext, text: str,
    ) -> str:
        """LLM 合成最终文本之后触发，可改写文本（None 不改）

        多个 hook 时按装配顺序串行调用，前一个的输出是后一个的输入。
        """
        return text


# ============================================================
# 进度推送
# ============================================================

class ProgressNotifyHook(LoopHook):
    """通过 WebSocket 推送 Agent 执行进度

    仅在 ctx.task_id 存在时推送（headless 场景自然 no-op）。
    """

    def __init__(self, max_turns: int) -> None:
        self._max_turns = max_turns

    async def on_turn_start(self, ctx: HookContext) -> None:
        if not ctx.task_id:
            return
        # 首轮粗估总耗时（每个工具假设 3 秒）
        estimated_s = (
            len(ctx.selected_tools) * 3 if ctx.turn == 1 else None
        )
        await self._publish(
            ctx, tool_name="thinking", estimated_s=estimated_s,
        )

    async def on_tool_start(
        self, ctx: HookContext, tool_name: str, args: Dict[str, Any],
    ) -> None:
        if not ctx.task_id:
            return
        if tool_name in ("route_to_chat", "ask_user"):
            return  # 退出信号工具不推
        await self._publish(ctx, tool_name=tool_name)

    async def _publish(
        self,
        ctx: HookContext,
        tool_name: str,
        estimated_s: Optional[int] = None,
    ) -> None:
        try:
            from schemas.websocket import build_agent_step
            from services.websocket_manager import ws_manager

            msg = build_agent_step(
                conversation_id=ctx.conversation_id,
                tool_name=tool_name,
                status="running",
                turn=ctx.turn,
                task_id=ctx.task_id,
                max_turns=self._max_turns,
                elapsed_s=ctx.budget.elapsed if ctx.budget else None,
                tools_completed=list(dict.fromkeys(ctx.tools_called)),
                estimated_s=estimated_s,
            )
            await ws_manager.send_to_task_or_user(
                ctx.task_id, ctx.user_id, msg,
            )
        except Exception as e:
            logger.debug(
                f"ProgressNotifyHook failed | turn={ctx.turn} | error={e}"
            )


# ============================================================
# 子Agent思考进度（推送到主Agent的thinking区域）
# ============================================================

class SubAgentThinkingHook(LoopHook):
    """子Agent工具调用进度 → 以 thinking_chunk 推送到前端ThinkingBlock。

    独立持有 task_id/message_id（不依赖 hook_ctx.task_id），
    因此不与主 Agent 的 ProgressNotifyHook 冲突。
    """

    TOOL_LABEL: Dict[str, str] = {
        # 核心工具（最常调用）
        "local_data": "查询数据",
        "local_compare_stats": "对比统计",
        "local_stock_query": "查询库存",
        "local_product_identify": "识别商品",
        "code_execute": "执行数据分析",
        # 扩展本地工具
        "local_product_stats": "统计商品数据",
        "local_platform_map_query": "查询平台映射",
        "local_product_flow": "查询供应链",
        "local_global_stats": "统计全局数据",
        # 远程ERP
        "erp_info_query": "查询基础信息",
        "erp_product_query": "查询商品",
        "erp_trade_query": "查询订单",
        "erp_aftersales_query": "查询售后",
        "erp_warehouse_query": "查询仓储",
        "erp_purchase_query": "查询采购",
        "erp_taobao_query": "查询平台订单",
        "erp_execute": "执行ERP操作",
        # 通用
        "fetch_all_pages": "翻页获取全量数据",
        "erp_api_search": "搜索ERP文档",
    }

    def __init__(
        self,
        task_id: str,
        conversation_id: str,
        message_id: str,
        user_id: str,
        agent_name: str = "ERP Agent",
    ) -> None:
        self._task_id = task_id
        self._conversation_id = conversation_id
        self._message_id = message_id
        self._user_id = user_id
        self._agent_name = agent_name
        self._started = False
        self._done = False
        self._collected: list[str] = []

    async def on_tool_start(
        self, ctx: HookContext, tool_name: str, args: Dict[str, Any],
    ) -> None:
        label = self.TOOL_LABEL.get(tool_name, tool_name)
        text = ""
        if not self._started:
            text += f"\n\n── {self._agent_name} ──"
            self._started = True
        text += f"\n→ 正在{label}..."
        await self._push(text)

    async def push_done(self) -> None:
        """循环结束后由调用方手动调用（幂等，多次调用只推送一次）"""
        if self._started and not self._done:
            self._done = True
            await self._push("\n✓ 完成")

    @property
    def collected_text(self) -> str:
        """收集的全部进度文本（用于持久化到消息）"""
        return "".join(self._collected)

    async def _push(self, text: str) -> None:
        self._collected.append(text)
        try:
            from schemas.websocket import build_thinking_chunk
            from services.websocket_manager import ws_manager

            msg = build_thinking_chunk(
                task_id=self._task_id,
                conversation_id=self._conversation_id,
                message_id=self._message_id,
                chunk=text,
            )
            await ws_manager.send_to_task_or_user(
                self._task_id, self._user_id, msg,
            )
        except Exception:
            pass  # 进度推送失败不影响业务


# ============================================================
# 审计日志
# ============================================================

class ToolAuditHook(LoopHook):
    """每次工具执行结束后写入 tool_audit_log 表（fire-and-forget）"""

    async def on_tool_end(
        self,
        ctx: HookContext,
        tool_name: str,
        args: Dict[str, Any],
        result: str,
        status: str,
        elapsed_ms: int,
        is_cached: bool,
        is_truncated: bool,
        tool_call_id: str,
        turn_prompt_tokens: int = 0,
        turn_completion_tokens: int = 0,
    ) -> None:
        try:
            from services.agent.tool_audit import (
                ToolAuditEntry, build_args_hash, record_tool_audit,
            )
            from services.agent.observability import get_trace_id
            entry = ToolAuditEntry(
                task_id=ctx.task_id or "",
                conversation_id=ctx.conversation_id,
                user_id=ctx.user_id,
                org_id=ctx.org_id or "",
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                turn=ctx.turn,
                args_hash=build_args_hash(args),
                result_length=len(result) if isinstance(result, (str, bytes)) else 0,
                elapsed_ms=elapsed_ms,
                status=status,
                is_cached=is_cached,
                is_truncated=is_truncated,
                prompt_tokens=turn_prompt_tokens,
                completion_tokens=turn_completion_tokens,
                trace_id=get_trace_id(),
            )
            asyncio.create_task(record_tool_audit(ctx.db, entry))
        except Exception as e:
            logger.debug(f"ToolAuditHook failed | tool={tool_name} | error={e}")


# ============================================================
# L4 时间校验
# ============================================================

class TemporalValidatorHook(LoopHook):
    """L4 TemporalValidator + L5 偏离日志

    在 LLM 合成的最终文本上做时间事实校验，自动 patch 错误描述。
    设计文档：docs/document/TECH_ERP时间准确性架构.md §14
    """

    async def on_text_synthesis(
        self, ctx: HookContext, text: str,
    ) -> str:
        try:
            from services.agent.guardrails import (
                emit_deviation_records,
                validate_and_patch,
            )
            patched_text, deviations = validate_and_patch(
                text, ctx=ctx.request_ctx,
            )
            if deviations:
                emit_deviation_records(
                    db=ctx.db,
                    deviations=deviations,
                    task_id=ctx.task_id or "",
                    conversation_id=ctx.conversation_id,
                    user_id=ctx.user_id,
                    org_id=ctx.org_id,
                    turn=ctx.turn,
                    patched=True,
                )
            return patched_text
        except Exception as e:
            logger.warning(f"TemporalValidatorHook skipped | error={e}")
            return text


# ============================================================
# [A2] 失败反思
# ============================================================

class FailureReflectionHook(LoopHook):
    """工具返回错误时，往 messages 注入 system message 引导模型分析

    只匹配工具错误框架生成的固定前缀，不匹配业务数据中的"错误"/"失败"。

    副作用：mutate ctx.messages（追加 system 消息）。
    """

    _ERROR_PREFIXES = (
        "工具执行失败:",
        "工具执行超时",
        "工具参数JSON格式错误:",
        "❌",
        "Traceback",
    )

    async def on_tool_end(
        self,
        ctx: HookContext,
        tool_name: str,
        args: Dict[str, Any],
        result: str,
        status: str,
        elapsed_ms: int,
        is_cached: bool,
        is_truncated: bool,
        tool_call_id: str,
        **kwargs: Any,
    ) -> None:
        if not result:
            return
        if not (
            result.startswith(self._ERROR_PREFIXES)
            or "Error:" in result[:100]
        ):
            return
        ctx.messages.append({
            "role": "system",
            "content": (
                f"工具 {tool_name} 返回了错误。请分析原因后选择："
                f"1) 换参数重试 2) 换工具 3) 用 ask_user 向用户确认"
            ),
        })


# ============================================================
# [A1] 歧义检测
# ============================================================

class AmbiguityDetectionHook(LoopHook):
    """工具返回多条匹配时，注入 system message 引导模型用 ask_user 确认

    触发条件：local_product_identify 返回"匹配到N个商品/SKU"且 N≥2。

    副作用：mutate ctx.messages（追加 system 消息）。
    """

    # 匹配 local_product_identify 的返回格式
    _IDENTIFY_MULTI_RE = re.compile(
        r'匹配到(\d+)个(?:商品|SKU)',
    )

    # 需要检测歧义的工具集合
    _AMBIGUITY_TOOLS = {
        "local_product_identify",
    }

    async def on_tool_end(
        self,
        ctx: HookContext,
        tool_name: str,
        args: Dict[str, Any],
        result: str,
        status: str,
        elapsed_ms: int,
        is_cached: bool,
        is_truncated: bool,
        tool_call_id: str,
        **kwargs: Any,
    ) -> None:
        if not result or tool_name not in self._AMBIGUITY_TOOLS:
            return

        match = self._IDENTIFY_MULTI_RE.search(result)
        if not match:
            return

        count = int(match.group(1))
        if count < 2:
            return

        ctx.messages.append({
            "role": "system",
            "content": (
                f"⚠ {tool_name} 返回了 {count} 条匹配结果。"
                f"禁止自行选择，必须用 ask_user 将候选列表展示给用户，"
                f"让用户确认具体目标后再继续查询。"
            ),
        })
