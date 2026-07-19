"""从持久 ConversationItem 构建固定 revision 的模型历史。"""

from __future__ import annotations

import json
from typing import Any

from services.handlers.interrupt_anchor import fix_orphan_tool_calls


_MAX_CONTEXT_ITEMS = 200
_MAX_CONTEXT_SCAN = 5_000


def load_unified_context_messages(
    db: Any,
    *,
    conversation_id: str,
    base_revision: int,
    summary_revision: int,
    org_id: str | None,
) -> tuple[list[dict[str, Any]], bool]:
    """返回新事实投影和是否命中新主链；空会话允许 legacy 回退。"""
    compaction = _load_latest_compaction(
        db,
        conversation_id=conversation_id,
        base_revision=base_revision,
        org_id=org_id,
    )
    query = (
        db.table("conversation_context_items")
        .select(
            "item_type,payload,group_id,sequence,context_revision,"
            "source_message_id"
        )
        .eq("conversation_id", conversation_id)
        .lte("context_revision", base_revision)
        .order("sequence", desc=True)
    )
    if org_id:
        query = query.eq("org_id", org_id)
    if summary_revision > 0:
        query = query.gt("context_revision", summary_revision)
    if compaction is not None:
        query = query.gt(
            "sequence",
            int(compaction.get("through_sequence") or 0),
        )
    rows = _load_context_rows(query)
    rows = [
        row for row in rows
        if isinstance(row, dict) and row.get("item_type")
    ]
    if not rows and compaction is None:
        return [], False
    rows.sort(key=lambda row: int(row.get("sequence") or 0))
    messages = _project_compaction(compaction) + [
        {
            **message,
            "_context_sequence": int(row.get("sequence") or 0),
            "_context_revision": int(row.get("context_revision") or 0),
        }
        for row in rows
        for message in _project_item(row)
    ]
    return fix_orphan_tool_calls(messages), True


def _load_context_rows(query: Any) -> list[dict[str, Any]]:
    """分页读取，达到安全扫描上限时明确失败，禁止静默丢弃旧事实。"""
    rows: list[dict[str, Any]] = []
    for offset in range(0, _MAX_CONTEXT_SCAN, _MAX_CONTEXT_ITEMS):
        result = query.range(
            offset,
            offset + _MAX_CONTEXT_ITEMS - 1,
        ).execute()
        page = result.data if result and isinstance(result.data, list) else []
        rows.extend(row for row in page if isinstance(row, dict))
        if len(page) < _MAX_CONTEXT_ITEMS:
            return rows
    raise RuntimeError("CONTEXT_ITEM_SCAN_LIMIT_EXCEEDED")


def _load_latest_compaction(
    db: Any,
    *,
    conversation_id: str,
    base_revision: int,
    org_id: str | None,
) -> dict[str, Any] | None:
    query = (
        db.table("conversation_compactions")
        .select(
            "id,through_sequence,summary_payload,summary_hash,"
            "context_revision"
        )
        .eq("conversation_id", conversation_id)
        .eq("status", "ready")
        .lte("context_revision", base_revision)
        .order("through_sequence", desc=True)
        .range(0, 0)
    )
    if org_id:
        query = query.eq("org_id", org_id)
    result = query.execute()
    rows = result.data if result and isinstance(result.data, list) else []
    return rows[0] if rows and isinstance(rows[0], dict) else None


def _project_compaction(
    compaction: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if compaction is None:
        return []
    payload = compaction.get("summary_payload")
    payload = payload if isinstance(payload, dict) else {}
    return [{
        "role": "system",
        "content": f"[结构化历史压缩]\n{_dumps(payload)}",
        "_context_sequence": int(compaction.get("through_sequence") or 0),
        "_context_revision": int(compaction.get("context_revision") or 0),
        "_context_compaction_id": str(compaction.get("id") or ""),
    }]


def _project_item(row: dict[str, Any]) -> list[dict[str, Any]]:
    item_type = str(row.get("item_type") or "")
    payload = row.get("payload")
    payload = payload if isinstance(payload, dict) else {}
    if item_type == "user":
        return [{"role": "user", "content": _project_user(payload)}]
    if item_type == "assistant":
        content = _project_assistant(payload)
        return [{"role": "assistant", "content": content}] if content else []
    if item_type == "tool_call":
        arguments = payload.get("arguments") or {}
        if not isinstance(arguments, str):
            arguments = _dumps(arguments)
        return [{
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": str(payload.get("tool_call_id") or ""),
                "type": "function",
                "function": {
                    "name": str(payload.get("tool_name") or "tool"),
                    "arguments": arguments,
                },
            }],
        }]
    if item_type == "tool_result":
        return [{
            "role": "tool",
            "tool_call_id": str(payload.get("tool_call_id") or ""),
            "content": _dumps(payload),
        }]
    if item_type == "artifact_ref":
        return [{
            "role": "assistant",
            "content": f"[历史 Artifact]\n{_dumps(payload)}",
        }]
    if item_type == "compaction":
        return [{
            "role": "system",
            "content": f"[历史压缩摘要]\n{_dumps(payload)}",
        }]
    if item_type == "interrupt":
        return [{"role": "system", "content": _dumps(payload)}]
    return []


def _project_user(payload: dict[str, Any]) -> Any:
    content = payload.get("content")
    if not isinstance(content, list):
        return _message_reference(payload)
    parts: list[dict[str, Any]] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        part_type = part.get("type")
        if part_type == "text" and part.get("text"):
            parts.append({"type": "text", "text": str(part["text"])})
        elif part_type == "image" and part.get("url"):
            parts.append({
                "type": "image_url",
                "image_url": {"url": str(part["url"])},
            })
        elif part_type == "file":
            parts.append({
                "type": "text",
                "text": f"[历史附件：{part.get('name') or '文件'}]",
            })
    if not parts:
        return _message_reference(payload)
    if len(parts) == 1 and parts[0].get("type") == "text":
        return parts[0]["text"]
    return parts


def _project_assistant(payload: dict[str, Any]) -> str:
    content = payload.get("content")
    if not isinstance(content, dict):
        return _message_reference(payload)
    block_type = content.get("type")
    if block_type == "text":
        return str(content.get("text") or "")
    if block_type in {"image", "chart", "diagram", "file"}:
        return f"[历史产物：{block_type}]"
    return _dumps(content)


def _message_reference(payload: dict[str, Any]) -> str:
    reference = payload.get("message_ref")
    return f"[历史消息引用：{_dumps(reference)}]" if reference else ""


def _dumps(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        default=str,
    )
