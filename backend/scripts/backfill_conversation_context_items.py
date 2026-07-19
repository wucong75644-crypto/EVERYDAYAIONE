"""把既有 messages 幂等投影为统一 ConversationItem 与 Artifact。

默认只做 dry-run：
    python backend/scripts/backfill_conversation_context_items.py
    python backend/scripts/backfill_conversation_context_items.py --apply
    python backend/scripts/backfill_conversation_context_items.py --apply --batch-size 500
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import psycopg
from psycopg.types.json import Jsonb


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
NAMESPACE = uuid.UUID("297d5e70-646a-4c62-a22b-71fc6872ac3f")
INLINE_PAYLOAD_BYTES = 64 * 1024
MAX_ITEM_PAYLOAD_BYTES = 256 * 1024
MAX_LOCAL_SEQUENCE = 999


@dataclass(frozen=True)
class Projection:
    """单条历史消息的确定性投影。"""

    items: tuple[dict[str, Any], ...]
    artifacts: tuple[dict[str, Any], ...]


@dataclass
class Stats:
    """回填扫描和写入计数。"""

    messages: int = 0
    items: int = 0
    artifacts: int = 0


def canonical_json(value: Any) -> str:
    """生成跨进程稳定的 JSON 表示。"""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def content_hash(value: Any) -> str:
    """计算规范 JSON 的 SHA-256。"""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def stable_uuid(*parts: object) -> str:
    """按业务身份生成可重跑的 UUID。"""
    return str(uuid.uuid5(NAMESPACE, ":".join(str(part) for part in parts)))


def decode_content(value: Any) -> list[dict[str, Any]]:
    """把 messages.content 统一为 block 列表。"""
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            decoded = None
        if isinstance(decoded, list):
            value = decoded
        else:
            return [{"type": "text", "text": value}]
    if not isinstance(value, list):
        return []
    return [block for block in value if isinstance(block, dict)]


def project_message(row: dict[str, Any]) -> Projection:
    """把一条历史消息拆成 typed items，并把大事实转为 message_slice。"""
    revision = int(row["context_revision"])
    message_id = str(row["id"])
    conversation_id = str(row["conversation_id"])
    role = str(row["role"])
    items: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = []

    for block_index, block in enumerate(decode_content(row.get("content"))):
        block_type = str(block.get("type") or "text")
        if role == "assistant" and block_type == "thinking":
            reasoning_payload = {"text": block.get("text", "")}
            if _json_bytes(reasoning_payload) > MAX_ITEM_PAYLOAD_BYTES:
                artifact = _artifact_for_block(
                    row, block, block_index, artifact_type="text"
                )
                artifacts.append(artifact)
                reasoning_payload = _artifact_payload(artifact)
            _append_item(
                items, row, "reasoning", reasoning_payload,
                block_index=block_index,
            )
            continue
        if role == "assistant" and block_type == "tool_step":
            _project_tool_step(
                row=row,
                block=block,
                block_index=block_index,
                items=items,
                artifacts=artifacts,
            )
            continue
        if role == "assistant" and block_type == "tool_result":
            _project_standalone_result(
                row=row,
                block=block,
                block_index=block_index,
                items=items,
                artifacts=artifacts,
            )
            continue

        item_type = "user" if role == "user" else "assistant"
        payload = {"content": block}
        if _json_bytes(payload) > MAX_ITEM_PAYLOAD_BYTES:
            artifact = _artifact_for_block(
                row, block, block_index, artifact_type="mixed"
            )
            artifacts.append(artifact)
            payload = _artifact_payload(artifact)
            item_type = "artifact_ref"
        _append_item(
            items, row, item_type, payload, block_index=block_index
        )

    if not items:
        payload = {"content": {"type": "text", "text": ""}}
        _append_item(items, row, role, payload, block_index=0)

    if len(items) > MAX_LOCAL_SEQUENCE + 1:
        raise ValueError(
            f"message {message_id} projects to too many context items"
        )
    for local_index, item in enumerate(items):
        local_sequence = _message_local_sequence(row, local_index)
        item["local_sequence"] = local_sequence
        item["sequence"] = revision * 1000 + local_sequence
        item["id"] = stable_uuid(message_id, local_sequence, item["item_type"])
    return Projection(tuple(items), tuple(artifacts))


def _project_tool_step(
    *,
    row: dict[str, Any],
    block: dict[str, Any],
    block_index: int,
    items: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
) -> None:
    call_id = str(
        block.get("tool_call_id")
        or block.get("id")
        or stable_uuid(row["id"], block_index, "tool-call")
    )
    group_id = stable_uuid(row["id"], call_id, "group")
    tool_name = str(block.get("tool_name") or block.get("name") or "tool")
    arguments = block.get("input", block.get("arguments", {}))
    call_payload = {
        "tool_call_id": call_id,
        "tool_name": tool_name,
        "arguments": arguments,
    }
    if _json_bytes(call_payload) > MAX_ITEM_PAYLOAD_BYTES:
        argument_artifact = _artifact_for_block(
            row,
            arguments,
            block_index,
            artifact_type="json",
            tool_call_id=call_id,
            tool_name=tool_name,
        )
        artifacts.append(argument_artifact)
        call_payload["arguments"] = _artifact_payload(argument_artifact)
    _append_item(
        items,
        row,
        "tool_call",
        call_payload,
        block_index=block_index,
        group_id=group_id,
    )
    output = block.get("output", block.get("result", block.get("text")))
    if output is None and block.get("status") not in {"completed", "error"}:
        return
    artifact = _artifact_for_block(
        row,
        output,
        block_index,
        artifact_type="error" if block.get("status") == "error" else "mixed",
        tool_call_id=call_id,
        tool_name=tool_name,
    )
    artifacts.append(artifact)
    _append_item(
        items,
        row,
        "tool_result",
        {
            **_artifact_payload(artifact),
            "tool_call_id": call_id,
            "is_error": block.get("status") == "error",
        },
        block_index=block_index,
        group_id=group_id,
    )


def _project_standalone_result(
    *,
    row: dict[str, Any],
    block: dict[str, Any],
    block_index: int,
    items: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
) -> None:
    artifact = _artifact_for_block(
        row,
        block.get("text", block),
        block_index,
        artifact_type="mixed",
        tool_name=str(block.get("tool_name") or ""),
    )
    artifacts.append(artifact)
    _append_item(
        items,
        row,
        "artifact_ref",
        _artifact_payload(artifact),
        block_index=block_index,
    )


def _artifact_for_block(
    row: dict[str, Any],
    value: Any,
    block_index: int,
    *,
    artifact_type: str,
    tool_call_id: str | None = None,
    tool_name: str = "",
) -> dict[str, Any]:
    digest = content_hash(value)
    artifact_id = stable_uuid(
        row["conversation_id"],
        row["id"],
        block_index,
        tool_call_id or "",
        artifact_type,
        digest,
    )
    byte_size = len(canonical_json(value).encode("utf-8"))
    return {
        "id": artifact_id,
        "conversation_id": str(row["conversation_id"]),
        "org_id": row.get("org_id"),
        "task_id": row.get("task_id"),
        "source_message_id": str(row["id"]),
        "tool_call_id": tool_call_id,
        "tool_name": tool_name,
        "artifact_type": artifact_type,
        "status": "ready",
        "storage_kind": "message_slice",
        "storage_ref": {
            "message_id": str(row["id"]),
            "block_index": block_index,
        },
        "model_view": _bounded_view(value, 40 * 1024),
        "history_view": _bounded_view(value, 8 * 1024),
        "content_hash": digest,
        "byte_size": byte_size,
        "metadata": {"backfilled": True},
        "sensitivity": "internal",
        "context_revision": int(row["context_revision"]),
    }


def _bounded_view(value: Any, max_bytes: int) -> dict[str, Any]:
    text = value if isinstance(value, str) else canonical_json(value)
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return {"content": value, "truncated": False}
    preview = encoded[:max_bytes].decode("utf-8", errors="ignore")
    return {
        "preview": preview,
        "truncated": True,
        "byte_size": len(encoded),
    }


def _artifact_payload(artifact: dict[str, Any]) -> dict[str, Any]:
    return {
        "artifact_id": artifact["id"],
        "artifact_type": artifact["artifact_type"],
        "byte_size": artifact["byte_size"],
        "content_hash": artifact["content_hash"],
        "model_view": artifact["model_view"],
    }


def _append_item(
    items: list[dict[str, Any]],
    row: dict[str, Any],
    item_type: str,
    payload: dict[str, Any],
    *,
    block_index: int,
    group_id: str | None = None,
) -> None:
    items.append({
        "conversation_id": str(row["conversation_id"]),
        "org_id": row.get("org_id"),
        "task_id": row.get("task_id"),
        "turn_id": row.get("turn_id"),
        "source_message_id": str(row["id"]),
        "item_type": item_type,
        "group_id": group_id,
        "payload": payload,
        "content_hash": content_hash(payload),
        "context_revision": int(row["context_revision"]),
        "_block_index": block_index,
    })


def _message_local_sequence(row: dict[str, Any], index: int) -> int:
    role_offset = 0 if str(row["role"]) == "user" else 500
    local_sequence = role_offset + index
    if local_sequence > MAX_LOCAL_SEQUENCE:
        raise ValueError(f"message {row['id']} exceeds local sequence range")
    return local_sequence


def _json_bytes(value: Any) -> int:
    return len(canonical_json(value).encode("utf-8"))


def iter_rows(
    conn: psycopg.Connection[Any], batch_size: int
) -> Iterable[list[dict[str, Any]]]:
    """使用 server-side cursor 批量读取可进入上下文的历史消息。"""
    query = """
        SELECT m.id, m.conversation_id, m.org_id, m.turn_id, m.role::text,
               m.content, m.context_revision,
               COALESCE(ta.id, ti.id) AS task_id
          FROM messages m
          LEFT JOIN tasks ta ON ta.assistant_message_id = m.id
          LEFT JOIN tasks ti ON ti.input_message_id = m.id
         WHERE m.message_kind = 'conversation'
           AND m.context_revision > 0
           AND m.status::text IN ('completed', 'interrupted')
         ORDER BY m.conversation_id, m.context_revision, m.created_at, m.id
    """
    with conn.cursor(
        name="context_backfill",
        row_factory=psycopg.rows.dict_row,
        withhold=True,
    ) as cursor:
        cursor.execute(query)
        while rows := cursor.fetchmany(batch_size):
            yield rows


def insert_projection(
    conn: psycopg.Connection[Any], projection: Projection
) -> None:
    """插入一个投影；唯一约束保证重复执行不重复写入。"""
    with conn.cursor() as cursor:
        for artifact in projection.artifacts:
            cursor.execute(
                """
                INSERT INTO conversation_artifacts(
                    id, conversation_id, org_id, task_id, source_message_id,
                    tool_call_id, tool_name, artifact_type, status,
                    storage_kind, storage_ref, model_view, history_view,
                    content_hash, byte_size, metadata, sensitivity,
                    context_revision
                ) VALUES (
                    %(id)s, %(conversation_id)s, %(org_id)s, %(task_id)s,
                    %(source_message_id)s, %(tool_call_id)s, %(tool_name)s,
                    %(artifact_type)s, %(status)s, %(storage_kind)s,
                    %(storage_ref)s, %(model_view)s, %(history_view)s,
                    %(content_hash)s, %(byte_size)s, %(metadata)s,
                    %(sensitivity)s, %(context_revision)s
                )
                ON CONFLICT (id) DO NOTHING
                """,
                _json_params(artifact),
            )
        for item in projection.items:
            cursor.execute(
                """
                INSERT INTO conversation_context_items(
                    id, conversation_id, org_id, task_id, turn_id,
                    source_message_id, sequence, local_sequence, item_type,
                    group_id, payload, content_hash, context_revision
                ) VALUES (
                    %(id)s, %(conversation_id)s, %(org_id)s, %(task_id)s,
                    %(turn_id)s, %(source_message_id)s, %(sequence)s,
                    %(local_sequence)s, %(item_type)s, %(group_id)s,
                    %(payload)s, %(content_hash)s, %(context_revision)s
                )
                ON CONFLICT (conversation_id, sequence) DO NOTHING
                """,
                _json_params(item),
            )


def _json_params(value: dict[str, Any]) -> dict[str, Any]:
    params = {key: item for key, item in value.items() if not key.startswith("_")}
    for key in ("storage_ref", "model_view", "history_view", "metadata", "payload"):
        if key in params:
            params[key] = Jsonb(params[key])
    return params


def load_env() -> None:
    """从项目环境文件加载 DATABASE_URL，不覆盖进程环境。"""
    for path in (BACKEND / ".env", ROOT / ".env"):
        if not path.exists():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key, value.strip().strip("\"'"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    if args.batch_size <= 0 or (args.limit is not None and args.limit <= 0):
        parser.error("batch-size and limit must be positive")

    load_env()
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")

    stats = Stats()
    with psycopg.connect(database_url) as conn:
        for rows in iter_rows(conn, args.batch_size):
            for row in rows:
                if args.limit is not None and stats.messages >= args.limit:
                    break
                projection = project_message(row)
                stats.messages += 1
                stats.items += len(projection.items)
                stats.artifacts += len(projection.artifacts)
                if args.apply:
                    insert_projection(conn, projection)
            if args.apply:
                conn.commit()
            if args.limit is not None and stats.messages >= args.limit:
                break
        if not args.apply:
            conn.rollback()

    mode = "apply" if args.apply else "dry-run"
    print(
        f"{mode}: messages={stats.messages} items={stats.items} "
        f"artifacts={stats.artifacts}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
