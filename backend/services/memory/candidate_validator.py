"""通用记忆候选的确定性证据门禁。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .contracts import (
    ALLOWED_EXPLICITNESS,
    ALLOWED_MEMORY_KINDS,
    ALLOWED_MEMORY_SCOPES,
    MemoryCandidate,
    MemoryValidationIssue,
    MemoryValidationResult,
)


_QUESTION_PREFIXES = (
    "是否", "能否", "可不可以", "会不会", "为什么", "怎么", "如何",
    "what ", "why ", "how ", "could ", "would ", "can ",
)
_HYPOTHETICAL_MARKERS = (
    "如果", "假如", "假设", "要是", "可能", "也许", "或许",
    "if ", "suppose ", "assuming ", "maybe ", "perhaps ",
)
_EXAMPLE_MARKERS = (
    "例如", "比如", "举个例子", "假设有", "example:", "for example",
)
_TRANSIENT_MARKERS = (
    "这次", "本次", "当前这次", "这一单", "本单", "这张表", "这个文件",
    "刚才", "暂时", "临时", "先帮我", "for this time", "this time",
)


def validate_memory_candidate(
    candidate: MemoryCandidate,
    messages: Sequence[Mapping[str, Any]],
) -> MemoryValidationResult:
    """验证候选的类型、时效和用户原文证据，失败时不允许进入写入阶段。"""
    issues: list[MemoryValidationIssue] = []
    message_index = _index_messages(messages, issues)

    _validate_enums(candidate, issues)
    _validate_time_range(candidate, issues)
    _validate_claim(candidate, issues)
    _validate_evidence(candidate, message_index, issues)

    return MemoryValidationResult(accepted=not issues, issues=tuple(issues))


def _index_messages(
    messages: Sequence[Mapping[str, Any]],
    issues: list[MemoryValidationIssue],
) -> dict[str, Mapping[str, Any]]:
    index: dict[str, Mapping[str, Any]] = {}
    for message in messages:
        message_id = message.get("id")
        if not isinstance(message_id, str) or not message_id:
            continue
        if message_id in index:
            issues.append(_issue("duplicate_message_id", f"duplicate message id: {message_id}"))
            continue
        index[message_id] = message
    return index


def _validate_enums(
    candidate: MemoryCandidate,
    issues: list[MemoryValidationIssue],
) -> None:
    if candidate.kind not in ALLOWED_MEMORY_KINDS:
        issues.append(_issue("unsupported_kind", f"unsupported kind: {candidate.kind}"))
    if candidate.scope not in ALLOWED_MEMORY_SCOPES:
        issues.append(_issue("unsupported_scope", f"unsupported scope: {candidate.scope}"))
    if candidate.explicitness not in ALLOWED_EXPLICITNESS:
        issues.append(_issue(
            "unsupported_explicitness",
            f"unsupported explicitness: {candidate.explicitness}",
        ))
    if candidate.scope == "long_term" and candidate.explicitness != "explicit":
        issues.append(_issue(
            "long_term_requires_explicit_evidence",
            "long-term memory must be explicitly stated",
        ))


def _validate_time_range(
    candidate: MemoryCandidate,
    issues: list[MemoryValidationIssue],
) -> None:
    if (
        candidate.valid_from is not None
        and candidate.valid_until is not None
        and candidate.valid_until < candidate.valid_from
    ):
        issues.append(_issue(
            "invalid_validity_range",
            "valid_until must not be earlier than valid_from",
        ))


def _validate_claim(
    candidate: MemoryCandidate,
    issues: list[MemoryValidationIssue],
) -> None:
    text = candidate.claim.strip()
    if len(text) > 1_000:
        issues.append(_issue("claim_too_long", "claim exceeds 1000 characters"))
    if _looks_like_question(text):
        issues.append(_issue("question_not_memory", "questions cannot become memory claims"))


def _validate_evidence(
    candidate: MemoryCandidate,
    message_index: Mapping[str, Mapping[str, Any]],
    issues: list[MemoryValidationIssue],
) -> None:
    has_user_evidence = False
    for evidence in candidate.evidence:
        message = message_index.get(evidence.message_id)
        if message is None:
            issues.append(_issue(
                "evidence_message_missing",
                f"evidence message not found: {evidence.message_id}",
            ))
            continue

        content = _message_text(message.get("content"))
        if evidence.quote not in content:
            issues.append(_issue(
                "evidence_quote_mismatch",
                f"quote is not present in message: {evidence.message_id}",
            ))
            continue

        if message.get("role") == "user":
            has_user_evidence = True
            _validate_user_quote(evidence.quote, candidate.scope, issues)

    if not has_user_evidence:
        issues.append(_issue(
            "user_evidence_required",
            "at least one exact quote from a user message is required",
        ))


def _validate_user_quote(
    quote: str,
    scope: str,
    issues: list[MemoryValidationIssue],
) -> None:
    normalized = quote.strip().lower()
    if _looks_like_question(normalized):
        issues.append(_issue("question_evidence", "question text is not factual evidence"))
    if _contains_marker(normalized, _HYPOTHETICAL_MARKERS):
        issues.append(_issue("hypothetical_evidence", "hypothetical text is not factual evidence"))
    if _contains_marker(normalized, _EXAMPLE_MARKERS):
        issues.append(_issue("example_evidence", "example text is not factual evidence"))
    if scope == "long_term" and _contains_marker(normalized, _TRANSIENT_MARKERS):
        issues.append(_issue(
            "transient_long_term_evidence",
            "transient instructions cannot support long-term memory",
        ))


def _looks_like_question(text: str) -> bool:
    normalized = text.strip().lower()
    if normalized.endswith(("?", "？")):
        return True
    return any(normalized.startswith(prefix) for prefix in _QUESTION_PREFIXES)


def _contains_marker(text: str, markers: Sequence[str]) -> bool:
    return any(marker in text for marker in markers)


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            str(part.get("text", ""))
            for part in content
            if isinstance(part, Mapping) and part.get("type") == "text"
        )
    return ""


def _issue(code: str, message: str) -> MemoryValidationIssue:
    return MemoryValidationIssue(code=code, message=message)
