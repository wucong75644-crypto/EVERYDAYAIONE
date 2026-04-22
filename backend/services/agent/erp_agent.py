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
    "你是 ERP 数据分析专家。你可以自主完成跨域查询、关联计算和报表生成。\n\n"
    "## 你的工具\n"
    "- **local_data**：本地数据库统一查询（订单/采购/售后/收货/上架/采退），毫秒级\n"
    "- **local_stock_query**：库存查询（需精确编码）\n"
    "- **local_product_identify**：编码识别（模糊名称→精确编码）\n"
    "- **local_compare_stats**：时间维度对比（同比/环比）\n"
    "- **local_product_stats**：按商品编码查统计报表\n"
    "- **local_shop_list / local_warehouse_list / local_supplier_list**：参考列表\n"
    "- **erp_*_query**：远程API（local 无数据时降级使用）\n"
    "- **code_execute**：Python 沙盒，用于数据关联、计算、生成Excel\n\n"
    "## local_data mode 选择（最重要，必须遵守）\n\n"
    "| 用户意图 | mode | 触发词 |\n"
    "|---------|------|--------|\n"
    "| 统计/汇总/多少 | **summary** | 多少单、统计、汇总、查一下XX情况、按XX分组 |\n"
    "| 查看具体记录 | **detail** | 某订单详情、看看明细、具体记录 |\n"
    "| 生成文件下载 | **export** | 导出、下载、生成Excel、导出来 |\n\n"
    "**⚠ 默认 summary。除非用户明确说「导出」「下载」「Excel」，否则一律用 summary。**\n"
    "**⚠ 「查询XX」「XX多少」「XX情况」= summary，不是 export。**\n\n"
    "## 任务理解\n"
    "主 Agent 会给你查询任务和对话背景。\n"
    "- 参数明确 → 直接查\n"
    "- 参数不够（不知道查哪个平台/什么时间/哪个商品）→ 返回说明缺什么，让主 Agent 补充\n\n"
    "## 工作规则\n"
    "1. local 工具优先，远程 API 仅在本地无数据时使用\n"
    "2. 跨域数据通过 product_code（商品编码）关联\n"
    "3. 需要计算/排序/生成报表时用 code_execute\n"
    "4. code_execute 中用 read_file() 读取 staging 文件\n"
    "5. 生成的 Excel/CSV 输出到 OUTPUT_DIR\n"
    "6. 最终回复应简洁清晰：结论 + 关键数据 + 文件（如有）\n\n"
    "## 时间规范\n"
    "- 日期用 ISO: 2026-04-14 00:00:00\n"
    "- 含「付款」→ time_type=pay_time\n"
    "- 含「发货」→ time_type=consign_time\n"
    "- 默认 doc_created_at\n"
)


_ERP_AGENT_ROUTING_RULES = (
    "## 工具选择规则\n\n"
    "### 层级：local > erp > fetch_all_pages > code_execute\n"
    "- 禁止跳过 local 工具直接用 erp 远程 API\n"
    "- code_execute 是纯计算沙盒，不能查数据\n\n"
    "### 常见场景\n"
    "- 今天/本周/本月多少单 → local_data(doc_type=order, mode=summary, filters=[时间条件])\n"
    "- 已发货/未发货订单 → local_data(filters=[{field:order_status, op:eq, value:SELLER_SEND_GOODS}])\n"
    "- 按店铺/平台统计 → local_data(mode=summary, group_by=[shop_name])\n"
    "- 按商品排名 → local_data(mode=summary, group_by=[outer_id])\n"
    "- 导出 Excel → local_data(mode=export) → code_execute 读 staging 生成 Excel\n"
    "- 查某订单详情 → local_data(mode=detail, filters=[{field:order_no, op:eq, value:xxx}])\n"
    "- 对比/同比/环比 → local_compare_stats\n"
    "- 某商品编码的采购/售后/订单 → local_data(filters=[{field:outer_id, op:eq, value:编码}])\n"
    "- 跨域关联分析 → 多次 local_data 查不同 doc_type → code_execute 用 product_code 关联\n\n"
    "### 时间规范\n"
    "- 日期用 ISO: 2026-04-14 00:00:00\n"
    "- 含「付款」→ time_type=pay_time\n"
    "- 含「发货」→ time_type=consign_time\n"
    "- 默认 doc_created_at\n\n"
    "### 降级策略\n"
    "- local 工具返回错误 → 改用 erp 远程工具重试\n"
    "- 连续 2 次空结果 → 在最终回复中说明未找到数据，建议缩小范围\n\n"
    "### 参数充分度判断\n"
    "- 参数充分 → 直接查\n"
    "- 可推断且无歧义 → 直接查，结果中说明假设\n"
    "- 有歧义 → 在最终回复中列出可能的选项，建议用户明确\n\n"
    "### ERP 远程工具协议\n"
    "1. 两步查询：先传 action 拿参数文档 → 再传 params 执行\n"
    "2. page/page_size 在 tool 级别传，不放 params 里\n\n"
    "### 编码识别\n"
    "- 裸值编码/单号 → 先 local_product_identify(code=XX) 确认类型\n"
    "- 套件无独立库存 → 查子单品逐个查\n\n"
    "### 规则\n"
    "- 禁止猜测参数值\n"
    "- 参数明确时直接查询，禁止试探性查询\n"
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
        request_ctx: Optional["RequestContext"] = None,
        budget: Optional["ExecutionBudget"] = None,
    ) -> None:
        self.db = db
        self.user_id = user_id
        self.conversation_id = conversation_id
        self.org_id = org_id
        self.task_id = task_id
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

        # 1. 构建工具列表（ERP 域 + code_execute）
        all_tools = get_erp_agent_tools(org_id=self.org_id)

        # 2. 创建 LLM adapter
        adapter = create_chat_adapter(
            settings.agent_loop_model, org_id=self.org_id, db=self.db,
        )

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

            # 6. 执行工具循环
            tools_called: List[str] = []
            loop_result = await tool_loop.run(
                messages=messages,
                selected_tools=all_tools,
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

            # 9. LoopResult → AgentResult
            return self._convert_result(loop_result)
        finally:
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
        - task_id=None：不推送 WebSocket 进度（防止与主 Agent 冲突）
        - 只挂 ToolAuditHook（审计日志）
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
                enable_tool_expansion=False,     # 工具列表固定
                force_tool_use_first=True,       # 必须查数据
            ),
            hooks=[ToolAuditHook()],
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
            + "\n" + _ERP_AGENT_ROUTING_RULES
            + "\n\n## 当前时间\n" + time_injection
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
