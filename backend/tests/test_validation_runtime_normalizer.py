"""Validation Runtime 结果归一化测试。"""

import asyncio

from services.agent.agent_result import AgentResult
from services.agent.runtime.validation.effects import resolve_tool_effect
from services.agent.runtime.validation.normalizer import normalize_tool_result
from services.agent.runtime.validation.types import ResultClass, ToolEffect


def _normalize(result, **kwargs):
    return normalize_tool_result(
        result,
        tool_call_id="call-1",
        tool_name="code_execute",
        **kwargs,
    )


def test_structured_success_is_success() -> None:
    result = _normalize(AgentResult(summary="ok", status="success"))

    assert result.result_class == ResultClass.SUCCESS
    assert result.terminal is True


def test_structured_retryable_error_uses_metadata() -> None:
    result = _normalize(
        AgentResult(
            summary="失败",
            status="error",
            error_message="字段不存在",
            metadata={"retryable": True, "error_code": "BINDER_ERROR"},
        )
    )

    assert result.result_class == ResultClass.RETRYABLE
    assert result.error_code == "BINDER_ERROR"
    assert result.retryable is True


def test_structured_non_retryable_error_is_fatal() -> None:
    result = _normalize(
        AgentResult(
            summary="无权限",
            status="error",
            metadata={"retryable": False},
        )
    )

    assert result.result_class == ResultClass.FATAL


def test_explicit_result_class_wins_without_text_guessing() -> None:
    result = _normalize(
        AgentResult(
            summary="需要用户选择",
            status="error",
            metadata={"result_class": "ambiguous"},
        )
    )

    assert result.result_class == ResultClass.AMBIGUOUS


def test_timeout_and_cancellation_have_distinct_classes() -> None:
    assert _normalize(TimeoutError("slow")).result_class == ResultClass.RETRYABLE
    assert _normalize(
        asyncio.CancelledError()
    ).result_class == ResultClass.CANCELLED


def test_non_idempotent_exception_is_unknown_not_retryable() -> None:
    result = _normalize(
        RuntimeError("connection lost"),
        effect=ToolEffect.NON_IDEMPOTENT_WRITE,
    )

    assert result.result_class == ResultClass.UNKNOWN
    assert result.retryable is False


def test_permission_error_is_fatal() -> None:
    result = _normalize(PermissionError("denied"))

    assert result.result_class == ResultClass.FATAL
    assert result.error_code == "PERMISSION_DENIED"


def test_legacy_error_defaults_to_retryable_once() -> None:
    result = _normalize("执行失败", audit_status="error")

    assert result.result_class == ResultClass.RETRYABLE
    assert result.error_code == "LEGACY_TOOL_ERROR"


def test_legacy_success_and_cancelled_follow_audit_status() -> None:
    assert _normalize("ok", audit_status="success").result_class == ResultClass.SUCCESS
    assert _normalize("ok").result_class == ResultClass.SUCCESS
    cancelled = _normalize("stopped", audit_status="cancelled")
    assert cancelled.result_class == ResultClass.CANCELLED


def test_audit_timeout_uses_agent_result_summary_when_error_is_empty() -> None:
    result = _normalize(
        AgentResult(summary="provider slow", status="success"),
        audit_status="timeout",
    )

    assert result.error_message == "provider slow"


def test_agent_result_empty_partial_timeout_and_cancelled() -> None:
    expected = {
        "empty": ResultClass.PARTIAL,
        "partial": ResultClass.PARTIAL,
        "timeout": ResultClass.RETRYABLE,
        "cancelled": ResultClass.CANCELLED,
    }

    for status, result_class in expected.items():
        result = _normalize(AgentResult(summary=status, status=status))
        assert result.result_class == result_class


def test_invalid_status_and_explicit_class_are_fatal() -> None:
    invalid_status = _normalize(AgentResult(summary="bad", status="unexpected"))
    invalid_class = _normalize(
        AgentResult(
            summary="bad",
            status="error",
            metadata={"result_class": "not-a-class"},
        )
    )

    assert invalid_status.error_code == "INVALID_RESULT_STATUS"
    assert invalid_status.result_class == ResultClass.FATAL
    assert invalid_class.result_class == ResultClass.FATAL


def test_retry_after_accepts_non_negative_numbers_only() -> None:
    accepted = _normalize(
        AgentResult(
            summary="wait",
            status="timeout",
            metadata={"retry_after_seconds": 1.5},
        )
    )
    rejected = _normalize(
        AgentResult(
            summary="wait",
            status="timeout",
            metadata={"retry_after_seconds": -1},
        )
    )

    assert accepted.retry_after_seconds == 1.5
    assert rejected.retry_after_seconds is None


def test_dynamic_values_do_not_change_error_fingerprint() -> None:
    first = _normalize(RuntimeError("request 12345 failed"))
    second = _normalize(RuntimeError("request 67890 failed"))

    assert first.fingerprint == second.fingerprint


def test_existing_safety_metadata_maps_dangerous_tools_to_write_effect(
    monkeypatch,
) -> None:
    from config.chat_tools import SafetyLevel

    monkeypatch.setattr(
        "config.chat_tools.get_safety_level",
        lambda _name: SafetyLevel.DANGEROUS,
    )

    assert resolve_tool_effect(
        "write_tool",
    ) == ToolEffect.NON_IDEMPOTENT_WRITE


def test_missing_safety_metadata_defaults_to_read_only(monkeypatch) -> None:
    monkeypatch.setattr(
        "config.chat_tools.get_safety_level",
        lambda _name: (_ for _ in ()).throw(KeyError("missing")),
    )

    assert resolve_tool_effect("unknown_tool") == ToolEffect.READ_ONLY
