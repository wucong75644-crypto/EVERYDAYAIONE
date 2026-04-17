"""
DAG 执行引擎。

按 ExecutionPlan 的 Round 顺序执行部门 Agent：
- Round 之间按依赖串行
- Round 内多个 Agent 并行（asyncio.gather）
- 错误传播：ERROR 跳过依赖它的后续 Round
- PARTIAL 阈值：数据量 <10% 预期 → 按 ERROR 处理

设计文档: docs/document/TECH_多Agent单一职责重构.md §9.2 / §13.6
"""
from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

from services.agent.department_agent import DepartmentAgent
from services.agent.execution_plan import ExecutionPlan, Round
from services.agent.session_file_registry import SessionFileRegistry
from services.agent.tool_output import OutputStatus, ToolOutput


class DAGExecutor:
    """DAG 执行引擎。

    agents: 域名 → DepartmentAgent 实例的映射
    query:  用户原始查询
    file_registry: 共享文件注册表（部门Agent写的文件自动注册，
                   ComputeAgent 可通过 registry 查找）
    """

    def __init__(
        self,
        agents: dict[str, DepartmentAgent],
        query: str,
        round_timeout: float = 30.0,
        compute_timeout: float = 120.0,
        file_registry: SessionFileRegistry | None = None,
        deadline: float | None = None,
    ):
        self._agents = agents
        self._query = query
        self._round_timeout = round_timeout
        self._compute_timeout = compute_timeout
        self._file_registry = file_registry
        self._deadline = deadline

    async def run(
        self,
        plan: ExecutionPlan,
        task_id: str | None = None,
    ) -> DAGResult:
        """按 ExecutionPlan 执行，返回 DAGResult。

        task_id: 可选，传入后在 Round 间检查用户打断（steer）。
        注意：Round 内并行执行期间暂不支持打断，
        响应延迟最长等于单个 Round 超时时间。
        """
        if plan.is_abort:
            return DAGResult(
                outputs=[],
                summary=plan.abort_message,
                status="error",
            )

        round_results: dict[int, list[ToolOutput]] = {}

        for i, rnd in enumerate(plan.rounds):
            # 收集前序依赖的输出作为 context
            context: list[ToolOutput] = []
            for dep_idx in rnd.depends_on:
                context.extend(round_results.get(dep_idx, []))

            # ── ERROR 传播检查 ──
            error_inputs = [
                c for c in context if c.status == OutputStatus.ERROR
            ]
            if error_inputs:
                sources = ", ".join(c.source for c in error_inputs)
                round_results[i] = [ToolOutput(
                    summary=f"跳过：依赖的 {sources} 查询失败",
                    status=OutputStatus.ERROR,
                    source="dag_executor",
                    error_message=error_inputs[0].error_message,
                )]
                continue

            # ── PARTIAL 阈值检查 ──
            skip_round = False
            for p in (c for c in context if c.status == OutputStatus.PARTIAL):
                expected = p.metadata.get("total_expected")
                if expected is None:
                    continue
                actual = (
                    len(p.data or []) if p.data
                    else (p.file_ref.row_count if p.file_ref else 0)
                )
                if expected > 0 and actual < expected * 0.1:
                    round_results[i] = [ToolOutput(
                        summary=(
                            f"{p.source} 数据严重不完整"
                            f"（{actual}/{expected}行），跳过后续分析"
                        ),
                        status=OutputStatus.ERROR,
                        source="dag_executor",
                    )]
                    skip_round = True
                    break
            if skip_round:
                continue

            # ── 执行当前 Round（per-Agent 超时，已完成的结果保留）──
            results = await self._execute_round(rnd, context)
            round_results[i] = results

            # ── DAG 级打断检查（Round 间）──
            # 限制：Round 内多个 Agent 并行执行时无法中断，
            # 最长等待 = max(agent_timeout) ≈ round_timeout
            if task_id:
                from services.websocket_manager import ws_manager
                steer_msg = ws_manager.check_steer(task_id)
                if steer_msg:
                    logger.info(
                        f"DAG steer at Round {i} | "
                        f"msg={steer_msg[:50]}",
                    )
                    done_outputs = [
                        out for outs in round_results.values()
                        for out in outs
                    ]
                    remaining = len(plan.rounds) - i - 1
                    summaries = [
                        o.summary for o in done_outputs if o.summary
                    ]
                    summary = "\n\n".join(summaries)
                    if remaining > 0:
                        summary += (
                            f"\n\n⚠ 用户发送了新消息，"
                            f"跳过剩余 {remaining} 轮。"
                        )
                    return DAGResult(
                        outputs=done_outputs,
                        summary=summary,
                        status="partial",
                    )

        # 正常结束：循环后收集
        all_outputs = [
            out for outs in round_results.values() for out in outs
        ]
        return self._build_result(round_results, all_outputs)

    async def _execute_round(
        self, rnd: Round, context: list[ToolOutput],
    ) -> list[ToolOutput]:
        """执行单个 Round。

        每个 Agent 独立超时（compute 域用 compute_timeout，其余用 round_timeout）。
        一个 Agent 超时不影响其他 Agent，已完成的结果保留。
        """
        tasks = [
            self._execute_agent_with_timeout(
                domain, rnd.task, context, rnd.params,
            )
            for domain in rnd.agents
        ]
        if len(tasks) == 1:
            return [await tasks[0]]
        return list(await asyncio.gather(*tasks))

    async def _execute_agent_with_timeout(
        self, domain: str, task: str, context: list[ToolOutput],
        params: dict | None = None,
    ) -> ToolOutput:
        """执行单个 Agent（带独立超时 + deadline 协调）。"""
        config_timeout = (
            self._compute_timeout if domain == "compute"
            else self._round_timeout
        )
        # 协调 deadline：不能超过 DAG 全局剩余时间
        if self._deadline:
            import time
            remaining = self._deadline - time.monotonic()
            if remaining < 5.0:
                logger.warning(
                    f"DAG budget exhausted before {domain} "
                    f"(remaining={remaining:.1f}s)",
                )
                return ToolOutput(
                    summary=f"DAG 全局超时，跳过 {domain}",
                    source=domain,
                    status=OutputStatus.ERROR,
                    error_message=(
                        f"DAG deadline exceeded, {remaining:.1f}s left"
                    ),
                )
            timeout = min(config_timeout, remaining)
        else:
            timeout = config_timeout
        try:
            result = await asyncio.wait_for(
                self._execute_agent(domain, task, context, params),
                timeout=timeout,
            )
            # 执行成功后，自动注册 FILE_REF 到共享 registry
            if self._file_registry and result.file_ref:
                self._file_registry.register(
                    domain, "execute", result.file_ref,
                )
            return result
        except asyncio.TimeoutError:
            logger.warning(f"DAG agent {domain} timeout ({timeout}s)")
            return ToolOutput(
                summary=f"{domain} 查询超时（{timeout:.0f}秒）",
                source=domain,
                status=OutputStatus.ERROR,
                error_message=f"{domain} timeout after {timeout}s",
            )
        except Exception as e:
            logger.opt(exception=True).error(
                f"DAG agent {domain} exception | task={task[:50]}",
            )
            is_known = isinstance(
                e, (ValueError, PermissionError, ConnectionError),
            )
            error_msg = (
                str(e) if is_known
                else f"内部错误，请联系管理员（{type(e).__name__}）"
            )
            return ToolOutput(
                summary=f"{domain} 执行异常: {error_msg}",
                source=domain,
                status=OutputStatus.ERROR,
                error_message=str(e),
            )

    async def _execute_agent(
        self, domain: str, task: str, context: list[ToolOutput],
        params: dict | None = None,
    ) -> ToolOutput:
        """执行单个 Agent。"""
        agent = self._agents.get(domain)
        if not agent:
            return ToolOutput(
                summary=f"未知域: {domain}",
                source=domain,
                status=OutputStatus.ERROR,
                error_message=f"unknown domain: {domain}",
            )

        logger.info(
            f"DAG executing | domain={domain} | task={task[:50]}",
        )
        # ComputeAgent 有不同的接口（execute_from_dag）
        from services.agent.compute_agent import ComputeAgent
        if isinstance(agent, ComputeAgent):
            return await agent.execute_from_dag(task, context=context)
        return await agent.execute(
            task, context=context, dag_mode=True, params=params,
        )

    def _build_result(
        self,
        round_results: dict[int, list[ToolOutput]],
        all_outputs: list[ToolOutput],
    ) -> DAGResult:
        """按 Round 索引升序找根因，构建最终结果。"""
        # 找根因 ERROR
        for round_idx in sorted(round_results.keys()):
            errors = [
                o for o in round_results[round_idx]
                if o.status == OutputStatus.ERROR
            ]
            if errors:
                error_details = [
                    f"{e.source}: {e.error_message}" for e in errors
                ]
                cascade = sum(
                    1 for idx in round_results if idx > round_idx
                    for o in round_results[idx]
                    if o.status == OutputStatus.ERROR
                )
                summary = "查询未完成：\n" + "\n".join(
                    f"  - {d}" for d in error_details
                )
                if cascade > 0:
                    summary += f"\n（导致后续 {cascade} 个步骤跳过）"
                summary += "\n请修正以上问题后重试。"
                return DAGResult(
                    outputs=all_outputs,
                    summary=summary,
                    status="error",
                )

        # 检查 PARTIAL 警告
        has_partial = any(
            o.status == OutputStatus.PARTIAL for o in all_outputs
        )
        summaries = [o.summary for o in all_outputs if o.summary]
        summary = "\n\n".join(summaries)
        if has_partial:
            summary = "⚠ 部分数据不完整，以下结论仅供参考\n\n" + summary

        return DAGResult(
            outputs=all_outputs,
            summary=summary,
            status="success",
        )


class DAGResult:
    """DAG 执行结果。"""

    def __init__(
        self,
        outputs: list[ToolOutput],
        summary: str,
        status: str = "success",
    ):
        self.outputs = outputs
        self.summary = summary
        self.status = status

    @property
    def is_success(self) -> bool:
        return self.status == "success"

    @property
    def collected_files(self) -> list[dict]:
        """收集所有 FILE_REF 文件信息。"""
        files = []
        for o in self.outputs:
            if o.file_ref:
                files.append({
                    "source": o.source,
                    "path": o.file_ref.path,
                    "filename": o.file_ref.filename,
                    "row_count": o.file_ref.row_count,
                })
        return files
