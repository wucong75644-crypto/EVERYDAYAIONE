"""Validation Runtime 的稳定内部协议。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ValidationStage(StrEnum):
    INPUT = "input"
    EXECUTION = "execution"
    OUTPUT = "output"
    COMPLETION = "completion"


class ResultClass(StrEnum):
    SUCCESS = "success"
    RETRYABLE = "retryable"
    NEEDS_INPUT = "needs_input"
    AMBIGUOUS = "ambiguous"
    PARTIAL = "partial"
    FATAL = "fatal"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class RecoveryDecision(StrEnum):
    CONTINUE = "continue"
    RETRY_MODEL = "retry_model"
    RETRY_TRANSPORT = "retry_transport"
    NEEDS_INPUT = "needs_input"
    FINALIZE = "finalize"
    WRAP_UP = "wrap_up"
    FAIL = "fail"
    CANCEL = "cancel"


class ToolEffect(StrEnum):
    READ_ONLY = "read_only"
    IDEMPOTENT_WRITE = "idempotent_write"
    NON_IDEMPOTENT_WRITE = "non_idempotent_write"
    ASYNC_EXTERNAL = "async_external"


@dataclass(frozen=True)
class RecoveryPolicy:
    max_same_error_retries: int = 1
    max_consecutive_failures: int = 3
    wrap_up_turns_reserved: int = 1

    def __post_init__(self) -> None:
        for name in (
            "max_same_error_retries",
            "max_consecutive_failures",
            "wrap_up_turns_reserved",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be non-negative")


@dataclass(frozen=True)
class ValidatedToolResult:
    tool_call_id: str
    requested_tool_name: str
    effective_tool_name: str
    stage: ValidationStage
    result_class: ResultClass
    effect: ToolEffect
    terminal: bool
    system_fact: Any
    observation: Any
    error_code: str = ""
    error_message: str = ""
    retryable: bool = False
    retry_after_seconds: float | None = None
    fingerprint: str = ""

    def __post_init__(self) -> None:
        if not self.tool_call_id:
            raise ValueError("tool_call_id is required")
        if not self.requested_tool_name:
            raise ValueError("requested_tool_name is required")
        if self.retry_after_seconds is not None and self.retry_after_seconds < 0:
            raise ValueError("retry_after_seconds must be non-negative")


@dataclass(frozen=True)
class ValidationReceipt:
    task_id: str
    model_step: int
    tool_call_id: str
    tool_name: str
    stage: ValidationStage
    result_class: ResultClass
    decision: RecoveryDecision
    attempt: int
    fingerprint: str
    reason_code: str
    duration_ms: int

    def __post_init__(self) -> None:
        for name in ("model_step", "attempt", "duration_ms"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "model_step": self.model_step,
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "stage": self.stage.value,
            "result_class": self.result_class.value,
            "decision": self.decision.value,
            "attempt": self.attempt,
            "fingerprint": self.fingerprint,
            "reason_code": self.reason_code,
            "duration_ms": self.duration_ms,
        }
