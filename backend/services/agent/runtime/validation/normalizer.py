"""旧工具结果到统一终态协议的确定性归一化。"""

from __future__ import annotations

import asyncio
import hashlib
import re
from typing import Any, Mapping

from services.agent.agent_result import AgentResult
from services.agent.runtime.validation.types import (
    ResultClass,
    ToolEffect,
    ValidatedToolResult,
    ValidationStage,
)


_DYNAMIC_VALUE = re.compile(
    r"\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b"
    r"|\b\d{2,}\b",
    re.IGNORECASE,
)


def normalize_tool_result(
    result: Any,
    *,
    tool_call_id: str,
    tool_name: str,
    audit_status: str = "",
    effect: ToolEffect = ToolEffect.READ_ONLY,
    effective_tool_name: str | None = None,
) -> ValidatedToolResult:
    """从结构化信号归一化结果；文本只作为兼容事实，不做业务语义猜测。"""
    result_class, error_code, error_message, retryable = _classify(
        result,
        audit_status=audit_status,
        effect=effect,
    )
    message = error_message or _summary(result)
    return ValidatedToolResult(
        tool_call_id=tool_call_id,
        requested_tool_name=tool_name,
        effective_tool_name=effective_tool_name or tool_name,
        stage=ValidationStage.OUTPUT,
        result_class=result_class,
        effect=effect,
        terminal=True,
        system_fact=result,
        observation=_observation(result),
        error_code=error_code,
        error_message=error_message,
        retryable=retryable,
        retry_after_seconds=_retry_after(result),
        fingerprint=_fingerprint(tool_name, error_code, message),
    )


def _classify(
    result: Any,
    *,
    audit_status: str,
    effect: ToolEffect,
) -> tuple[ResultClass, str, str, bool]:
    if isinstance(result, asyncio.CancelledError):
        return ResultClass.CANCELLED, "CANCELLED", str(result), False
    if isinstance(result, TimeoutError) or audit_status == "timeout":
        return ResultClass.RETRYABLE, "TIMEOUT", _error_text(result), True
    if isinstance(result, PermissionError):
        return ResultClass.FATAL, "PERMISSION_DENIED", str(result), False
    if isinstance(result, BaseException):
        if effect in {
            ToolEffect.NON_IDEMPOTENT_WRITE,
            ToolEffect.ASYNC_EXTERNAL,
        }:
            return ResultClass.UNKNOWN, "EXECUTION_UNKNOWN", str(result), False
        return ResultClass.RETRYABLE, type(result).__name__.upper(), str(result), True
    if isinstance(result, AgentResult):
        return _classify_agent_result(result)
    if audit_status == "success":
        return ResultClass.SUCCESS, "", "", False
    if audit_status == "cancelled":
        return ResultClass.CANCELLED, "CANCELLED", _error_text(result), False
    if audit_status == "error":
        return ResultClass.RETRYABLE, "LEGACY_TOOL_ERROR", _error_text(result), True
    return ResultClass.SUCCESS, "", "", False


def _classify_agent_result(
    result: AgentResult,
) -> tuple[ResultClass, str, str, bool]:
    metadata = result.metadata if isinstance(result.metadata, dict) else {}
    explicit = _explicit_class(metadata)
    if explicit is not None:
        retryable = explicit == ResultClass.RETRYABLE
        return explicit, _error_code(metadata, result), result.error_message, retryable
    if result.status == "success" or result.status == "plan":
        return ResultClass.SUCCESS, "", "", False
    if result.status in {"empty", "partial"}:
        return ResultClass.PARTIAL, result.status.upper(), result.error_message, False
    if result.status == "timeout":
        return ResultClass.RETRYABLE, "TIMEOUT", result.error_message, True
    if result.status == "cancelled":
        return ResultClass.CANCELLED, "CANCELLED", result.error_message, False
    if result.status == "error":
        retryable = metadata.get("retryable")
        if retryable is False:
            return ResultClass.FATAL, _error_code(metadata, result), result.error_message, False
        return ResultClass.RETRYABLE, _error_code(metadata, result), result.error_message, True
    return ResultClass.FATAL, "INVALID_RESULT_STATUS", result.error_message, False


def _explicit_class(metadata: Mapping[str, Any]) -> ResultClass | None:
    raw = metadata.get("result_class")
    if raw is None:
        return None
    try:
        return ResultClass(str(raw))
    except ValueError:
        return ResultClass.FATAL


def _error_code(metadata: Mapping[str, Any], result: AgentResult) -> str:
    code = metadata.get("error_code")
    if code:
        return str(code)[:64]
    return result.status.upper() if result.status else "TOOL_ERROR"


def _retry_after(result: Any) -> float | None:
    if not isinstance(result, AgentResult):
        return None
    value = result.metadata.get("retry_after_seconds")
    if isinstance(value, (int, float)) and value >= 0:
        return float(value)
    return None


def _summary(result: Any) -> str:
    if isinstance(result, AgentResult):
        return result.summary
    return str(result or "")


def _error_text(result: Any) -> str:
    if isinstance(result, AgentResult):
        return result.error_message or result.summary
    return str(result or "")


def _observation(result: Any) -> Any:
    if isinstance(result, AgentResult):
        return result.to_message_content()
    return str(result or "")


def _fingerprint(tool_name: str, error_code: str, message: str) -> str:
    normalized = _DYNAMIC_VALUE.sub("<dynamic>", message.lower())[:256]
    raw = f"{tool_name}:{error_code}:{normalized}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
