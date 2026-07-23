"""Provider 请求前的确定性旧 ToolResult 裁剪。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any

from services.handlers.context_compressor.archive import _extract_archive_meta
from services.handlers.context_compressor.tokens import (
    _extract_text,
    _is_archived,
    estimate_tokens,
)


@dataclass(frozen=True)
class PruningReceipt:
    """不含正文的单次确定性裁剪结果。"""

    schema_version: int
    model_step: int
    outcome: str
    trigger_tokens: int
    tokens_before: int
    tokens_after: int
    protected_user_turns: int
    eligible_tool_pairs: int
    pruned_tool_results: int
    source_hash: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def prune_context(
    messages: list[dict[str, Any]],
    *,
    usable_input: int,
    model_step: int,
    trigger_ratio: float = 0.5,
    protected_user_turns: int = 3,
) -> PruningReceipt:
    """超过阈值时只裁剪完整旧工具组中的 ToolResult。"""
    tokens_before = estimate_tokens(messages)
    trigger_tokens = int(usable_input * trigger_ratio)
    source_hash = _hash_messages(messages)
    if tokens_before < trigger_tokens:
        return _receipt(
            model_step=model_step,
            outcome="below_threshold",
            trigger_tokens=trigger_tokens,
            tokens_before=tokens_before,
            tokens_after=tokens_before,
            protected_user_turns=protected_user_turns,
            eligible_tool_pairs=0,
            pruned_tool_results=0,
            source_hash=source_hash,
        )

    eligible_groups = _eligible_tool_groups(
        messages,
        protected_user_turns=protected_user_turns,
    )
    tool_names = _tool_name_map(messages)
    pruned = 0
    for _assistant_index, tool_indices in eligible_groups:
        for index in tool_indices:
            message = messages[index]
            content = message.get("content", "")
            if _is_archived(message):
                continue
            message["content"] = _extract_archive_meta(
                _extract_text(content),
                tool_names.get(str(message.get("tool_call_id") or ""), ""),
            )
            pruned += 1

    tokens_after = estimate_tokens(messages)
    return _receipt(
        model_step=model_step,
        outcome="pruned" if pruned else "no_eligible_results",
        trigger_tokens=trigger_tokens,
        tokens_before=tokens_before,
        tokens_after=tokens_after,
        protected_user_turns=protected_user_turns,
        eligible_tool_pairs=len(eligible_groups),
        pruned_tool_results=pruned,
        source_hash=source_hash,
    )


def _eligible_tool_groups(
    messages: list[dict[str, Any]],
    *,
    protected_user_turns: int,
) -> list[tuple[int, list[int]]]:
    user_indices = [
        index
        for index, message in enumerate(messages)
        if _is_user_turn(message)
    ]
    if len(user_indices) <= protected_user_turns:
        return []
    protected_start = user_indices[-protected_user_turns]
    groups: list[tuple[int, list[int]]] = []
    for index, message in enumerate(messages[:protected_start]):
        calls = message.get("tool_calls") or []
        if message.get("role") != "assistant" or not calls:
            continue
        call_ids = {
            str(call.get("id"))
            for call in calls
            if isinstance(call, dict) and call.get("id")
        }
        tool_indices: list[int] = []
        cursor = index + 1
        while (
            cursor < protected_start
            and messages[cursor].get("role") == "tool"
        ):
            if str(messages[cursor].get("tool_call_id")) not in call_ids:
                tool_indices = []
                break
            tool_indices.append(cursor)
            cursor += 1
        if tool_indices and len(tool_indices) == len(call_ids):
            groups.append((index, tool_indices))
    return groups


def _tool_name_map(messages: list[dict[str, Any]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for message in messages:
        for call in message.get("tool_calls") or []:
            call_id = str(call.get("id") or "")
            if call_id:
                result[call_id] = str(
                    call.get("function", {}).get("name") or ""
                )
    return result


def _is_user_turn(message: dict[str, Any]) -> bool:
    if message.get("role") != "user":
        return False
    content = message.get("content")
    if not isinstance(content, list):
        return True
    return not any(
        isinstance(part, dict)
        and part.get("type") == "text"
        and part.get("text") == "[系统：以下是工具返回的图片]"
        for part in content
    )


def _hash_messages(messages: list[dict[str, Any]]) -> str:
    encoded = json.dumps(
        messages,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _receipt(**fields: Any) -> PruningReceipt:
    return PruningReceipt(schema_version=1, **fields)
