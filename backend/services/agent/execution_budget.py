"""
多维执行预算管理器

为 Agent 工具循环提供多维预算控制：
- max_turns: 轮次上限（主控制，对标 OpenAI Agents SDK）
- max_tokens: token 上限（安全网，对标 Claude Agent SDK）
- max_wall_time: 时间上限（纯兜底，正常不触发）

设计原则（基于行业调研）：
- 轮次为主控：确定性强，不受网络波动影响
- Token 为辅助：防止单轮产出巨量 token
- 时间只做安全网：兜底，不作为正常退出条件
- 子 Agent 共享父预算：fork() 分割，子消耗回写父
"""

from __future__ import annotations

import time
from typing import Optional

from loguru import logger


class ExecutionBudget:
    """多维执行预算管理器"""

    # v6: 上下文紧张阈值（token 剩余低于此值时收紧 inline/wrap 策略）
    TIGHT_THRESHOLD = 15000

    def __init__(
        self,
        max_turns: int = 15,
        max_tokens: int = 200_000,
        max_wall_time: float = 600.0,
        reserved_for_response: int = 4000,
        wrap_up_turns_reserved: int = 1,
    ) -> None:
        self._max_turns = max_turns
        self._max_tokens = max_tokens
        self._max_wall_time = max_wall_time
        self._reserved_for_response = reserved_for_response
        self._wrap_up_turns_reserved = wrap_up_turns_reserved
        self._turns_used = 0
        self._tokens_used = 0
        self._per_tool_tokens: dict[str, int] = {}
        self._start = time.monotonic()
        self._parent: Optional[ExecutionBudget] = None

    # ========================================
    # 消耗记录
    # ========================================

    def use_turn(self) -> None:
        """记录一轮工具调用"""
        self._turns_used += 1

    def use_tokens(self, n: int, tool_name: str = "") -> None:
        """记录 token 消耗（自动回写父 budget + per-tool 统计）"""
        self._tokens_used += n
        if tool_name:
            self._per_tool_tokens[tool_name] = (
                self._per_tool_tokens.get(tool_name, 0) + n
            )
        if self._parent is not None:
            self._parent.use_tokens(n, tool_name)

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
    def tokens_used(self) -> int:
        return self._tokens_used

    @property
    def tokens_remaining(self) -> int:
        """可用 token（已扣除 reserved_for_response）"""
        return max(0, self._max_tokens - self._tokens_used - self._reserved_for_response)

    @property
    def is_tight(self) -> bool:
        """上下文是否紧张（token 剩余 < TIGHT_THRESHOLD）"""
        return self.tokens_remaining < self.TIGHT_THRESHOLD

    @property
    def inline_threshold(self) -> int:
        """v6 两档 inline 切换（对标 Claude Code 容量分层）。

        正常：200 行（inline）；紧张：50 行（更积极 staging）。
        """
        return 50 if self.is_tight else 200

    def get_tool_tokens(self) -> dict[str, int]:
        """查询 per-tool token 消耗"""
        return dict(self._per_tool_tokens)

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

        优先级：wrap_up_budget（提前预留）> max_turns > max_tokens > wall_timeout
        wrap_up_budget 在 max_turns - reserved 时触发，预留轮次给 synthesize_wrap_up。
        """
        if self._turns_used >= self._max_turns:
            return "max_turns"     # 最终兜底（wrap_up 合成也用完时）
        if (
            self._wrap_up_turns_reserved > 0
            and self._turns_used >= self._max_turns - self._wrap_up_turns_reserved
        ):
            return "wrap_up_budget"
        if self._tokens_used >= self._max_tokens:
            return "max_tokens"
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
        - 共享父的 token 预算（子消耗自动回写父）
        - 共享父的墙钟时间（子不能比父活久）
        """
        child = ExecutionBudget(
            max_turns=min(max_turns, self.turns_remaining),
            max_tokens=self.tokens_remaining + self._reserved_for_response,
            max_wall_time=self.remaining,
            reserved_for_response=self._reserved_for_response,
            wrap_up_turns_reserved=self._wrap_up_turns_reserved,
        )
        child._parent = self
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
                f"tokens={self._tokens_used}/{self._max_tokens} | "
                f"elapsed={self.elapsed:.1f}s/{self._max_wall_time}s"
            )
            return False
        return True
