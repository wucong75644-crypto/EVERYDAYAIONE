"""Chat 执行内核的纯结果协议。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from schemas.message import ContentPart


@dataclass(frozen=True)
class ChatExecutionResult:
    parts: list[ContentPart]
    content_blocks: list[dict[str, Any]]
    usage: dict[str, Any]
    credits_cost: int
    tool_digest: dict[str, Any] | None
    data_evidence: list[dict[str, Any]] = field(default_factory=list)
    artifact_drafts: tuple[Any, ...] = ()
    context_receipts: list[dict[str, Any]] = field(default_factory=list)
    compaction: dict[str, Any] | None = None
