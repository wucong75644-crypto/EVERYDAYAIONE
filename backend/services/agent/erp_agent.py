"""
ERP 独立 Agent — 领域专家模式。

内置 ToolLoopExecutor，可自主完成跨域查询 + 关联计算 + 报表生成。
主 Agent 只需一次调用，ERPAgent 内部编排多步工具调用并返回结论。

工具集：ERP 本地/远程查询工具 + code_execute（沙盒计算）
不含：erp_agent（防递归）、erp_execute（只读）、ask_user（无交互）

设计文档: docs/document/TECH_ERPAgent架构简化.md
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from services.agent.execution_budget import ExecutionBudget
    from utils.time_context import RequestContext

from services.agent.agent_result import AgentResult


def _error_result(summary: str, status: str = "error") -> AgentResult:
    """构建错误/异常 AgentResult 的快捷方式。"""
    return AgentResult(
        status=status, summary=summary,
        source="erp_agent", error_message=summary,
    )


# ── ERPAgent 内部 system prompt ──

_ERP_AGENT_SYSTEM_PROMPT = (
    "你是 ERP 数据分析专家，负责查询和分析企业的订单、库存、采购、售后数据。\n\n"
    "=== 已加载工具 ===\n"
    "local_data / local_compare_stats / local_stock_query / local_product_identify / "
    "local_product_stats / local_platform_map_query / "
    "local_shop_list / local_warehouse_list / local_supplier_list / code_execute\n\n"
    "=== 远程API工具（按需自动加载，local 无法满足时降级使用） ===\n"
    "erp_info_query / erp_product_query / erp_trade_query / "
    "erp_aftersales_query / erp_warehouse_query / erp_purchase_query / "
    "erp_taobao_query / fetch_all_pages / erp_api_search\n\n"
    "=== 规则 ===\n"
    "- local_data 覆盖 90% 查询，优先使用\n"
    "- erp_*_query 仅用于：物流轨迹、操作日志、仓储操作，或 local 返回错误时降级\n"
    "- code_execute 不能查数据，用 read_file() 读 staging 文件\n"
    "- local_data 默认 mode=summary，「导出」「下载」才用 export\n"
    "- 时间用 ISO 格式，含「付款」用 pay_time，含「发货」用 consign_time，默认 doc_created_at\n"
    "- 工具调用之间不要输出文字，静默使用工具\n"
    "- 最后输出一次结构化结果：关键数据 + 结论，不要润色、不要加建议、不要评论\n"
)


class ERPAgent:
    """ERP 领域专家 Agent — 内置 ToolLoopExecutor 自主编排查询+计算"""

    def __init__(
        self,
        db: Any,
        user_id: str,
        conversation_id: str,
        org_id: str,
        task_id: Optional[str] = None,
        message_id: Optional[str] = None,
        request_ctx: Optional["RequestContext"] = None,
        budget: Optional["ExecutionBudget"] = None,
    ) -> None:
        self.db = db
        self.user_id = user_id
        self.conversation_id = conversation_id
        self.org_id = org_id
        self.task_id = task_id
        self.message_id = message_id
        self._budget = budget
        from utils.time_context import RequestContext
        self.request_ctx = request_ctx or RequestContext.build(
            user_id=user_id, org_id=org_id, request_id=task_id or "",
        )
        from services.agent.experience_recorder import ExperienceRecorder
        self._experience = ExperienceRecorder(org_id=org_id, writer="erp_agent")

    async def execute(
        self,
        task: str,
        conversation_context: str = "",
    ) -> AgentResult:
        """执行 ERP 数据分析任务。

        Args:
            task: 主 Agent 整理好的清晰任务描述
            conversation_context: 对话背景补充（可选）
        """
        query = task
        if conversation_context:
            query = f"{task}\n（背景：{conversation_context}）"

        # Langfuse span
        from services.agent.observability.langfuse_integration import (
            create_trace, create_span,
        )
        create_span(
            create_trace(name="erp_agent", user_id=self.user_id),
            name="erp_agent.execute",
            metadata={"task": task[:200], "has_context": bool(conversation_context)},
        )

        if not self.org_id:
            return _error_result("当前账号未开通 ERP 功能，请联系管理员配置企业账号。")

        try:
            return await self._execute_with_tool_loop(query)
        except asyncio.TimeoutError:
            return _error_result("查询超时，请缩小查询范围后重试", status="timeout")
        except Exception as e:
            logger.opt(exception=True).error(
                f"ERPAgent exception | query={query[:100]}",
            )
            is_known = isinstance(e, (ValueError, PermissionError, ConnectionError))
            error_msg = (
                str(e) if is_known
                else f"内部错误，请联系管理员（{type(e).__name__}）"
            )
            return _error_result(f"执行异常: {error_msg}")

    # ── 核心执行：ToolLoopExecutor ──

    async def _execute_with_tool_loop(self, query: str) -> AgentResult:
        """构建工具循环并执行，返回 AgentResult。"""
        from core.config import get_settings
        from services.adapters.factory import create_chat_adapter
        from services.agent.tool_executor import ToolExecutor
        from config.erp_tools import get_erp_agent_tools

        settings = get_settings()

        # 1. 工具分层加载（对齐 Claude Code deferred tools 模式）
        #    core_tools: LLM 始终可见（10个：9 local + code_execute）
        #    all_tools:  全量（19个），供 tool_expansion 按需注入
        core_tools, all_tools = get_erp_agent_tools(org_id=self.org_id)

        # 2. 创建 LLM adapter
        adapter = create_chat_adapter(
            settings.agent_loop_model, org_id=self.org_id, db=self.db,
        )

        tool_loop = None
        try:
            # 3. 创建 ToolExecutor（与主 Agent 共用同一个类，上下文隔离靠参数）
            executor = ToolExecutor(
                self.db, self.user_id, self.conversation_id,
                self.org_id, self.request_ctx,
            )

            # 4. 装配 ToolLoopExecutor + HookContext
            tool_loop, hook_ctx, budget = self._build_tool_loop(
                adapter, executor, all_tools,
            )

            # 5. 构建 messages
            messages = self._build_messages(query)

            # 6. 执行工具循环（selected_tools=core，LLM 只看核心工具）
            tools_called: List[str] = []
            loop_result = await tool_loop.run(
                messages=messages,
                selected_tools=core_tools,
                tools_called=tools_called,
                hook_ctx=hook_ctx,
                budget=budget,
            )

            # 7. 经验记录
            asyncio.create_task(self._experience.record(
                "routing", query, tools_called[:5],
                f"tool_loop | turns={loop_result.turns} | "
                f"tokens={loop_result.total_tokens}",
                confidence=0.8,
            ))

            # 8. staging 延迟清理
            asyncio.create_task(self._cleanup_staging_delayed())

            # 9. 推送完成标记 + 收集thinking文本 → AgentResult
            from services.agent.loop_hooks import SubAgentThinkingHook
            thinking_hook = None
            if tool_loop:
                for hook in tool_loop.hooks:
                    if isinstance(hook, SubAgentThinkingHook):
                        thinking_hook = hook
                        break
            if thinking_hook:
                await thinking_hook.push_done()

            result = self._convert_result(loop_result)
            if thinking_hook:
                result.thinking_text = thinking_hook.collected_text
            return result
        finally:
            # 异常路径也推送完成标记
            from services.agent.loop_hooks import SubAgentThinkingHook
            try:
                if tool_loop:
                    for hook in tool_loop.hooks:
                        if isinstance(hook, SubAgentThinkingHook):
                            await hook.push_done()
                            break
            except Exception:
                pass
            try:
                await adapter.close()
            except Exception:
                pass

    def _build_tool_loop(
        self,
        adapter: Any,
        executor: Any,
        all_tools: List[Dict[str, Any]],
    ) -> tuple:
        """装配 ToolLoopExecutor + HookContext + Budget。

        与 ScheduledTaskAgent 差异：
        - hook_ctx.task_id=None：不走 ProgressNotifyHook（防止与主 Agent 冲突）
        - SubAgentThinkingHook 独立持有 task_id，通过 thinking_chunk 推送进度
        - ERPAgent 专用 max_turns/max_tokens
        """
        from services.agent.tool_loop_executor import ToolLoopExecutor
        from services.agent.loop_types import (
            HookContext, LoopConfig, LoopStrategy,
        )
        from services.agent.loop_hooks import ToolAuditHook
        from services.agent.execution_budget import ExecutionBudget
        from core.config import get_settings

        settings = get_settings()

        hook_ctx = HookContext(
            db=self.db,
            user_id=self.user_id,
            org_id=self.org_id,
            conversation_id=self.conversation_id,
            task_id=None,  # 不推送 WS 进度，防止与主 Agent ProgressNotifyHook 冲突
            request_ctx=self.request_ctx,
        )

        # Hooks: 审计 + 子Agent思考进度（仅 Web 链路有 task_id 时挂载）
        hooks = [ToolAuditHook()]
        if self.task_id and self.message_id:
            from services.agent.loop_hooks import SubAgentThinkingHook
            hooks.append(SubAgentThinkingHook(
                task_id=self.task_id,
                conversation_id=self.conversation_id,
                message_id=self.message_id,
                user_id=self.user_id,
            ))

        tool_loop = ToolLoopExecutor(
            adapter=adapter,
            executor=executor,
            all_tools=all_tools,
            config=LoopConfig(
                max_turns=settings.erp_agent_max_turns,
                max_tokens=settings.erp_agent_max_tokens,
                tool_timeout=settings.erp_agent_tool_timeout,
                thinking_mode="enabled",  # qwen3.5 function calling 需要开启
                no_synthesis_fallback_text=(
                    "查询过程中未能生成完整结论，请缩小查询范围或更具体地描述需求。"
                ),
            ),
            strategy=LoopStrategy(
                exit_signals=frozenset(),       # 无用户交互
                enable_tool_expansion=True,      # 扩展工具按需注入（deferred tools）
                force_tool_use_first=True,       # 必须查数据
            ),
            hooks=hooks,
        )

        # Budget: 从父 budget fork，或创建独立 budget
        if self._budget:
            budget = self._budget.fork(
                max_turns=settings.erp_agent_max_turns,
            )
        else:
            budget = ExecutionBudget(
                max_turns=settings.erp_agent_max_turns,
                max_tokens=settings.erp_agent_max_tokens,
                max_wall_time=300.0,
            )

        return tool_loop, hook_ctx, budget

    def _build_messages(self, query: str) -> List[Dict[str, Any]]:
        """构建 ERPAgent 内部 LLM 的 messages。"""
        # 时间事实注入
        time_injection = self.request_ctx.for_prompt_injection()

        system_content = (
            _ERP_AGENT_SYSTEM_PROMPT
            + "\n## 当前时间\n" + time_injection
        )

        return [
            {"role": "system", "content": system_content},
            {"role": "user", "content": query},
        ]

    @staticmethod
    def _convert_result(loop_result: Any) -> AgentResult:
        """LoopResult → AgentResult 转换。"""
        if loop_result.exit_via_ask_user:
            return AgentResult(
                status="ask_user",
                summary=loop_result.text,
                source="erp_agent",
                ask_user_question=loop_result.text,
                tokens_used=loop_result.total_tokens,
            )

        status = "success" if loop_result.is_llm_synthesis else "empty"
        return AgentResult(
            status=status,
            summary=loop_result.text,
            collected_files=loop_result.collected_files,
            source="erp_agent",
            tokens_used=loop_result.total_tokens,
            confidence=1.0,
        )

    # ── 工具描述自动生成（静态层）──

    @staticmethod
    def build_tool_description() -> str:
        """从 capability manifest 格式化为 5 段式描述文本。

        纯模板渲染，不含任何硬编码内容。
        改内容 → 改 get_capability_manifest()；改格式 → 改此方法。
        设计文档: docs/document/TECH_Agent能力通信架构.md §3.3.2
        """
        from services.agent.plan_builder import get_capability_manifest
        m = get_capability_manifest()

        # ① 功能定义
        lines = [m["summary"]]

        # ② 决策边界
        lines.append("\n使用场景：" + "；".join(m["use_when"]))
        dont = " / ".join(
            f"{d['场景']}→{d['替代']}" for d in m["dont_use_when"]
        )
        lines.append(f"不要用于：{dont}")

        # ③ 能力清单
        lines.append("\n能力：")
        lines.append(
            f"- 输出模式：{' / '.join(m['modes'])}（>200行自动导出文件）",
        )
        lines.append(f"- 分组统计：按{'/'.join(m['group_by'])}统计")
        lines.append(
            f"- 过滤：自动识别{'、'.join(m['platforms'])}、商品编码、订单号",
        )
        lines.append(
            f"- 时间列：{' / '.join(m['time_cols'])}（默认 doc_created_at）",
        )
        lines.append("- 异常数据：默认排除刷单，query 中写'包含刷单'则包含")
        lines.append("- 跨域关联分析：可自主查多个域的数据并关联计算")
        lines.append("- 报表生成：可自主生成Excel/CSV报表文件")

        # ③+ 可查询信息分类
        categories = m.get("field_categories", {})
        if categories:
            lines.append(f"- 可查询信息：{'/'.join(categories.keys())}")
            lines.append(
                "  （query 中提到具体信息如'备注''地址''快递单号'"
                "会自动返回对应字段）",
            )

        # ④ 返回说明
        lines.append("\n返回：")
        for r in m["returns"]:
            lines.append(f"- {r}")

        # ⑤ few-shot 示例
        lines.append("\nquery 示例：")
        for ex in m["examples"]:
            lines.append(f"· \"{ex['query']}\" → {ex['effect']}")

        return "\n".join(lines)

    # ── staging 清理 ──

    async def _cleanup_staging_delayed(self, delay: int = 900) -> None:
        """会话级 staging 延迟清理（15 分钟，覆盖 ~85% 的用户追问间隔）。"""
        import shutil
        from pathlib import Path
        from core.config import get_settings

        try:
            await asyncio.sleep(delay)
            settings = get_settings()
            from core.workspace import resolve_staging_dir
            staging_dir = Path(resolve_staging_dir(
                settings.file_workspace_root,
                self.user_id, self.org_id, self.conversation_id,
            ))
            if staging_dir.exists():
                shutil.rmtree(staging_dir, ignore_errors=True)
                logger.info(
                    f"ERPAgent staging cleaned | dir={staging_dir}",
                )
        except Exception as e:
            logger.debug(f"ERPAgent staging cleanup failed | error={e}")
