"""
全局执行时间预算

为 Agent 的工具循环提供统一的时间预算管理：
- 跟踪总耗时，防止无限运行
- 动态计算单工具超时 = min(per_tool_limit, remaining)
- 超时时提供清晰的诊断信息
"""

from __future__ import annotations

import time

from loguru import logger


class ExecutionBudget:
    """执行时间预算管理器"""

    def __init__(self, deadline_seconds: float) -> None:
        self._start = time.monotonic()
        self._deadline = deadline_seconds

    @property
    def elapsed(self) -> float:
        """已用时间（秒）"""
        return time.monotonic() - self._start

    @property
    def remaining(self) -> float:
        """剩余时间（秒），最小 0"""
        return max(0.0, self._deadline - self.elapsed)

    @property
    def is_expired(self) -> bool:
        """预算是否已耗尽"""
        return self.remaining <= 0

    def tool_timeout(self, max_per_tool: float = 30.0) -> float:
        """计算单工具超时 = min(per_tool_limit, remaining)

        Args:
            max_per_tool: 单工具最大超时（秒）

        Returns:
            实际应使用的超时值（秒），至少 1.0
        """
        timeout = min(max_per_tool, self.remaining)
        return max(1.0, timeout)  # 至少给 1 秒

    def check_or_log(self, context: str) -> bool:
        """检查预算并记录日志

        Returns:
            True 如果预算充足，False 如果已耗尽
        """
        if self.is_expired:
            logger.warning(
                f"ExecutionBudget expired | context={context} | "
                f"elapsed={self.elapsed:.1f}s | deadline={self._deadline}s"
            )
            return False
        return True
