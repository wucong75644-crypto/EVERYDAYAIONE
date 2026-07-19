"""上下文治理的安全结构化观测事件。"""

from __future__ import annotations

from typing import Any

from loguru import logger


_ALLOWED_EVENTS = {
    "context_cache",
    "context_compaction",
    "context_evidence_get",
    "context_evidence_search",
    "context_receipt",
}
_ALLOWED_FIELDS = {
    "base_revision",
    "context_estimated_tokens",
    "context_tokens_by_kind",
    "context_tool_schema_tokens",
    "conversation_id",
    "message_count",
    "model_id",
    "mode",
    "org_id",
    "outcome",
    "result_count",
    "reason",
    "selector",
    "summary_revision",
    "task_id",
    "tokens_after",
    "tokens_before",
    "tool_count",
    "trimmed_tokens",
    "truncated",
    "turn",
}


def record_context_event(event: str, **fields: Any) -> None:
    """Best-effort 记录无正文上下文事件，观测失败不影响聊天。"""
    if event not in _ALLOWED_EVENTS:
        return
    try:
        safe_fields = {
            key: value
            for key, value in fields.items()
            if key in _ALLOWED_FIELDS
            if value is None or isinstance(value, (str, int, float, bool, dict))
        }
        logger.bind(
            metric=f"gen_ai.{event}",
            **safe_fields,
        ).info(event)
    except Exception:
        return
