"""单次 Run 内 Artifact 完整事实存储与有界读取。"""

from __future__ import annotations

import json
from collections import OrderedDict
from typing import Any

from .normalizer import canonical_json
from .types import ArtifactDraft, ArtifactPage


_CHARS_PER_TOKEN = 2.5
_MAX_READ_TOKENS = 16_000
_MAX_SEARCH_RESULTS = 20


class ArtifactStore:
    """Run-local Artifact registry；最终由 Actor 原子提交持久化。"""

    def __init__(self) -> None:
        self._drafts: OrderedDict[str, ArtifactDraft] = OrderedDict()

    def add(self, draft: ArtifactDraft) -> bool:
        """登记 Artifact；相同稳定 ID 幂等。"""
        if draft.artifact_id in self._drafts:
            return False
        self._drafts[draft.artifact_id] = draft
        return True

    def get(self, artifact_id: str) -> ArtifactDraft | None:
        return self._drafts.get(artifact_id)

    def snapshot(self) -> tuple[ArtifactDraft, ...]:
        return tuple(self._drafts.values())

    def search(
        self, query: str = "", *, limit: int = 5
    ) -> tuple[dict[str, Any], ...]:
        """按 ID、工具名、类型及模型视图搜索当前 Run 目录。"""
        query = query.strip().casefold()[:200]
        limit = max(1, min(limit, _MAX_SEARCH_RESULTS))
        matches: list[dict[str, Any]] = []
        for draft in reversed(self._drafts.values()):
            item = draft.directory_item()
            item["model_view"] = draft.history_view
            blob = canonical_json(item).casefold()
            if query and query not in blob:
                continue
            matches.append(item)
            if len(matches) >= limit:
                break
        return tuple(matches)

    def read(
        self,
        artifact_id: str,
        *,
        cursor: int = 0,
        max_tokens: int = 4_000,
    ) -> ArtifactPage | None:
        """按 UTF-8 字节游标读取完整规范内容。"""
        draft = self.get(artifact_id)
        if draft is None:
            return None
        return page_content(
            artifact_id,
            draft.content,
            cursor=cursor,
            max_tokens=max_tokens,
        )


def page_content(
    artifact_id: str,
    content: Any,
    *,
    cursor: int,
    max_tokens: int,
) -> ArtifactPage:
    """对内存或持久内容应用同一 UTF-8 分页协议。"""
    encoded = canonical_json(content).encode("utf-8")
    cursor = _align_utf8_start(
        encoded, max(0, min(int(cursor), len(encoded)))
    )
    max_tokens = max(256, min(int(max_tokens), _MAX_READ_TOKENS))
    max_bytes = int(max_tokens * _CHARS_PER_TOKEN)
    end = min(len(encoded), cursor + max_bytes)
    chunk = encoded[cursor:end]
    while end < len(encoded):
        try:
            text = chunk.decode("utf-8")
            break
        except UnicodeDecodeError:
            end -= 1
            chunk = encoded[cursor:end]
    else:
        text = chunk.decode("utf-8", errors="ignore")
    return ArtifactPage(
        artifact_id=artifact_id,
        content=text,
        cursor=cursor,
        next_cursor=end if end < len(encoded) else None,
        byte_size=len(encoded),
        returned_bytes=len(chunk),
        complete=end >= len(encoded),
    )


def _align_utf8_start(encoded: bytes, cursor: int) -> int:
    """把任意用户游标前移到下一个 UTF-8 字符边界。"""
    while cursor < len(encoded) and encoded[cursor] & 0b11000000 == 0b10000000:
        cursor += 1
    return cursor
