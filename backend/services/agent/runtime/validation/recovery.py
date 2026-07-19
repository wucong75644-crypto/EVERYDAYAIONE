"""根据统一结果、进展和预算生成纯恢复决策。"""

from __future__ import annotations

from services.agent.runtime.validation.tracker import ValidationTracker
from services.agent.runtime.validation.types import (
    RecoveryDecision,
    RecoveryPolicy,
    ResultClass,
    ValidatedToolResult,
)


def decide_recovery(
    result: ValidatedToolResult,
    tracker: ValidationTracker,
    *,
    turns_remaining: int,
    policy: RecoveryPolicy | None = None,
) -> RecoveryDecision:
    config = policy or RecoveryPolicy()
    if result.result_class in {ResultClass.SUCCESS, ResultClass.PARTIAL}:
        return RecoveryDecision.CONTINUE
    if result.result_class == ResultClass.CANCELLED:
        return RecoveryDecision.CANCEL
    if result.result_class in {ResultClass.NEEDS_INPUT, ResultClass.AMBIGUOUS}:
        return RecoveryDecision.NEEDS_INPUT
    if result.result_class == ResultClass.UNKNOWN:
        return RecoveryDecision.WRAP_UP
    if result.result_class == ResultClass.FATAL:
        return (
            RecoveryDecision.WRAP_UP
            if tracker.has_meaningful_progress
            else RecoveryDecision.FAIL
        )
    if result.result_class != ResultClass.RETRYABLE:
        return RecoveryDecision.FAIL
    if tracker.same_error_streak > config.max_same_error_retries:
        return RecoveryDecision.WRAP_UP
    if tracker.consecutive_failures >= config.max_consecutive_failures:
        return RecoveryDecision.WRAP_UP
    if turns_remaining <= config.wrap_up_turns_reserved:
        return RecoveryDecision.WRAP_UP
    return RecoveryDecision.RETRY_MODEL
