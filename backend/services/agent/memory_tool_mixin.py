"""模型主动调用的通用 Curated Memory Search/Get。"""

from __future__ import annotations

import json
from typing import Any

from services.agent.agent_result import AgentResult
from services.memory.retrieval_pipeline import RetrievalPipeline


class MemoryToolMixin:
    """在 ToolExecutor 的用户/组织范围内执行只读记忆检索。"""

    async def _memory_search(self, args: dict[str, Any]) -> AgentResult:
        query = str(args.get("query") or "").strip()
        if not query:
            return AgentResult(
                summary="query 无效",
                status="error",
                error_message="Validation: query is required",
            )
        limit = max(1, min(int(args.get("limit") or 3), 6))
        memories = await RetrievalPipeline(self.db).search(
            query=query,
            user_id=self.user_id,
            org_id=self.org_id,
            max_results=limit,
        )
        payload = [
            {
                "memory_ref": _memory_ref(item.atom_id),
                "content": item.content,
                "kind": item.kind,
                "score": round(item.score, 6),
                "valid_until": item.valid_until,
            }
            for item in memories
        ]
        return AgentResult(
            summary=_dumps({"count": len(payload), "memories": payload}),
            status="success" if payload else "empty",
        )

    async def _memory_get(self, args: dict[str, Any]) -> AgentResult:
        atom_id = _parse_memory_ref(args.get("memory_ref"))
        if atom_id is None:
            return AgentResult(
                summary="memory_ref 无效",
                status="error",
                error_message="Validation: memory_ref is required",
            )
        memory = await RetrievalPipeline(self.db).get(
            atom_id=atom_id,
            user_id=self.user_id,
            org_id=self.org_id,
        )
        if memory is None:
            return AgentResult(
                summary=f"未找到当前用户可访问的记忆：{_memory_ref(atom_id)}",
                status="empty",
            )
        return AgentResult(
            summary=_dumps({
                "memory_ref": _memory_ref(memory.atom_id),
                "content": memory.content,
                "kind": memory.kind,
                "valid_from": memory.valid_from,
                "valid_until": memory.valid_until,
                "source_message_ids": list(memory.source_message_ids),
            }),
            status="success",
        )


def _memory_ref(atom_id: str) -> str:
    return f"memory:{atom_id}"


def _parse_memory_ref(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text.startswith("memory:"):
        return None
    atom_id = text.removeprefix("memory:")
    return atom_id or None


def _dumps(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
