"""Agent Runtime 稳定身份与幂等键类型。"""

from __future__ import annotations

from typing import NewType


SessionId = NewType("SessionId", str)
RunId = NewType("RunId", str)
ModelStepId = NewType("ModelStepId", str)
ActionId = NewType("ActionId", str)
ActionAttemptId = NewType("ActionAttemptId", str)
RuntimeEventId = NewType("RuntimeEventId", str)
IdempotencyKey = NewType("IdempotencyKey", str)
FencingToken = NewType("FencingToken", str)


def require_stable_value(value: str, field_name: str) -> None:
    """拒绝空白稳定身份。"""
    if not value or not value.strip():
        raise ValueError(f"{field_name} is required")
