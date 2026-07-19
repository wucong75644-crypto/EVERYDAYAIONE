"""Validation Runtime 协议与恢复策略测试。"""

import pytest

from services.agent.runtime.validation.recovery import decide_recovery
from services.agent.runtime.validation.tracker import ValidationTracker
from services.agent.runtime.validation.types import (
    RecoveryDecision,
    RecoveryPolicy,
    ResultClass,
    ToolEffect,
    ValidatedToolResult,
    ValidationReceipt,
    ValidationStage,
)


def _result(
    result_class: ResultClass,
    *,
    fingerprint: str = "fp",
) -> ValidatedToolResult:
    return ValidatedToolResult(
        tool_call_id="call-1",
        requested_tool_name="tool",
        effective_tool_name="tool",
        stage=ValidationStage.OUTPUT,
        result_class=result_class,
        effect=ToolEffect.READ_ONLY,
        terminal=True,
        system_fact={},
        observation="result",
        retryable=result_class == ResultClass.RETRYABLE,
        fingerprint=fingerprint,
    )


@pytest.mark.parametrize(
    ("result_class", "decision"),
    [
        (ResultClass.SUCCESS, RecoveryDecision.CONTINUE),
        (ResultClass.PARTIAL, RecoveryDecision.CONTINUE),
        (ResultClass.NEEDS_INPUT, RecoveryDecision.NEEDS_INPUT),
        (ResultClass.AMBIGUOUS, RecoveryDecision.NEEDS_INPUT),
        (ResultClass.CANCELLED, RecoveryDecision.CANCEL),
        (ResultClass.UNKNOWN, RecoveryDecision.WRAP_UP),
        (ResultClass.FATAL, RecoveryDecision.FAIL),
    ],
)
def test_recovery_decisions(
    result_class: ResultClass,
    decision: RecoveryDecision,
) -> None:
    tracker = ValidationTracker()
    result = _result(result_class)
    tracker.observe(result)

    assert decide_recovery(
        result,
        tracker,
        turns_remaining=5,
    ) == decision


def test_retryable_allows_one_model_correction_then_wraps_up() -> None:
    tracker = ValidationTracker()
    first = _result(ResultClass.RETRYABLE)
    tracker.observe(first)
    assert decide_recovery(
        first, tracker, turns_remaining=5,
    ) == RecoveryDecision.RETRY_MODEL

    second = _result(ResultClass.RETRYABLE)
    tracker.observe(second)
    assert decide_recovery(
        second, tracker, turns_remaining=4,
    ) == RecoveryDecision.WRAP_UP


def test_retryable_wraps_up_when_failure_or_turn_budget_is_exhausted() -> None:
    failure_tracker = ValidationTracker()
    failure = _result(ResultClass.RETRYABLE)
    failure_tracker.observe(failure)
    failure_tracker.consecutive_failures = 3

    assert decide_recovery(
        failure,
        failure_tracker,
        turns_remaining=5,
    ) == RecoveryDecision.WRAP_UP

    turn_tracker = ValidationTracker()
    turn_tracker.observe(failure)
    assert decide_recovery(
        failure,
        turn_tracker,
        turns_remaining=1,
    ) == RecoveryDecision.WRAP_UP


def test_success_resets_failure_streak_and_marks_progress() -> None:
    tracker = ValidationTracker()
    tracker.observe(_result(ResultClass.RETRYABLE))
    tracker.observe(_result(ResultClass.SUCCESS, fingerprint="success"))

    assert tracker.consecutive_failures == 0
    assert tracker.same_error_streak == 0
    assert tracker.has_meaningful_progress is True


def test_fatal_after_progress_wraps_up_instead_of_hard_fail() -> None:
    tracker = ValidationTracker()
    tracker.observe(_result(ResultClass.SUCCESS))
    fatal = _result(ResultClass.FATAL, fingerprint="fatal")
    tracker.observe(fatal)

    assert decide_recovery(
        fatal, tracker, turns_remaining=5,
    ) == RecoveryDecision.WRAP_UP


def test_policy_rejects_negative_values() -> None:
    with pytest.raises(ValueError):
        RecoveryPolicy(max_same_error_retries=-1)


def test_validated_result_rejects_missing_identity_and_negative_delay() -> None:
    base = {
        "requested_tool_name": "tool",
        "effective_tool_name": "tool",
        "stage": ValidationStage.OUTPUT,
        "result_class": ResultClass.SUCCESS,
        "effect": ToolEffect.READ_ONLY,
        "terminal": True,
        "system_fact": {},
        "observation": "ok",
    }
    with pytest.raises(ValueError, match="tool_call_id"):
        ValidatedToolResult(tool_call_id="", **base)
    with pytest.raises(ValueError, match="requested_tool_name"):
        ValidatedToolResult(
            tool_call_id="call-1",
            **{**base, "requested_tool_name": ""},
        )
    with pytest.raises(ValueError, match="retry_after_seconds"):
        ValidatedToolResult(
            tool_call_id="call-1",
            retry_after_seconds=-1,
            **base,
        )


def test_receipt_rejects_negative_counters() -> None:
    with pytest.raises(ValueError, match="attempt"):
        ValidationReceipt(
            task_id="task-1",
            model_step=0,
            tool_call_id="call-1",
            tool_name="tool",
            stage=ValidationStage.OUTPUT,
            result_class=ResultClass.SUCCESS,
            decision=RecoveryDecision.CONTINUE,
            attempt=-1,
            fingerprint="fp",
            reason_code="OK",
            duration_ms=0,
        )


def test_receipt_projection_contains_only_protocol_fields() -> None:
    receipt = ValidationReceipt(
        task_id="task-1",
        model_step=2,
        tool_call_id="call-1",
        tool_name="code_execute",
        stage=ValidationStage.OUTPUT,
        result_class=ResultClass.RETRYABLE,
        decision=RecoveryDecision.RETRY_MODEL,
        attempt=0,
        fingerprint="abc",
        reason_code="TIMEOUT",
        duration_ms=20,
    )

    assert receipt.to_dict()["result_class"] == "retryable"
    assert "output" not in receipt.to_dict()
