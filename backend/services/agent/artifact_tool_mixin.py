"""当前 Run 内通用 Artifact Search/Get/Read。"""

from __future__ import annotations

import json
from typing import Any

from services.agent.agent_result import AgentResult


class ArtifactToolMixin:
    """通过 RuntimeState 读取本轮已产生的完整工具事实。"""

    async def _artifact_search(self, args: dict[str, Any]) -> AgentResult:
        store = self._artifact_store()
        repository = self._artifact_repository()
        if store is None and repository is None:
            return AgentResult(
                summary="当前执行没有可访问的 Artifact",
                status="empty",
            )
        query = str(args.get("query") or "")
        limit = max(1, min(int(args.get("limit") or 5), 20))
        current = store.search(query, limit=limit) if store else ()
        persisted = repository.search(query, limit=limit) if repository else ()
        matches = tuple(current) + tuple(
            item for item in persisted
            if item.get("artifact_id") not in {
                current_item.get("artifact_id") for current_item in current
            }
        )
        matches = matches[:limit]
        return AgentResult(
            summary=_dumps({"count": len(matches), "artifacts": matches}),
            status="success" if matches else "empty",
        )

    async def _artifact_get(self, args: dict[str, Any]) -> AgentResult:
        store = self._artifact_store()
        repository = self._artifact_repository()
        artifact_id = str(args.get("artifact_id") or "").strip()
        draft = store.get(artifact_id) if store and artifact_id else None
        persisted = (
            repository.get(artifact_id)
            if draft is None and repository and artifact_id else None
        )
        if draft is None and persisted is None:
            return AgentResult(
                summary=f"未找到当前执行可访问的 Artifact：{artifact_id}",
                status="empty",
            )
        payload = (
            draft.directory_item()
            if draft is not None
            else _persistent_directory_item(persisted)
        )
        return AgentResult(
            summary=_dumps(payload),
            status="success",
        )

    async def _artifact_read(self, args: dict[str, Any]) -> AgentResult:
        store = self._artifact_store()
        repository = self._artifact_repository()
        artifact_id = str(args.get("artifact_id") or "").strip()
        if (store is None and repository is None) or not artifact_id:
            return AgentResult(
                summary="artifact_id 无效或当前执行没有 Artifact 访问范围",
                status="error",
                error_message="Validation: scoped artifact_id is required",
            )
        cursor = max(0, int(args.get("cursor") or 0))
        max_tokens = max(256, min(
            int(args.get("max_tokens") or 4000), 16000
        ))
        page = (
            store.read(
                artifact_id, cursor=cursor, max_tokens=max_tokens
            ) if store else None
        )
        if page is None and repository is not None:
            page = await repository.read(
                artifact_id, cursor=cursor, max_tokens=max_tokens
            )
        if page is None:
            return AgentResult(
                summary=f"未找到当前执行可访问的 Artifact：{artifact_id}",
                status="empty",
            )
        return AgentResult(summary=_dumps(page.to_dict()), status="success")

    def _artifact_store(self) -> Any:
        state = getattr(self, "runtime_state", None)
        return getattr(state, "artifacts", None)

    def _artifact_repository(self) -> Any:
        state = getattr(self, "runtime_state", None)
        conversation_id = getattr(state, "conversation_id", None)
        base_revision = getattr(state, "base_revision", None)
        if (
            not conversation_id
            or not isinstance(base_revision, int)
            or conversation_id != getattr(self, "conversation_id", None)
        ):
            return None
        from services.agent.runtime.artifacts.repository import (
            PersistentArtifactRepository,
        )

        return PersistentArtifactRepository(
            self.db,
            conversation_id=conversation_id,
            base_revision=base_revision,
            org_id=getattr(state, "org_id", None),
        )


def _dumps(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _persistent_directory_item(row: dict[str, Any] | None) -> dict[str, Any]:
    row = row or {}
    return {
        "artifact_id": str(row.get("id") or ""),
        "tool_call_id": row.get("tool_call_id"),
        "tool_name": str(row.get("tool_name") or ""),
        "artifact_type": str(row.get("artifact_type") or ""),
        "status": str(row.get("status") or ""),
        "byte_size": row.get("byte_size"),
        "content_hash": row.get("content_hash"),
        "model_view": row.get("model_view") or {},
        "metadata": row.get("metadata") or {},
        "context_revision": row.get("context_revision"),
    }
