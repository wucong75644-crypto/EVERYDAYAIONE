"""
多维执行预算管理器

为 Agent 工具循环提供两维预算控制（对标大厂方案）：
- max_turns: 轮次上限（主控制，对标 OpenAI Agents SDK）
- max_wall_time: 时间上限（纯兜底，正常不触发）

上下文大小由压缩器基于模型窗口控制（每轮压缩到窗口内），
不需要累计 token 限制——max_turns 天然封顶总消耗。

设计原则（基于行业调研）：
- 轮次为主控：确定性强，不受网络波动影响
- 时间只做安全网：兜底，不作为正常退出条件
- 子 Agent 共享父墙钟：fork() 分割轮次，共享时间
"""

from __future__ import annotations

import time
from typing import Optional

from loguru import logger


class ExecutionBudget:
    """两维执行预算管理器（turns + wall_time）"""

    def __init__(
        self,
        max_turns: int = 15,
        max_wall_time: float = 600.0,
        wrap_up_turns_reserved: int = 1,
    ) -> None:
        self._max_turns = max_turns
        self._max_wall_time = max_wall_time
        self._wrap_up_turns_reserved = wrap_up_turns_reserved
        self._turns_used = 0
        self._start = time.monotonic()

    # ========================================
    # 消耗记录
    # ========================================

    def use_turn(self) -> None:
        """记录一轮工具调用"""
        self._turns_used += 1

    # ========================================
    # 状态查询
    # ========================================

    @property
    def turns_used(self) -> int:
        return self._turns_used

    @property
    def turns_remaining(self) -> int:
        return max(0, self._max_turns - self._turns_used)

    @property
    def elapsed(self) -> float:
        """已用时间（秒）"""
        return time.monotonic() - self._start

    @property
    def remaining(self) -> float:
        """剩余墙钟时间（秒），最小 0"""
        return max(0.0, self._max_wall_time - self.elapsed)

    @property
    def is_expired(self) -> bool:
        """预算是否已耗尽（任意维度）"""
        return self.stop_reason is not None

    @property
    def stop_reason(self) -> Optional[str]:
        """返回第一个触发的限制，或 None（还能继续）

        优先级：wrap_up_budget（提前预留）> max_turns > wall_timeout
        wrap_up_budget 在 max_turns - reserved 时触发，预留轮次给 synthesize_wrap_up。
        """
        if self._turns_used >= self._max_turns:
            return "max_turns"     # 最终兜底（wrap_up 合成也用完时）
        if (
            self._wrap_up_turns_reserved > 0
            and self._turns_used >= self._max_turns - self._wrap_up_turns_reserved
        ):
            return "wrap_up_budget"
        if self.elapsed >= self._max_wall_time:
            return "wall_timeout"
        return None

    # ========================================
    # 子 Agent 预算分割
    # ========================================

    def fork(
        self,
        max_turns: int = 10,
    ) -> ExecutionBudget:
        """从父预算中分割子 Agent 预算

        子 Agent 特性：
        - 独立的轮次上限（不超过父剩余轮次）
        - 共享父的墙钟时间（子不能比父活久）
        """
        child = ExecutionBudget(
            max_turns=min(max_turns, self.turns_remaining),
            max_wall_time=self.remaining,
            wrap_up_turns_reserved=self._wrap_up_turns_reserved,
        )
        return child

    # ========================================
    # 工具超时计算
    # ========================================

    def tool_timeout(self, max_per_tool: float = 30.0) -> float:
        """计算单工具超时 = min(per_tool_limit, wall_remaining)

        Args:
            max_per_tool: 单工具最大超时（秒）

        Returns:
            实际应使用的超时值（秒），至少 1.0
        """
        timeout = min(max_per_tool, self.remaining)
        return max(1.0, timeout)

    # ========================================
    # 向后兼容旧接口
    # ========================================

    def check_or_log(self, context: str) -> bool:
        """检查预算并记录日志（向后兼容旧调用方）

        Returns:
            True 如果预算充足，False 如果已耗尽
        """
        reason = self.stop_reason
        if reason:
            logger.warning(
                f"ExecutionBudget expired | context={context} | "
                f"reason={reason} | turns={self._turns_used}/{self._max_turns} | "
                f"elapsed={self.elapsed:.1f}s/{self._max_wall_time}s"
            )
            return False
        return True
