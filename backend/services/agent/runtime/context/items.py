"""把本 Turn 输入、输出和工具 Artifact 投影为 ConversationItem。"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any, Iterable

from services.agent.runtime.artifacts import ArtifactDraft


_GROUP_NAMESPACE = uuid.UUID("7ded8da4-d657-453a-8327-f4da49f41be2")
_MAX_PAYLOAD_BYTES = 256 * 1024
_ASSISTANT_SEQUENCE_START = 500
_MAX_LOCAL_SEQUENCE = 999


def build_turn_context_items(
    *,
    input_content: list[Any],
    output_blocks: list[dict[str, Any]],
    artifacts: Iterable[ArtifactDraft],
    input_message_id: str,
    output_message_id: str,
) -> list[dict[str, Any]]:
    """构建当前闭合 Turn；工具调用和结果始终共享原子 group_id。"""
    by_call = {
        draft.tool_call_id: draft
        for draft in artifacts
        if draft.tool_call_id
    }
    items = [
        _item(
            local_sequence=0,
            item_type="user",
            payload=_bounded_payload(
                {"content": [_dump_part(part) for part in input_content]},
                source_message_id=input_message_id,
            ),
            source_message_id=input_message_id,
        )
    ]
    sequence = _ASSISTANT_SEQUENCE_START
    for block_index, block in enumerate(output_blocks):
        block_type = str(block.get("type") or "")
        if block_type == "tool_step":
            call_id = str(block.get("tool_call_id") or "")
            group_id = str(uuid.uuid5(
                _GROUP_NAMESPACE,
                f"{output_message_id}:{call_id}:{block_index}",
            ))
            items.append(_item(
                local_sequence=sequence,
                item_type="tool_call",
                payload=_bounded_payload({
                    "tool_call_id": call_id,
                    "tool_name": str(block.get("tool_name") or ""),
                    "arguments": block.get("input") or block.get("code") or {},
                }, source_message_id=output_message_id),
                source_message_id=output_message_id,
                group_id=group_id,
            ))
            sequence += 1
            draft = by_call.get(call_id)
            items.append(_item(
                local_sequence=sequence,
                item_type="tool_result",
                payload=_tool_result_payload(
                    block, draft, output_message_id=output_message_id
                ),
                source_message_id=output_message_id,
                group_id=group_id,
            ))
        else:
            item_type = "reasoning" if block_type == "thinking" else "assistant"
            items.append(_item(
                local_sequence=sequence,
                item_type=item_type,
                payload=_bounded_payload(
                    {"content": block},
                    source_message_id=output_message_id,
                ),
                source_message_id=output_message_id,
            ))
        sequence += 1
        if sequence > _MAX_LOCAL_SEQUENCE + 1:
            raise ValueError("ACTOR_CONTEXT_ITEM_LIMIT_EXCEEDED")
    return items


def _tool_result_payload(
    block: dict[str, Any],
    draft: ArtifactDraft | None,
    *,
    output_message_id: str,
) -> dict[str, Any]:
    if draft is None:
        return _bounded_payload(
            {
                "tool_call_id": str(block.get("tool_call_id") or ""),
                "status": str(block.get("status") or "completed"),
                "output": block.get("output"),
            },
            source_message_id=output_message_id,
        )
    return {
        "tool_call_id": draft.tool_call_id,
        "artifact_id": draft.artifact_id,
        "artifact_type": draft.artifact_type,
        "status": draft.status,
        "byte_size": draft.byte_size,
        "content_hash": draft.content_hash,
        "model_view": draft.model_view,
    }


def _item(
    *,
    local_sequence: int,
    item_type: str,
    payload: dict[str, Any],
    source_message_id: str,
    group_id: str | None = None,
) -> dict[str, Any]:
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        default=str,
    )
    return {
        "local_sequence": local_sequence,
        "item_type": item_type,
        "group_id": group_id,
        "source_message_id": source_message_id,
        "payload": payload,
        "content_hash": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }


def _bounded_payload(
    payload: dict[str, Any],
    *,
    source_message_id: str,
) -> dict[str, Any]:
    encoded = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), default=str,
    ).encode("utf-8")
    if len(encoded) <= _MAX_PAYLOAD_BYTES:
        return payload
    return {
        "message_ref": {
            "message_id": source_message_id,
            "content_hash": hashlib.sha256(encoded).hexdigest(),
        },
        "byte_size": len(encoded),
    }


def _dump_part(part: Any) -> Any:
    dumper = getattr(part, "model_dump", None)
    return dumper(exclude_none=True) if callable(dumper) else part
