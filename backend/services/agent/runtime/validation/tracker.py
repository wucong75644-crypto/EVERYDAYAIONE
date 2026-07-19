"""单次Run内的失败与有效进展追踪。"""

from __future__ import annotations

from dataclasses import dataclass

from services.agent.runtime.validation.types import (
    ResultClass,
    ValidatedToolResult,
)


@dataclass
class ValidationTracker:
    consecutive_failures: int = 0
    same_error_streak: int = 0
    total_failures: int = 0
    last_error_fingerprint: str = ""
    has_meaningful_progress: bool = False

    def observe(self, result: ValidatedToolResult) -> int:
        """记录终态并返回当前错误指纹的尝试序号。"""
        if result.result_class == ResultClass.SUCCESS:
            self._record_progress()
            return 0
        if result.result_class == ResultClass.PARTIAL:
            self.has_meaningful_progress = True
        self.consecutive_failures += 1
        self.total_failures += 1
        if result.fingerprint == self.last_error_fingerprint:
            self.same_error_streak += 1
        else:
            self.same_error_streak = 1
            self.last_error_fingerprint = result.fingerprint
        return self.same_error_streak - 1

    def _record_progress(self) -> None:
        self.consecutive_failures = 0
        self.same_error_streak = 0
        self.last_error_fingerprint = ""
        self.has_meaningful_progress = True
