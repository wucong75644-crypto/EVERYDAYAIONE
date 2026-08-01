"""Real DB, Artifact and Conversation/Memory read capabilities."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping
from uuid import UUID

from services.agent.runtime.artifacts.store import page_content
from services.agent.runtime.executors.contracts import ActionSnapshot
from services.agent.runtime.executors.real_base import (
    RealReadCapability, RuntimeReadResources, bounded_limit, execute_query,
    optional_text, required_text,
)


class ConversationReadCapability(RealReadCapability):
    async def _read_bound(self, snapshot: ActionSnapshot, request: Mapping[str, object]):
        conversation_id = self.resources.conversation_id
        if not conversation_id:
            raise PermissionError("READ_CONVERSATION_CONTEXT_REQUIRED")
        limit = bounded_limit(request.get("limit"), default=10, maximum=20)
        query = (
            self.resources.database.table("messages")
            .select("id,role,content,created_at")
            .eq("conversation_id", conversation_id)
            .order("created_at", desc=True)
            .limit(limit)
        )
        result = await execute_query(query)
        rows = getattr(result, "data", None)
        rows = rows if isinstance(rows, list) else []
        messages = [_message_view(row) for row in reversed(rows)]
        return {
            "summary": "当前对话暂无历史消息" if not messages else "当前对话历史消息",
            "count": len(messages), "messages": messages,
        }


class KnowledgeReadCapability(RealReadCapability):
    async def _read_bound(self, snapshot: ActionSnapshot, request: Mapping[str, object]):
        query_text = required_text(request, "query", max_len=200).casefold()
        limit = bounded_limit(request.get("limit"), default=5, maximum=10)
        query = (
            self.resources.database.table("knowledge_nodes")
            .select("id,category,node_type,title,content,confidence,source,metadata")
            .eq("is_deleted", False)
            .limit(100)
        )
        result = await execute_query(query)
        rows = getattr(result, "data", None)
        rows = rows if isinstance(rows, list) else []
        matches = [
            _knowledge_view(row) for row in rows
            if query_text in f"{row.get('title', '')} {row.get('content', '')}".casefold()
        ][:limit]
        return {
            "summary": "未找到相关知识" if not matches else "知识库检索结果",
            "count": len(matches), "items": matches,
        }


class MemoryReadCapability(RealReadCapability):
    async def _read_bound(self, snapshot: ActionSnapshot, request: Mapping[str, object]):
        operation = snapshot.request.get("memory_operation")
        if operation == "get" or "memory_ref" in request:
            return await self._get(request)
        query_text = required_text(request, "query", max_len=200).casefold()
        limit = bounded_limit(request.get("limit"), default=3, maximum=6)
        query = (
            self.resources.database.table("memory_atoms")
            .select("id,content,metadata,valid_from,valid_until,source_message_ids")
            .eq("user_id", self.resources.user_id)
            .eq("is_deleted", False).eq("status", "active").limit(100)
        )
        if self.resources.org_id is None:
            query = query.is_("org_id", "null")
        else:
            query = query.eq("org_id", self.resources.org_id)
        result = await execute_query(query)
        rows = getattr(result, "data", None)
        rows = rows if isinstance(rows, list) else []
        matches = [
            _memory_view(row) for row in rows
            if query_text in str(row.get("content") or "").casefold()
        ][:limit]
        return {
            "summary": "未找到当前用户可访问的记忆" if not matches else "记忆检索结果",
            "count": len(matches), "memories": matches,
        }

    async def _get(self, request: Mapping[str, object]) -> dict[str, object]:
        ref = required_text(request, "memory_ref", max_len=160)
        if not ref.startswith("memory:") or len(ref) <= 7:
            raise ValueError("READ_MEMORY_REF_INVALID")
        try:
            memory_id = str(UUID(ref.removeprefix("memory:")))
        except ValueError as exc:
            raise ValueError("READ_MEMORY_REF_INVALID") from exc
        query = (
            self.resources.database.table("memory_atoms")
            .select("id,content,metadata,valid_from,valid_until,source_message_ids")
            .eq("id", memory_id)
            .eq("user_id", self.resources.user_id)
            .eq("is_deleted", False).eq("status", "active").limit(1)
        )
        if self.resources.org_id is None:
            query = query.is_("org_id", "null")
        else:
            query = query.eq("org_id", self.resources.org_id)
        result = await execute_query(query)
        rows = getattr(result, "data", None)
        row = rows[0] if isinstance(rows, list) and rows else None
        if not isinstance(row, Mapping):
            return {"summary": "未找到当前用户可访问的记忆", "count": 0}
        return {"summary": "记忆详情", **_memory_view(row)}


class EvidenceReadCapability(RealReadCapability):
    async def _read_bound(self, snapshot: ActionSnapshot, request: Mapping[str, object]):
        if not self.resources.conversation_id or self.resources.base_revision is None:
            raise PermissionError("READ_EVIDENCE_CONTEXT_REQUIRED")
        if "artifact_id" in request:
            return await self._get(request)
        query_text = (optional_text(request, "query", max_len=200) or "").casefold()
        limit = bounded_limit(request.get("limit"), default=5, maximum=10)
        query = (
            self.resources.database.table("conversation_data_evidence")
            .select("artifact_id,source,columns,query_scope,model_view,byte_size,context_revision")
            .eq("conversation_id", self.resources.conversation_id)
            .lte("context_revision", self.resources.base_revision)
            .eq("validation_status", "ready").order("context_revision", desc=True)
            .limit(200)
        )
        result = await execute_query(query)
        rows = getattr(result, "data", None)
        rows = rows if isinstance(rows, list) else []
        items = [_evidence_view(row) for row in rows]
        items = [item for item in items if not query_text or query_text in _json(item).casefold()][:limit]
        return {"summary": "未找到可访问的 Evidence" if not items else "Evidence 检索结果", "count": len(items), "evidence": items}

    async def _get(self, request: Mapping[str, object]) -> dict[str, object]:
        artifact_id = required_text(request, "artifact_id", max_len=160)
        query = (
            self.resources.database.table("conversation_data_evidence")
            .select("artifact_id,source,columns,rows,query_scope,metric_definitions,model_view,byte_size,context_revision")
            .eq("conversation_id", self.resources.conversation_id)
            .eq("artifact_id", artifact_id).lte("context_revision", self.resources.base_revision)
            .eq("validation_status", "ready").limit(1)
        )
        result = await execute_query(query)
        rows = getattr(result, "data", None)
        row = rows[0] if isinstance(rows, list) and rows else None
        if not isinstance(row, Mapping):
            return {"summary": "未找到可访问的 Evidence", "count": 0}
        payload = _evidence_view(row)
        payload["model_view"] = row.get("model_view") or {}
        if request.get("selector") == "rows":
            payload["rows"] = row.get("rows") or []
        payload = _bound_mapping(payload, request.get("max_tokens"), 4000)
        return {"summary": "Evidence 详情", **payload}


class ArtifactReadCapability(RealReadCapability):
    async def _read_bound(self, snapshot: ActionSnapshot, request: Mapping[str, object]):
        if not self.resources.conversation_id or self.resources.base_revision is None:
            raise PermissionError("READ_ARTIFACT_CONTEXT_REQUIRED")
        operation = snapshot.request.get("artifact_operation")
        if operation == "search" or "query" in request and "artifact_id" not in request:
            return await self._search(request)
        if operation == "get" or "artifact_id" in request and "cursor" not in request:
            return await self._get(request)
        return await self._read(request)

    async def _search(self, request: Mapping[str, object]) -> dict[str, object]:
        query_text = (optional_text(request, "query", max_len=200) or "").casefold()
        limit = bounded_limit(request.get("limit"), default=5, maximum=20)
        rows = await self._rows()
        items = [_artifact_view(row) for row in rows]
        items = [item for item in items if not query_text or query_text in _json(item).casefold()][:limit]
        return {"summary": "未找到当前执行可访问的 Artifact" if not items else "Artifact 检索结果", "count": len(items), "artifacts": items}

    async def _get(self, request: Mapping[str, object]) -> dict[str, object]:
        artifact_id = required_text(request, "artifact_id", max_len=160)
        row = next((item for item in await self._rows() if str(item.get("id")) == artifact_id), None)
        if row is None:
            return {"summary": "未找到当前执行可访问的 Artifact", "count": 0}
        return {"summary": "Artifact 详情", **_artifact_view(row)}

    async def _read(self, request: Mapping[str, object]) -> dict[str, object]:
        artifact_id = required_text(request, "artifact_id", max_len=160)
        rows = await self._rows()
        row = next((item for item in rows if str(item.get("id")) == artifact_id), None)
        if row is None:
            return {"summary": "未找到当前执行可访问的 Artifact", "count": 0}
        storage_kind = row.get("storage_kind")
        if storage_kind == "oss":
            raise PermissionError("READ_ARTIFACT_EXTERNAL_STORAGE_FORBIDDEN")
        content = row.get("inline_content")
        if storage_kind == "message_slice":
            content = await self._message_slice(row.get("storage_ref"))
        page = page_content(
            artifact_id, content,
            cursor=_bounded_int(request.get("cursor"), 0, 16_000),
            max_tokens=_bounded_int(request.get("max_tokens"), 256, 16_000),
        )
        return {"summary": "Artifact 分页内容", **page.to_dict()}

    async def _rows(self) -> list[dict[str, Any]]:
        query = (
            self.resources.database.table("conversation_artifacts")
            .select("id,tool_call_id,tool_name,artifact_type,status,storage_kind,inline_content,storage_ref,model_view,history_view,content_hash,byte_size,metadata,context_revision")
            .eq("conversation_id", self.resources.conversation_id)
            .lte("context_revision", self.resources.base_revision).eq("status", "ready")
            .limit(200)
        )
        if self.resources.org_id:
            query = query.eq("org_id", self.resources.org_id)
        result = await execute_query(query)
        rows = getattr(result, "data", None)
        return rows if isinstance(rows, list) else []

    async def _message_slice(self, reference: object) -> object:
        if not isinstance(reference, Mapping):
            raise ValueError("READ_ARTIFACT_MESSAGE_REF_INVALID")
        message_id = required_text(reference, "message_id", max_len=160)
        block_index = reference.get("block_index")
        if isinstance(block_index, bool) or not isinstance(block_index, int) or block_index < 0:
            raise ValueError("READ_ARTIFACT_BLOCK_INDEX_INVALID")
        query = self.resources.database.table("messages").select("content").eq("id", message_id).eq("conversation_id", self.resources.conversation_id).limit(1)
        result = await execute_query(query)
        rows = getattr(result, "data", None)
        row = rows[0] if isinstance(rows, list) and rows else None
        content = row.get("content") if isinstance(row, Mapping) else None
        if isinstance(content, str):
            content = json.loads(content)
        if not isinstance(content, list) or block_index >= len(content):
            raise ValueError("READ_ARTIFACT_MESSAGE_BLOCK_INVALID")
        block = content[block_index]
        return block.get("output", block.get("result", block.get("text", block))) if isinstance(block, Mapping) else block


class WorkspaceReadCapability(RealReadCapability):
    async def _read_bound(self, snapshot: ActionSnapshot, request: Mapping[str, object]):
        root = self._root(snapshot)
        path = optional_text(request, "path", max_len=500)
        keyword = optional_text(request, "keyword", max_len=100)
        pattern = optional_text(request, "file_pattern", max_len=100)
        if path and not keyword and not pattern:
            target = self._safe(root, path)
            items = self._entries(target, root)
        elif keyword or pattern:
            items = self._search(root, keyword or "", pattern)
        else:
            items = self._entries(root, root)
        items = items[:100]
        return {"summary": "未找到匹配文件" if not items else "Workspace 文件检索结果", "count": len(items), "files": items}

    def _root(self, snapshot: ActionSnapshot) -> Path:
        if self.resources.workspace_root is None:
            raise PermissionError("READ_WORKSPACE_ROOT_REQUIRED")
        if snapshot.scope.org_id:
            return self.resources.workspace_root / "org" / snapshot.scope.org_id / self.resources.user_id
        import hashlib
        return self.resources.workspace_root / "personal" / hashlib.md5(self.resources.user_id.encode()).hexdigest()[:8]

    def _safe(self, root: Path, value: str) -> Path:
        if value.startswith("/") or "\\" in value:
            raise PermissionError("READ_WORKSPACE_PATH_INVALID")
        raw_target = root / value.lstrip("/")
        if raw_target.is_symlink():
            raise PermissionError("READ_WORKSPACE_SYMLINK_FORBIDDEN")
        target = raw_target.resolve()
        try:
            target.relative_to(root.resolve())
        except ValueError as exc:
            raise PermissionError("READ_WORKSPACE_PATH_ESCAPE") from exc
        if any(part in {".env", ".git", ".ssh", "staging"} for part in target.relative_to(root).parts):
            raise PermissionError("READ_WORKSPACE_BLOCKED_PATH")
        return target

    def _entries(self, target: Path, root: Path) -> list[dict[str, object]]:
        if not target.exists():
            return []
        if target.is_file():
            return [self._file(target, root)]
        return [self._file(item, root) for item in sorted(target.iterdir()) if not item.name.startswith(".") and item.name != "staging"]

    def _search(self, root: Path, keyword: str, pattern: str | None) -> list[dict[str, object]]:
        if not root.exists():
            return []
        found = []
        for item in root.rglob(pattern or "*"):
            if item.is_symlink() or item.name.startswith(".") or "staging" in item.relative_to(root).parts:
                continue
            if keyword.casefold() in item.name.casefold():
                found.append(self._file(item, root))
            if len(found) >= 100:
                break
        return found

    @staticmethod
    def _file(item: Path, root: Path) -> dict[str, object]:
        stat = item.stat()
        return {"name": item.name, "relative_path": str(item.relative_to(root)), "kind": "directory" if item.is_dir() else "file", "byte_size": stat.st_size}


def _message_view(row: Mapping[str, object]) -> dict[str, object]:
    content = row.get("content")
    parts = content if isinstance(content, list) else []
    text = " ".join(str(part.get("text") or "") for part in parts if isinstance(part, Mapping) and part.get("type") == "text")
    return {"message_id": str(row.get("id") or ""), "role": str(row.get("role") or "unknown"), "text": text[:4000], "created_at": row.get("created_at")}


def _knowledge_view(row: Mapping[str, object]) -> dict[str, object]:
    content = row.get("content")
    return {
        "id": row.get("id"), "category": row.get("category"),
        "node_type": row.get("node_type"), "title": str(row.get("title") or "")[:200],
        "content": str(content or "")[:1000], "confidence": row.get("confidence"),
        "source": row.get("source"), "metadata": row.get("metadata") or {},
        "content_truncated": isinstance(content, str) and len(content) > 1000,
    }


def _memory_view(row: Mapping[str, object]) -> dict[str, object]:
    content = row.get("content")
    return {
        "memory_ref": f"memory:{row.get('id')}", "content": str(content or "")[:1000],
        "content_truncated": isinstance(content, str) and len(content) > 1000,
        "kind": (row.get("metadata") or {}).get("kind") if isinstance(row.get("metadata"), Mapping) else "memory",
        "valid_from": row.get("valid_from"), "valid_until": row.get("valid_until"),
        "source_message_ids": row.get("source_message_ids") or [],
    }


def _evidence_view(row: Mapping[str, object]) -> dict[str, object]:
    return {"artifact_id": row.get("artifact_id"), "source": row.get("source"), "columns": row.get("columns") or [], "query_scope": row.get("query_scope") or {}, "byte_size": row.get("byte_size"), "context_revision": row.get("context_revision")}


def _artifact_view(row: Mapping[str, object]) -> dict[str, object]:
    model_view = row.get("history_view") or row.get("model_view") or {}
    if len(_json(model_view).encode("utf-8")) > 4000:
        model_view = {"truncated": True}
    return {
        "artifact_ref": f"artifact:{row.get('id')}", "artifact_type": row.get("artifact_type"),
        "status": row.get("status"), "byte_size": row.get("byte_size"),
        "content_hash": row.get("content_hash"), "model_view": model_view,
        "metadata": row.get("metadata") or {}, "context_revision": row.get("context_revision"),
    }


def _bound_mapping(value: Mapping[str, object], max_tokens: object, maximum: int) -> dict[str, object]:
    limit = _bounded_int(max_tokens, 2000, maximum)
    encoded = _json(value)
    if len(encoded) <= limit * 2:
        return dict(value)
    return {"artifact_id": value.get("artifact_id"), "byte_size": value.get("byte_size"), "truncated": True}


def _bounded_int(value: object, default: int, maximum: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > maximum:
        raise ValueError("READ_INTEGER_OUT_OF_RANGE")
    return max(value, 1)


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
