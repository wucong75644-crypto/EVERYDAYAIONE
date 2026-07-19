"""Validation Runtime 门面与Receipt测试。"""

from services.agent.agent_result import AgentResult
from services.agent.runtime.validation.observation import (
    build_recovery_observation,
)
from services.agent.runtime.validation.runtime import ValidationRuntime
from services.agent.runtime.validation.types import (
    RecoveryDecision,
    ResultClass,
)


def test_runtime_records_retry_decision_and_receipt() -> None:
    runtime = ValidationRuntime(task_id="task-1")
    validated, decision = runtime.observe_result(
        AgentResult(
            summary="执行错误",
            status="error",
            error_message="字段不存在",
            metadata={"retryable": True, "error_code": "BINDER_ERROR"},
        ),
        tool_call_id="call-1",
        tool_name="code_execute",
        model_step=1,
        turns_remaining=5,
        duration_ms=12,
    )

    assert validated.result_class == ResultClass.RETRYABLE
    assert decision == RecoveryDecision.RETRY_MODEL
    assert runtime.receipt_projection() == [
        {
            "task_id": "task-1",
            "model_step": 1,
            "tool_call_id": "call-1",
            "tool_name": "code_execute",
            "stage": "output",
            "result_class": "retryable",
            "decision": "retry_model",
            "attempt": 0,
            "fingerprint": validated.fingerprint,
            "reason_code": "BINDER_ERROR",
            "duration_ms": 12,
        }
    ]


def test_recovery_observation_is_structured_and_bounded() -> None:
    runtime = ValidationRuntime()
    validated, _ = runtime.observe_result(
        AgentResult(
            summary="执行错误",
            status="error",
            error_message="字段不存在",
            metadata={"retryable": True, "error_code": "BINDER_ERROR"},
        ),
        tool_call_id="call-1",
        tool_name="code_execute",
        model_step=1,
        turns_remaining=5,
    )

    observation = build_recovery_observation(validated)

    assert '"type":"tool_validation_error"' in observation
    assert '"error_code":"BINDER_ERROR"' in observation
    assert "system_fact" not in observation
