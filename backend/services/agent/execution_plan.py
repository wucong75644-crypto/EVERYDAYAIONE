"""
DAG 执行计划。

ERPAgent 路由层用 LLM 分析意图 → 生成 ExecutionPlan → 按 Round 顺序执行。
Round 内多个 Agent 并行（asyncio.gather），Round 之间按依赖串行。

设计文档: docs/document/TECH_多Agent单一职责重构.md §9.2 / §13.7 / §13.8
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ── DAG 限制 ──
MAX_ROUNDS = 5
MAX_AGENTS_PER_ROUND = 4
MAX_TOTAL_AGENTS = 10


@dataclass
class Round:
    """DAG 中的一轮执行。

    agents:     本轮需要执行的 Agent 域标识列表（如 ["warehouse", "purchase"]）
    task:       本轮任务描述（自然语言，LLM 生成）
    depends_on: 依赖的前序 Round 索引列表（必须 < 当前索引）
    params:     静态查询参数（PlanBuilder LLM 输出）
                包含 mode/doc_type/time_range/time_col/platform 等。
                动态参数（product_code 等）由部门 Agent 从 context 提取。
    """
    agents: list[str]
    task: str = ""
    depends_on: list[int] = field(default_factory=list)
    params: dict = field(default_factory=dict)


class PlanValidationError(Exception):
    """ExecutionPlan 校验失败"""


@dataclass
class ExecutionPlan:
    """DAG 执行计划。

    由 _plan_execution 生成（LLM 规划 → 降级链）。
    支持 abort（无法理解请求时直接返回错误）。
    """
    rounds: list[Round] = field(default_factory=list)
    abort_message: str = ""

    @property
    def is_abort(self) -> bool:
        """是否为中止计划（无法理解请求）"""
        return bool(self.abort_message)

    @property
    def total_agents(self) -> int:
        return sum(len(r.agents) for r in self.rounds)

    @property
    def is_single_domain(self) -> bool:
        """是否为单域直通（只有 1 个 Round、1 个 Agent）"""
        return (
            len(self.rounds) == 1
            and len(self.rounds[0].agents) == 1
            and not self.is_abort
        )

    @classmethod
    def abort(cls, message: str) -> ExecutionPlan:
        """创建中止计划"""
        return cls(abort_message=message)

    @classmethod
    def single(cls, domain: str, task: str = "") -> ExecutionPlan:
        """创建单域直通计划"""
        return cls(rounds=[Round(agents=[domain], task=task)])

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExecutionPlan:
        """从 LLM 返回的 JSON dict 解析。

        格式：
        {
          "rounds": [
            {"agents": ["aftersale"], "task": "查退货数据", "depends_on": []},
            {"agents": ["warehouse", "purchase"], "task": "查库存和采购", "depends_on": [0]},
            {"agents": ["compute"], "task": "合并对比导出", "depends_on": [0, 1]}
          ]
        }
        """
        rounds = []
        for r in data.get("rounds", []):
            rounds.append(Round(
                agents=r.get("agents", []),
                task=r.get("task", ""),
                depends_on=r.get("depends_on", []),
                params=r.get("params", {}),
            ))
        return cls(rounds=rounds)

    def validate(self) -> None:
        """校验 DAG 合法性。

        检查项：
        1. Round 数量 ≤ MAX_ROUNDS
        2. 每 Round Agent 数量 ≤ MAX_AGENTS_PER_ROUND
        3. 总 Agent 调用 ≤ MAX_TOTAL_AGENTS
        4. 依赖关系无环（depends_on 必须指向更小的索引）
        5. 每 Round 至少有 1 个 Agent
        """
        if self.is_abort:
            return

        if len(self.rounds) > MAX_ROUNDS:
            raise PlanValidationError(
                f"DAG 不能超过 {MAX_ROUNDS} 轮（当前 {len(self.rounds)} 轮）",
            )

        for i, rnd in enumerate(self.rounds):
            if len(rnd.agents) > MAX_AGENTS_PER_ROUND:
                raise PlanValidationError(
                    f"Round {i} 不能超过 {MAX_AGENTS_PER_ROUND} 个 Agent"
                    f"（当前 {len(rnd.agents)} 个）",
                )
            if not rnd.agents:
                raise PlanValidationError(
                    f"Round {i} 没有 Agent",
                )
            for dep in rnd.depends_on:
                if dep >= i:
                    raise PlanValidationError(
                        f"Round {i} 依赖了 Round {dep}（≥自身索引），"
                        f"DAG 有环或前向引用",
                    )
                if dep < 0 or dep >= len(self.rounds):
                    raise PlanValidationError(
                        f"Round {i} 依赖了不存在的 Round {dep}",
                    )

        total = self.total_agents
        if total > MAX_TOTAL_AGENTS:
            raise PlanValidationError(
                f"总 Agent 调用不能超过 {MAX_TOTAL_AGENTS} 次"
                f"（当前 {total} 次）",
            )

    def describe(self) -> str:
        """生成可读的执行计划描述（用于日志/调试）。"""
        if self.is_abort:
            return f"[ABORT] {self.abort_message}"
        lines = [f"ExecutionPlan（{len(self.rounds)} 轮，共 {self.total_agents} 个 Agent）:"]
        for i, rnd in enumerate(self.rounds):
            dep = f" ← 依赖 Round {rnd.depends_on}" if rnd.depends_on else ""
            agents = ", ".join(rnd.agents)
            lines.append(f"  Round {i}: [{agents}]{dep} — {rnd.task}")
        return "\n".join(lines)
