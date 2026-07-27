"""ModelStep 状态与闭合停止原因。"""

from __future__ import annotations

from enum import StrEnum


class ModelStepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StopReason(StrEnum):
    FINAL = "final"
    TOOL_CALLS = "tool_calls"
    STRUCTURED_FINAL = "structured_final"
    LENGTH = "length"
    CONTENT_FILTER = "content_filter"
    MODEL_REFUSAL = "model_refusal"
    BUDGET_EXHAUSTED = "budget_exhausted"
    CANCELLED = "cancelled"
    PROVIDER_ERROR = "provider_error"
    PROTOCOL_ERROR = "protocol_error"
