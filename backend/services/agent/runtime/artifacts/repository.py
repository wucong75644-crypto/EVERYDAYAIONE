"""固定会话 revision 内的持久 Artifact 读取。"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from .store import page_content
from .types import ArtifactPage


_SCAN_LIMIT = 200


class PersistentArtifactRepository:
    """以 conversation/base revision/org 限定持久 Artifact。"""

    def __init__(
        self,
        db: Any,
        *,
        conversation_id: str,
        base_revision: int,
        org_id: str | None,
    ) -> None:
        self._db = db
        self._conversation_id = conversation_id
        self._base_revision = base_revision
        self._org_id = org_id

    def search(
        self, query: str = "", *, limit: int = 5
    ) -> tuple[dict[str, Any], ...]:
        rows = self._query().order(
            "context_revision", desc=True
        ).range(0, _SCAN_LIMIT - 1).execute()
        values = rows.data if rows and isinstance(rows.data, list) else []
        query = query.strip().casefold()[:200]
        matches: list[dict[str, Any]] = []
        for row in values:
            item = _directory_item(row)
            if query and query not in _dumps(item).casefold():
                continue
            matches.append(item)
            if len(matches) >= max(1, min(limit, 20)):
                break
        return tuple(matches)

    def get(self, artifact_id: str) -> dict[str, Any] | None:
        result = (
            self._query()
            .eq("id", artifact_id)
            .maybe_single()
            .execute()
        )
        row = result.data if result else None
        return row if isinstance(row, dict) else None

    async def read(
        self,
        artifact_id: str,
        *,
        cursor: int,
        max_tokens: int,
    ) -> ArtifactPage | None:
        row = self.get(artifact_id)
        if row is None:
            return None
        content = await self._load_content(row)
        return page_content(
            artifact_id,
            content,
            cursor=cursor,
            max_tokens=max_tokens,
        )

    def _query(self) -> Any:
        query = (
            self._db.table("conversation_artifacts")
            .select(
                "id,tool_call_id,tool_name,artifact_type,status,"
                "storage_kind,inline_content,storage_ref,model_view,"
                "history_view,content_hash,byte_size,metadata,"
                "context_revision"
            )
            .eq("conversation_id", self._conversation_id)
            .lte("context_revision", self._base_revision)
            .eq("status", "ready")
        )
        if self._org_id:
            query = query.eq("org_id", self._org_id)
        return query

    async def _load_content(self, row: dict[str, Any]) -> Any:
        storage_kind = row.get("storage_kind")
        if storage_kind == "inline":
            return row.get("inline_content")
        reference = row.get("storage_ref")
        reference = reference if isinstance(reference, dict) else {}
        if storage_kind == "message_slice":
            return self._read_message_slice(reference)
        if storage_kind != "oss" or not reference.get("object_key"):
            raise RuntimeError("ARTIFACT_STORAGE_REFERENCE_INVALID")
        from services.oss_service import get_oss_service

        oss = get_oss_service()
        result = await asyncio.to_thread(
            oss.bucket.get_object,
            str(reference["object_key"]),
        )
        raw = await asyncio.to_thread(result.read)
        return json.loads(raw.decode("utf-8"))

    def _read_message_slice(self, reference: dict[str, Any]) -> Any:
        message_id = str(reference.get("message_id") or "")
        block_index = reference.get("block_index")
        if not message_id or not isinstance(block_index, int):
            raise RuntimeError("ARTIFACT_MESSAGE_SLICE_INVALID")
        result = (
            self._db.table("messages")
            .select("conversation_id,content")
            .eq("id", message_id)
            .eq("conversation_id", self._conversation_id)
            .maybe_single()
            .execute()
        )
        row = result.data if result else None
        if not isinstance(row, dict):
            raise RuntimeError("ARTIFACT_MESSAGE_SLICE_NOT_FOUND")
        content = row.get("content")
        if isinstance(content, str):
            content = json.loads(content)
        if (
            not isinstance(content, list)
            or block_index < 0
            or block_index >= len(content)
        ):
            raise RuntimeError("ARTIFACT_MESSAGE_SLICE_INVALID")
        block = content[block_index]
        if not isinstance(block, dict):
            return block
        return block.get("output", block.get("result", block.get("text", block)))


def _directory_item(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "artifact_id": str(row.get("id") or ""),
        "tool_call_id": row.get("tool_call_id"),
        "tool_name": str(row.get("tool_name") or ""),
        "artifact_type": str(row.get("artifact_type") or ""),
        "status": str(row.get("status") or ""),
        "byte_size": row.get("byte_size"),
        "content_hash": row.get("content_hash"),
        "model_view": row.get("history_view") or row.get("model_view") or {},
        "metadata": row.get("metadata") or {},
        "context_revision": row.get("context_revision"),
    }


def _dumps(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        default=str,
    )
