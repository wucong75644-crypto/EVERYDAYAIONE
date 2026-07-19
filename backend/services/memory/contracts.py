"""通用记忆候选与校验结果协议。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping


ALLOWED_MEMORY_KINDS = frozenset({
    "user_profile",
    "preference",
    "instruction",
    "decision",
    "reusable_context",
    "problem_solution",
    "tracked_plan",
    "skill_defined",
})
ALLOWED_MEMORY_SCOPES = frozenset({"session", "long_term"})
ALLOWED_EXPLICITNESS = frozenset({"explicit", "inferred"})


@dataclass(frozen=True)
class MemoryEvidence:
    """候选记忆引用的一段原始消息证据。"""

    message_id: str
    quote: str


@dataclass(frozen=True)
class MemoryCandidate:
    """模型提出、尚未获得写入资格的通用记忆候选。"""

    claim: str
    kind: str
    scope: str
    explicitness: str
    evidence: tuple[MemoryEvidence, ...]
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    attributes: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MemoryValidationIssue:
    """单个候选未通过确定性门禁的原因。"""

    code: str
    message: str


@dataclass(frozen=True)
class MemoryValidationResult:
    """候选是否可以进入后续去重/晋升阶段。"""

    accepted: bool
    issues: tuple[MemoryValidationIssue, ...] = ()


def parse_memory_candidate(raw: Mapping[str, Any]) -> MemoryCandidate:
    """严格解析模型候选；格式不完整时抛出 ValueError。"""
    if not isinstance(raw, Mapping):
        raise ValueError("candidate must be an object")

    claim = _required_text(raw, "claim")
    kind = _required_text(raw, "kind")
    scope = _required_text(raw, "scope")
    explicitness = _required_text(raw, "explicitness")

    raw_evidence = raw.get("evidence")
    if not isinstance(raw_evidence, list) or not raw_evidence:
        raise ValueError("evidence must be a non-empty list")
    evidence = tuple(_parse_evidence(item) for item in raw_evidence)

    attributes = raw.get("attributes", {})
    if not isinstance(attributes, Mapping):
        raise ValueError("attributes must be an object")

    return MemoryCandidate(
        claim=claim,
        kind=kind,
        scope=scope,
        explicitness=explicitness,
        evidence=evidence,
        valid_from=_parse_optional_datetime(raw.get("valid_from"), "valid_from"),
        valid_until=_parse_optional_datetime(raw.get("valid_until"), "valid_until"),
        attributes=dict(attributes),
    )


def _parse_evidence(raw: Any) -> MemoryEvidence:
    if not isinstance(raw, Mapping):
        raise ValueError("evidence item must be an object")
    return MemoryEvidence(
        message_id=_required_text(raw, "message_id"),
        quote=_required_text(raw, "quote"),
    )


def _required_text(raw: Mapping[str, Any], field_name: str) -> str:
    value = raw.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be non-empty text")
    return value.strip()


def _parse_optional_datetime(value: Any, field_name: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be ISO 8601 text or null")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be valid ISO 8601") from exc
