"""当前 Run 的唯一 LLM Compaction 合同。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from loguru import logger

from services.handlers.context_compressor.archive import _identify_tool_turns
from services.handlers.context_compressor.tokens import _extract_text, estimate_tokens


_COMPACTION_PROMPT = (
    "你是工具调用记录压缩器。按以下格式输出（最多{max_chars}字）：\n\n"
    "【已查数据】列出关键数字（金额/数量/编码/状态），每条一行，数字必须精确\n"
    "【编码映射】模糊名→精确编码（如有）\n"
    "【失败操作】操作名+原因（如有）\n"
    "【进行中】未完成的查询意图（如有）\n\n"
    '某项无内容写"无"。数字禁止近似化。直接输出，不加前缀。'
)


@dataclass(frozen=True)
class CompactionReceipt:
    """不含正文的当前 Run Compaction 结果。"""

    schema_version: int
    model_step: int
    outcome: str
    trigger_tokens: int
    tokens_before: int
    tokens_after: int
    removed_messages: int
    summary_chars: int
    prefix_hash: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


async def compact_context(
    messages: list[dict[str, Any]],
    *,
    usable_input: int,
    trigger_ratio: float = 0.85,
    suppression_scope: str | None = None,
    model_step: int = 0,
) -> CompactionReceipt:
    """达到阈值时摘要旧工具轮次；失败保留原消息并进入 suppression。"""
    tokens_before = estimate_tokens(messages)
    trigger_tokens = int(usable_input * trigger_ratio)
    if tokens_before <= trigger_tokens:
        return _receipt(
            model_step, "below_threshold", trigger_tokens, tokens_before
        )

    stale_indices = _select_stale_indices(messages)
    if not stale_indices:
        return _receipt(model_step, "no_stale_prefix", trigger_tokens, tokens_before)
    summary_input = _build_compaction_input(messages, stale_indices)
    if not summary_input:
        return _receipt(model_step, "empty_input", trigger_tokens, tokens_before)

    from services.agent.runtime.context.compaction_guard import (
        acquire_loop_compaction,
        compaction_prefix_fingerprint,
        finish_loop_compaction,
    )

    prefix_hash = compaction_prefix_fingerprint(messages, stale_indices)
    acquired = False
    if suppression_scope:
        coordination = await acquire_loop_compaction(
            suppression_scope,
            prefix_hash,
        )
        if coordination != "acquired":
            return _receipt(
                model_step,
                coordination,
                trigger_tokens,
                tokens_before,
                prefix_hash=prefix_hash,
            )
        acquired = True

    suppress = False
    try:
        summary = await _generate_summary(summary_input)
        if not summary:
            suppress = True
            return _receipt(
                model_step,
                "failed",
                trigger_tokens,
                tokens_before,
                prefix_hash=prefix_hash,
            )
        if compaction_prefix_fingerprint(messages, stale_indices) != prefix_hash:
            return _receipt(
                model_step,
                "stale_prefix",
                trigger_tokens,
                tokens_before,
                prefix_hash=prefix_hash,
            )
        _apply_compaction(messages, stale_indices, summary)
        tokens_after = estimate_tokens(messages)
        logger.info(
            f"Context compaction applied | removed={len(stale_indices)} | "
            f"summary_len={len(summary)} | tokens_before={tokens_before} | "
            f"tokens_after={tokens_after}"
        )
        return CompactionReceipt(
            schema_version=1,
            model_step=model_step,
            outcome="compacted",
            trigger_tokens=trigger_tokens,
            tokens_before=tokens_before,
            tokens_after=tokens_after,
            removed_messages=len(stale_indices),
            summary_chars=len(summary),
            prefix_hash=prefix_hash,
        )
    except Exception as error:
        logger.warning(f"Context compaction failed | error={error}")
        suppress = True
        return _receipt(
            model_step,
            "failed",
            trigger_tokens,
            tokens_before,
            prefix_hash=prefix_hash,
        )
    finally:
        if acquired:
            await finish_loop_compaction(
                suppression_scope,
                prefix_hash,
                suppress=suppress,
            )


async def _generate_summary(summary_input: str) -> str:
    from core.config import settings
    from services.agent.runtime.context.summary_model import call_summary_model

    max_chars = 500
    prompt = _COMPACTION_PROMPT.format(max_chars=max_chars)
    summary = await call_summary_model(
        settings.context_summary_model,
        summary_input,
        system_prompt=prompt,
        max_chars=max_chars,
    )
    if not summary:
        summary = await call_summary_model(
            settings.context_summary_fallback_model,
            summary_input,
            system_prompt=prompt,
            max_chars=max_chars,
        )
    return str(summary or "")[:max_chars]


def _build_compaction_input(
    messages: list[dict[str, Any]],
    stale_indices: list[int],
) -> str:
    lines: list[str] = []
    for index in stale_indices:
        message = messages[index]
        role = message.get("role", "")
        content = _extract_text(message.get("content", "") or "")
        if role == "assistant":
            tool_names = [
                call.get("function", {}).get("name", "?")
                for call in message.get("tool_calls", [])
            ]
            if tool_names:
                lines.append(f"AI 调用工具: {', '.join(tool_names)}")
            if content:
                lines.append(f"AI: {_bounded(content, 200)}")
        elif role == "tool":
            lines.append(f"工具结果: {_bounded(content, 300)}")
        elif role == "system" and len(content) <= 200:
            lines.append(f"系统: {content}")
    return "\n".join(lines)


def _select_stale_indices(messages: list[dict[str, Any]]) -> list[int]:
    turns = _identify_tool_turns(messages)
    if len(turns) <= 2:
        return []
    first_keep_index = min(turn[0] - 1 for turn in turns[-2:])
    return [
        index
        for index, message in enumerate(messages[:first_keep_index])
        if message.get("role") in {"assistant", "tool"}
        or (
            message.get("role") == "system"
            and any(
                marker in str(message.get("content", ""))
                for marker in ("已识别编码", "已用工具")
            )
        )
    ]


def _apply_compaction(
    messages: list[dict[str, Any]],
    stale_indices: list[int],
    summary: str,
) -> None:
    for index in reversed(stale_indices):
        messages.pop(index)
    insert_at = next(
        (
            index
            for index, message in enumerate(messages)
            if message.get("role") == "assistant" and message.get("tool_calls")
        ),
        0,
    )
    messages.insert(insert_at, {
        "role": "system",
        "content": f"[工具循环摘要] {summary}",
    })


def _bounded(value: str, limit: int) -> str:
    return value[:limit] + "..." if len(value) > limit else value


def _receipt(
    model_step: int,
    outcome: str,
    trigger_tokens: int,
    tokens: int,
    *,
    prefix_hash: str = "",
) -> CompactionReceipt:
    return CompactionReceipt(
        schema_version=1,
        model_step=model_step,
        outcome=outcome,
        trigger_tokens=trigger_tokens,
        tokens_before=tokens,
        tokens_after=tokens,
        removed_messages=0,
        summary_chars=0,
        prefix_hash=prefix_hash,
    )
