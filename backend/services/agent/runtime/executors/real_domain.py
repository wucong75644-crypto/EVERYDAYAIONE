"""Narrow Agent Runtime RPC adapters for conversation, knowledge and facts."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping
from uuid import UUID

from services.agent.runtime.executors.contracts import ActionSnapshot
from services.agent.runtime.executors.real_base import (
    RealReadCapability, bounded_limit, optional_text, read_rpc, required_text,
)


class ConversationReadCapability(RealReadCapability):
    async def _read_bound(self, snapshot: ActionSnapshot, request: Mapping[str, object]):
        value = await read_rpc(
            self.resources.database, "read_agent_runtime_conversation", snapshot,
            request, p_limit=bounded_limit(request.get("limit"), default=10, maximum=20),
        )
        return _rpc_object(value, "messages", "当前对话历史消息")


class KnowledgeReadCapability(RealReadCapability):
    async def _read_bound(self, snapshot: ActionSnapshot, request: Mapping[str, object]):
        value = await read_rpc(
            self.resources.database, "read_agent_runtime_knowledge", snapshot,
            request, p_query=required_text(request, "query"),
            p_limit=bounded_limit(request.get("limit"), default=5, maximum=10),
        )
        return _rpc_object(value, "items", "知识库检索结果")


class MemoryReadCapability(RealReadCapability):
    async def _read_bound(self, snapshot: ActionSnapshot, request: Mapping[str, object]):
        if snapshot.request.get("memory_operation") == "get" or "memory_ref" in request:
            ref = required_text(request, "memory_ref", max_len=160)
            if not ref.startswith("memory:"):
                raise ValueError("READ_MEMORY_REF_INVALID")
            try:
                ref = str(UUID(ref.removeprefix("memory:")))
            except ValueError as exc:
                raise ValueError("READ_MEMORY_REF_INVALID") from exc
            value = await read_rpc(
                self.resources.database, "read_agent_runtime_memory", snapshot,
                request, p_operation="get", p_memory_id=ref, p_query=None, p_limit=None,
            )
            return _rpc_detail(value, "记忆详情")
        value = await read_rpc(
            self.resources.database, "read_agent_runtime_memory", snapshot,
            request, p_operation="search", p_memory_id=None, p_query=required_text(request, "query"),
            p_limit=bounded_limit(request.get("limit"), default=3, maximum=6),
        )
        return _rpc_object(value, "memories", "记忆检索结果")


class EvidenceReadCapability(RealReadCapability):
    async def _read_bound(self, snapshot: ActionSnapshot, request: Mapping[str, object]):
        if "artifact_id" in request:
            value = await read_rpc(
                self.resources.database, "read_agent_runtime_evidence", snapshot,
                request, p_operation="get", p_artifact_id=required_text(request, "artifact_id"),
                p_selector=optional_text(request, "selector", max_len=20), p_query=None, p_limit=None,
            )
            return _rpc_detail(value, "Evidence 详情")
        value = await read_rpc(
            self.resources.database, "read_agent_runtime_evidence", snapshot,
            request, p_operation="search", p_artifact_id=None, p_selector=None,
                p_query=optional_text(request, "query") or "",
            p_limit=bounded_limit(request.get("limit"), default=5, maximum=10),
        )
        return _rpc_object(value, "evidence", "Evidence 检索结果")


class ArtifactReadCapability(RealReadCapability):
    async def _read_bound(self, snapshot: ActionSnapshot, request: Mapping[str, object]):
        if "query" in request and "artifact_id" not in request:
            value = await read_rpc(
                self.resources.database, "read_agent_runtime_artifact", snapshot,
                request, p_operation="search", p_artifact_id=None, p_cursor=None, p_max_tokens=None,
                p_query=optional_text(request, "query") or "",
                p_limit=bounded_limit(request.get("limit"), default=5, maximum=20),
            )
            return _rpc_object(value, "artifacts", "Artifact 检索结果")
        artifact_id = required_text(request, "artifact_id", max_len=160)
        if "cursor" not in request:
            value = await read_rpc(
                self.resources.database, "read_agent_runtime_artifact", snapshot,
                request, p_operation="get", p_artifact_id=artifact_id, p_query=None,
                p_limit=None, p_cursor=None, p_max_tokens=None,
            )
            return _rpc_detail(value, "Artifact 详情")
        value = await read_rpc(
            self.resources.database, "read_agent_runtime_artifact", snapshot,
            request, p_operation="read", p_artifact_id=artifact_id, p_query=None,
            p_limit=None,
            p_cursor=_bounded_int(request.get("cursor"), 0, 16_000),
            p_max_tokens=_bounded_int(request.get("max_tokens"), 256, 16_000),
        )
        return _rpc_detail(value, "Artifact 分页内容")


class WorkspaceReadCapability(RealReadCapability):
    async def _read_bound(self, snapshot: ActionSnapshot, request: Mapping[str, object]):
        root = self._root(snapshot)
        path = optional_text(request, "path", max_len=500)
        keyword = optional_text(request, "keyword", max_len=100)
        pattern = optional_text(request, "file_pattern", max_len=100)
        if path and not keyword and not pattern:
            items = self._entries(self._safe(root, path), root)
        elif keyword or pattern:
            items = self._search(root, keyword or "", pattern)
        else:
            items = self._entries(root, root)
        items = items[:100]
        return {"summary": "未找到匹配文件" if not items else "Workspace 文件检索结果", "count": len(items), "files": items}

    def _root(self, snapshot: ActionSnapshot) -> Path:
        if self.resources.workspace_root is None:
            raise PermissionError("READ_WORKSPACE_ROOT_REQUIRED")
        scope = snapshot.scope
        if scope.kind.value == "channel":
            return self.resources.workspace_root / "org" / str(scope.org_id) / scope.scope_id
        return self.resources.workspace_root / "personal" / scope.scope_id

    def _safe(self, root: Path, value: str) -> Path:
        if value.startswith("/") or "\\" in value or ".." in Path(value).parts:
            raise PermissionError("READ_WORKSPACE_PATH_INVALID")
        raw = root / value
        if raw.is_symlink():
            raise PermissionError("READ_WORKSPACE_SYMLINK_FORBIDDEN")
        target = raw.resolve()
        try:
            target.relative_to(root.resolve())
        except ValueError as exc:
            raise PermissionError("READ_WORKSPACE_PATH_ESCAPE") from exc
        if any(part in {".env", ".git", ".ssh", "staging"} for part in target.relative_to(root).parts):
            raise PermissionError("READ_WORKSPACE_BLOCKED_PATH")
        if target.exists() and target.is_file() and target.stat().st_nlink > 1:
            raise PermissionError("READ_WORKSPACE_HARDLINK_FORBIDDEN")
        return target

    def _entries(self, target: Path, root: Path) -> list[dict[str, object]]:
        if not target.exists():
            return []
        if target.is_file():
            return [self._file(target, root)] if target.stat().st_nlink == 1 else []
        return [self._file(item, root) for item in sorted(target.iterdir()) if self._visible(item, root)]

    def _search(self, root: Path, keyword: str, pattern: str | None) -> list[dict[str, object]]:
        if not root.exists():
            return []
        return [self._file(item, root) for item in root.rglob(pattern or "*") if self._visible(item, root) and keyword.casefold() in item.name.casefold()][:100]

    @staticmethod
    def _visible(item: Path, root: Path) -> bool:
        if item.is_symlink() or item.name.startswith(".") or "staging" in item.relative_to(root).parts:
            return False
        try:
            return item.stat().st_nlink == 1
        except OSError:
            return False

    @staticmethod
    def _file(item: Path, root: Path) -> dict[str, object]:
        stat = item.stat()
        return {"name": item.name, "relative_path": str(item.relative_to(root)), "kind": "directory" if item.is_dir() else "file", "byte_size": stat.st_size}


def _rpc_object(value: object, key: str, summary: str) -> dict[str, object]:
    if not isinstance(value, Mapping) or not isinstance(value.get(key), list):
        raise ValueError("READ_RPC_ALLOWLIST_INVALID")
    items = value[key]
    return {"summary": str(value.get("summary") or summary), "count": len(items), key: items}


def _rpc_detail(value: object, summary: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("READ_RPC_OBJECT_REQUIRED")
    return {"summary": str(value.get("summary") or summary), **{str(k): v for k, v in value.items() if k != "summary"}}


def _bounded_int(value: object, default: int, maximum: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > maximum:
        raise ValueError("READ_INTEGER_OUT_OF_RANGE")
    return max(value, 1)
