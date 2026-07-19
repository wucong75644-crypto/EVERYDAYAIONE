"""按模型预算组装历史，并为被替换的稳定前缀生成结构化压缩。"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any

from services.agent.runtime.context.budget import ContextBudget
from services.handlers.context_compressor.tokens import estimate_tokens


_COMPACTION_NAMESPACE = uuid.UUID("439782ee-28e9-4f15-8bc4-b1c624236448")
_PROMPT_VERSION = "unified-context-v1"
_MAX_SUMMARY_CHARS = 8_000


@dataclass(frozen=True)
class ContextPlan:
    """一次 Prompt 将消费的历史和可随 Actor 原子提交的压缩产物。"""

    messages: list[dict[str, Any]]
    compaction: dict[str, Any] | None
    trimmed_refs: tuple[int, ...]
    estimated_tokens: int


async def assemble_history(
    messages: list[dict[str, Any]],
    budget: ContextBudget,
) -> ContextPlan:
    """软阈值内原样消费；超阈值时按完整 Turn 压缩稳定前缀。"""
    clean = [_provider_message(message) for message in messages]
    current_tokens = estimate_tokens(clean)
    if current_tokens <= budget.soft_compaction:
        return ContextPlan(clean, None, (), current_tokens)

    cut = _stable_prefix_cut(messages)
    if cut <= 0:
        if current_tokens > budget.hard_compaction:
            raise RuntimeError("CONTEXT_REQUIRED_BLOCKS_EXCEED_HARD_LIMIT")
        return ContextPlan(clean, None, (), current_tokens)

    prefix = messages[:cut]
    tail = clean[cut:]
    summary_payload, model, pass_count = await _summarize_prefix(prefix)
    summary_message = {
        "role": "system",
        "content": (
            "[结构化历史压缩]\n"
            + json.dumps(
                summary_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        ),
    }
    planned = [summary_message, *tail]
    planned_tokens = estimate_tokens(planned)
    if planned_tokens > budget.hard_compaction:
        raise RuntimeError("CONTEXT_COMPACTION_EXCEEDS_HARD_LIMIT")

    sequences = tuple(
        int(message["_context_sequence"])
        for message in prefix
        if isinstance(message.get("_context_sequence"), int)
    )
    compaction = (
        _build_compaction(
            prefix,
            sequences,
            summary_payload,
            model=model,
            pass_count=pass_count,
        )
        if sequences else None
    )
    return ContextPlan(
        planned,
        compaction,
        sequences,
        planned_tokens,
    )


def _stable_prefix_cut(messages: list[dict[str, Any]]) -> int:
    """保留最近至少两个用户 Turn，并避免从 tool result 中间切开。"""
    user_indices = [
        index
        for index, message in enumerate(messages)
        if message.get("role") == "user"
    ]
    if len(user_indices) <= 2:
        return 0
    cut = user_indices[-2]
    while cut > 0 and messages[cut].get("role") == "tool":
        cut -= 1
    return cut


async def _summarize_prefix(
    messages: list[dict[str, Any]],
) -> tuple[dict[str, Any], str, int]:
    source = json.dumps(
        [_provider_message(message) for message in messages],
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    prompt = (
        "把历史压缩为 JSON 对象，只允许以下键：goals、constraints、"
        "decisions、facts、artifact_refs、failures、unfinished。每个值必须"
        "是字符串数组。精确保留数字、日期、编码、ID 和 Artifact ID；"
        "不得推测，不得输出 JSON 之外文字。"
    )
    from core.config import settings
    from services.context_summarizer import _call_summary_model

    for pass_count, model in enumerate(
        (
            settings.context_summary_model,
            settings.context_summary_fallback_model,
        ),
        start=1,
    ):
        raw = await _call_summary_model(
            model,
            source,
            system_prompt_override=prompt,
        )
        payload = _parse_summary(raw)
        if payload is not None:
            return payload, str(model), pass_count
    return _deterministic_summary(messages), "deterministic", 2


def _parse_summary(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return None
    required = (
        "goals", "constraints", "decisions", "facts",
        "artifact_refs", "failures", "unfinished",
    )
    if not isinstance(value, dict):
        return None
    result: dict[str, list[str]] = {}
    for key in required:
        items = value.get(key, [])
        if not isinstance(items, list):
            return None
        result[key] = [str(item)[:1_000] for item in items[:50]]
    encoded = json.dumps(result, ensure_ascii=False)
    return result if len(encoded) <= _MAX_SUMMARY_CHARS else None


def _deterministic_summary(
    messages: list[dict[str, Any]],
) -> dict[str, list[str]]:
    goals: list[str] = []
    facts: list[str] = []
    artifacts: list[str] = []
    failures: list[str] = []
    for message in messages:
        text = _message_text(message)
        if not text:
            continue
        excerpt = text[:500]
        role = message.get("role")
        if role == "user":
            goals.append(excerpt)
        elif role == "tool" and '"status":"error"' in text:
            failures.append(excerpt)
        else:
            facts.append(excerpt)
        if "artifact_id" in text:
            artifacts.append(excerpt)
    return {
        "goals": goals[-20:],
        "constraints": [],
        "decisions": [],
        "facts": facts[-30:],
        "artifact_refs": artifacts[-20:],
        "failures": failures[-20:],
        "unfinished": [],
    }


def _build_compaction(
    prefix: list[dict[str, Any]],
    sequences: tuple[int, ...],
    summary_payload: dict[str, Any],
    *,
    model: str,
    pass_count: int,
) -> dict[str, Any]:
    source_hash = _hash([_provider_message(message) for message in prefix])
    summary_hash = _hash(summary_payload)
    return {
        "id": str(uuid.uuid5(_COMPACTION_NAMESPACE, source_hash)),
        "from_sequence": min(sequences),
        "through_sequence": max(sequences),
        "source_hash": source_hash,
        "summary_payload": summary_payload,
        "summary_hash": summary_hash,
        "model": model,
        "prompt_version": _PROMPT_VERSION,
        "pass_count": pass_count,
        "input_tokens": estimate_tokens([
            _provider_message(message) for message in prefix
        ]),
        "output_tokens": max(
            1,
            len(json.dumps(summary_payload, ensure_ascii=False)) // 3,
        ),
    }


def _provider_message(message: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in message.items()
        if not key.startswith("_context_")
    }


def _message_text(message: dict[str, Any]) -> str:
    return json.dumps(
        _provider_message(message),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
