"""固定会话与 revision 作用域内的 Evidence Search/Get。"""

from __future__ import annotations

import json
from typing import Any


_SEARCH_SCAN_LIMIT = 200
_SEARCH_RESULT_LIMIT = 10
_GET_MAX_TOKENS = 4000
_CHARS_PER_TOKEN = 2.5


class EvidenceToolMixin:
    """只读访问 RuntimeState 绑定范围内的持久化 Evidence。"""

    async def _evidence_search(self, args: dict[str, Any]) -> Any:
        from services.agent.agent_result import AgentResult
        from services.agent.runtime.context import record_context_event

        scope = self._evidence_scope()
        if scope is None:
            record_context_event(
                "context_evidence_search",
                outcome="forbidden",
                org_id=getattr(self, "org_id", None),
            )
            return AgentResult(
                summary="当前任务没有可访问的历史数据证据",
                status="empty",
            )
        query = str(args.get("query") or "").strip().casefold()[:200]
        limit = max(1, min(int(args.get("limit") or 5), _SEARCH_RESULT_LIMIT))
        evidence_query = (
            self.db.table("conversation_data_evidence")
            .select(
                "artifact_id,source,columns,query_scope,model_view,"
                "byte_size,context_revision"
            )
            .eq("conversation_id", scope["conversation_id"])
            .lte("context_revision", scope["base_revision"])
            .eq("validation_status", "ready")
            .order("context_revision", desc=True)
        )
        before_revision = args.get("before_revision")
        if isinstance(before_revision, int) and before_revision > 0:
            evidence_query = evidence_query.lt(
                "context_revision",
                min(before_revision, scope["base_revision"] + 1),
            )
        result = evidence_query.range(0, _SEARCH_SCAN_LIMIT - 1).execute()
        rows = result.data if result and isinstance(result.data, list) else []
        matches = [
            _directory_item(row)
            for row in rows
            if not query or query in _search_blob(row)
        ][:_SEARCH_RESULT_LIMIT]
        matches = matches[:limit]
        record_context_event(
            "context_evidence_search",
            outcome="success" if matches else "empty",
            org_id=getattr(self, "org_id", None),
            conversation_id=scope["conversation_id"],
            base_revision=scope["base_revision"],
            result_count=len(matches),
        )
        return AgentResult(
            summary=_dumps({
                "count": len(matches),
                "evidence": matches,
                "next_before_revision": (
                    rows[-1].get("context_revision")
                    if len(rows) == _SEARCH_SCAN_LIMIT else None
                ),
            }),
            status="success" if matches else "empty",
        )

    async def _evidence_get(self, args: dict[str, Any]) -> Any:
        from services.agent.agent_result import AgentResult
        from services.agent.runtime.context import record_context_event

        scope = self._evidence_scope()
        artifact_id = str(args.get("artifact_id") or "").strip()
        if scope is None or not artifact_id:
            record_context_event(
                "context_evidence_get",
                outcome="forbidden",
                org_id=getattr(self, "org_id", None),
            )
            return AgentResult(
                summary="artifact_id 无效或当前任务没有 Evidence 访问范围",
                status="error",
                error_message="Validation: scoped artifact_id is required",
            )
        result = (
            self.db.table("conversation_data_evidence")
            .select(
                "artifact_id,source,columns,rows,file_ref,query_scope,"
                "metric_definitions,model_view,byte_size,context_revision"
            )
            .eq("conversation_id", scope["conversation_id"])
            .eq("artifact_id", artifact_id)
            .lte("context_revision", scope["base_revision"])
            .eq("validation_status", "ready")
            .maybe_single()
            .execute()
        )
        row = result.data if result else None
        if not isinstance(row, dict):
            record_context_event(
                "context_evidence_get",
                outcome="empty",
                org_id=getattr(self, "org_id", None),
                conversation_id=scope["conversation_id"],
                base_revision=scope["base_revision"],
            )
            return AgentResult(
                summary=f"未找到可访问的 Evidence：{artifact_id}",
                status="empty",
            )
        selector = str(args.get("selector") or "model_view")
        max_tokens = max(
            256, min(int(args.get("max_tokens") or 2000), _GET_MAX_TOKENS),
        )
        payload = _get_payload(row, selector)
        bounded = _bound_payload(payload, max_tokens)
        record_context_event(
            "context_evidence_get",
            outcome="success",
            org_id=getattr(self, "org_id", None),
            conversation_id=scope["conversation_id"],
            base_revision=scope["base_revision"],
            selector=selector,
            truncated=bool(bounded.get("truncated")),
        )
        return AgentResult(summary=_dumps(bounded), status="success")

    def _evidence_scope(self) -> dict[str, Any] | None:
        state = getattr(self, "runtime_state", None)
        conversation_id = getattr(state, "conversation_id", None)
        base_revision = getattr(state, "base_revision", None)
        if not conversation_id or not isinstance(base_revision, int):
            return None
        if conversation_id != getattr(self, "conversation_id", None):
            return None
        return {
            "conversation_id": conversation_id,
            "base_revision": base_revision,
        }


def _directory_item(row: dict[str, Any]) -> dict[str, Any]:
    model_view = row.get("model_view")
    model_view = model_view if isinstance(model_view, dict) else {}
    columns = row.get("columns")
    names = [
        str(column.get("name"))
        for column in columns
        if isinstance(column, dict) and column.get("name")
    ] if isinstance(columns, list) else []
    return {
        "artifact_id": str(row.get("artifact_id") or ""),
        "source": str(row.get("source") or ""),
        "columns": names,
        "query_scope": row.get("query_scope") or {},
        "row_count": model_view.get("row_count"),
        "tier": model_view.get("tier"),
        "byte_size": row.get("byte_size"),
        "context_revision": row.get("context_revision"),
    }


def _search_blob(row: dict[str, Any]) -> str:
    directory = _directory_item(row)
    return _dumps(directory).casefold()


def _get_payload(row: dict[str, Any], selector: str) -> dict[str, Any]:
    model_view = row.get("model_view")
    payload = (
        dict(model_view)
        if isinstance(model_view, dict)
        else _directory_item(row)
    )
    if selector == "rows" and isinstance(row.get("rows"), list):
        payload["rows"] = row["rows"]
    return payload


def _bound_payload(payload: dict[str, Any], max_tokens: int) -> dict[str, Any]:
    max_chars = int(max_tokens * _CHARS_PER_TOKEN)
    bounded = dict(payload)
    for key in ("rows", "sample_rows"):
        values = bounded.get(key)
        if not isinstance(values, list):
            continue
        values = list(values)
        bounded[key] = values
        while values and len(_dumps(bounded)) > max_chars:
            values.pop()
    if len(_dumps(bounded)) <= max_chars:
        return bounded
    return {
        "artifact_id": bounded.get("artifact_id"),
        "tier": bounded.get("tier"),
        "row_count": bounded.get("row_count"),
        "byte_size": bounded.get("byte_size"),
        "truncated": True,
    }


def _dumps(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
