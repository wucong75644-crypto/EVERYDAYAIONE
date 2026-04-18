"""
ERP 独立 Agent — DAG 编排模式。

意图分析 → ExecutionPlan → 部门 Agent 并行调度 → 汇总结果。
类型/常量见 erp_agent_types.py。
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from utils.time_context import RequestContext

from services.agent.erp_agent_types import ERPAgentResult


class ERPAgent:
    """ERP 独立 Agent — DAG 编排：意图分析 + 部门Agent调度 + 结果汇总"""

    def __init__(
        self,
        db: Any,
        user_id: str,
        conversation_id: str,
        org_id: str,
        task_id: Optional[str] = None,
        request_ctx: Optional["RequestContext"] = None,
    ) -> None:
        self.db = db
        self.user_id = user_id
        self.conversation_id = conversation_id
        self.org_id = org_id
        self.task_id = task_id
        # 时间事实层：请求级 SSOT。入口未传时构造一个新的（向后兼容）。
        # 设计文档：docs/document/TECH_ERP时间准确性架构.md §4 / §6.2.4
        from utils.time_context import RequestContext
        self.request_ctx = request_ctx or RequestContext.build(
            user_id=user_id, org_id=org_id, request_id=task_id or "",
        )
        # 经验记录器（Phase 2 提取，D16）
        from services.agent.experience_recorder import ExperienceRecorder
        self._experience = ExperienceRecorder(org_id=org_id, writer="erp_agent")

    async def execute(
        self,
        query: str,
        **_kwargs: Any,
    ) -> ERPAgentResult:
        """执行 ERP 查询（DAG 编排模式）。

        **_kwargs 兼容旧调用方传 parent_messages（已无用，不报错）。
        """
        if not self.org_id:
            return ERPAgentResult(
                text="当前账号未开通 ERP 功能，请联系管理员配置企业账号。",
                status="error",
            )

        import time as _time
        from core.config import get_settings
        _timeout = get_settings().dag_global_timeout
        _deadline = _time.monotonic() + _timeout
        try:
            return await asyncio.wait_for(
                self._execute_dag(query, deadline=_deadline),
                timeout=_timeout,
            )
        except asyncio.TimeoutError:
            return ERPAgentResult(
                text=f"查询超时（{_timeout:.0f}秒），请缩小查询范围后重试",
                status="error",
            )

    # ── DAG 执行 ──

    async def _execute_dag(
        self, query: str, deadline: float | None = None,
    ) -> ERPAgentResult:
        """意图分析 → 构建执行计划 → 按 Round 调度部门 Agent → 汇总。"""
        from services.agent.compute_agent import ComputeAgent
        from services.agent.dag_executor import DAGExecutor
        from services.agent.plan_builder import PlanBuilder
        from services.agent.session_file_registry import SessionFileRegistry
        from services.agent.departments.warehouse_agent import WarehouseAgent
        from services.agent.departments.purchase_agent import PurchaseAgent
        from services.agent.departments.trade_agent import TradeAgent
        from services.agent.departments.aftersale_agent import AftersaleAgent
        from core.workspace import resolve_staging_dir
        from core.config import get_settings as _gs

        _s = _gs()
        staging_dir = resolve_staging_dir(
            _s.file_workspace_root,
            self.user_id, self.org_id, self.conversation_id,
        )

        # 1. 构建部门 Agent + ComputeAgent 实例
        # 共享 registry：DAG 全生命周期传递，部门Agent写的文件
        # ComputeAgent 能看到
        shared_registry = SessionFileRegistry()

        agents: dict = {
            "warehouse": WarehouseAgent(
                db=self.db, org_id=self.org_id,
                request_ctx=self.request_ctx,
            ),
            "purchase": PurchaseAgent(
                db=self.db, org_id=self.org_id,
                request_ctx=self.request_ctx,
            ),
            "trade": TradeAgent(
                db=self.db, org_id=self.org_id,
                request_ctx=self.request_ctx,
            ),
            "aftersale": AftersaleAgent(
                db=self.db, org_id=self.org_id,
                request_ctx=self.request_ctx,
            ),
            "compute": ComputeAgent(
                staging_dir=staging_dir,
                file_registry=shared_registry,
                request_ctx=self.request_ctx,
                user_id=self.user_id,
                org_id=self.org_id,
                conversation_id=self.conversation_id,
            ),
        }

        # 2. 意图分析 → 生成执行计划（三级降级链）
        # 用 LLM 分析多域查询意图；单域或 LLM 失败时降级到关键词
        from services.adapters.factory import create_chat_adapter
        from core.config import settings
        plan_adapter = create_chat_adapter(
            settings.agent_loop_model, org_id=self.org_id, db=self.db,
        )
        try:
            builder = PlanBuilder(
                adapter=plan_adapter,
                request_ctx=self.request_ctx,
            )
            plan = await builder.build(query)
        finally:
            await plan_adapter.close()

        logger.info(f"ERPAgent DAG plan | {plan.describe()}")

        if plan.is_abort:
            return ERPAgentResult(
                text=plan.abort_message,
                status="error",
            )

        # 2.5 准入校验：DB 验证 product_code / order_no
        from services.agent.plan_builder import _fill_codes
        await _fill_codes(plan, query, self.db, self.org_id)

        # 3. DAG 执行
        executor = DAGExecutor(
            agents=agents, query=query,
            round_timeout=_s.dag_round_timeout,
            compute_timeout=_s.dag_compute_timeout,
            file_registry=shared_registry,
            deadline=deadline,
        )
        dag_result = await executor.run(plan, task_id=self.task_id)

        # 4. 经验记录
        domains_called = [
            a for rnd in plan.rounds for a in rnd.agents
        ]
        if dag_result.is_success:
            asyncio.create_task(self._experience.record(
                "routing", query, domains_called,
                f"DAG模式，{len(plan.rounds)}轮",
                confidence=0.6,
            ))
        else:
            asyncio.create_task(self._experience.record(
                "failure", query, domains_called,
                f"DAG失败：{dag_result.summary[:200]}",
            ))

        # 5. 汇总 token 消耗（PlanBuilder + ComputeAgent 的 LLM 调用）
        total_tokens = builder.tokens_used
        compute_agent = agents.get("compute")
        if compute_agent and hasattr(compute_agent, "_tokens_used"):
            total_tokens += compute_agent._tokens_used

        return ERPAgentResult(
            text=dag_result.summary,
            full_text=dag_result.summary,
            status=dag_result.status,
            tokens_used=total_tokens,
            tools_called=domains_called,
            collected_files=[
                {
                    "url": o.file_ref.path,
                    "name": o.file_ref.filename,
                    "mime_type": "application/octet-stream",
                    "size": o.file_ref.size_bytes,
                }
                for o in dag_result.outputs
                if o.file_ref
            ],
        )

    async def _cleanup_staging_delayed(self, delay: int = 300) -> None:
        """会话级 staging 延迟清理：等待后删除本次会话的 staging 文件

        延迟 5 分钟，避免 Agent 超时退出但 code_execute 还没读到 staging 文件。
        """
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
                    f"ERPAgent staging cleaned | dir={staging_dir}"
                )
        except Exception as e:
            logger.debug(f"ERPAgent staging cleanup failed | error={e}")
