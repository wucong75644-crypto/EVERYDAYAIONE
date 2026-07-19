"""Artifact 运行时的稳定内部类型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ArtifactDraft:
    """单次 Run 内已规范化但尚未提交数据库的完整工具事实。"""

    artifact_id: str
    tool_call_id: str
    tool_name: str
    artifact_type: str
    status: str
    content: Any
    content_hash: str
    byte_size: int
    model_view: dict[str, Any]
    history_view: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)
    sensitivity: str = "internal"

    def directory_item(self) -> dict[str, Any]:
        """返回不含完整正文的检索目录项。"""
        return {
            "artifact_id": self.artifact_id,
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "artifact_type": self.artifact_type,
            "status": self.status,
            "byte_size": self.byte_size,
            "content_hash": self.content_hash,
            "model_view": self.model_view,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class ArtifactPage:
    """一次有界 Artifact 读取结果。"""

    artifact_id: str
    content: Any
    cursor: int
    next_cursor: int | None
    byte_size: int
    returned_bytes: int
    complete: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "content": self.content,
            "cursor": self.cursor,
            "next_cursor": self.next_cursor,
            "byte_size": self.byte_size,
            "returned_bytes": self.returned_bytes,
            "complete": self.complete,
        }
