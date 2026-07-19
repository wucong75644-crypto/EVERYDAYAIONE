"""层5: 当前 Run 循环内 LLM 摘要（触发式）

超阈值时把本 Run 实际淘汰的旧工具轮次压缩为临时摘要；不读写跨 Turn 记忆。
"""

from typing import Any, Dict, List

from loguru import logger

from services.handlers.context_compressor.tokens import (
    _extract_text,
    estimate_tokens,
)
from services.handlers.context_compressor.archive import (
    _identify_tool_turns,
)


_LOOP_SUMMARY_PROMPT = (
    "你是工具调用记录压缩器。按以下格式输出（最多{max_chars}字）：\n\n"
    "【已查数据】列出关键数字（金额/数量/编码/状态），每条一行，数字必须精确\n"
    "【编码映射】模糊名→精确编码（如有）\n"
    "【失败操作】操作名+原因（如有）\n"
    "【进行中】未完成的查询意图（如有）\n\n"
    '某项无内容写"无"。数字禁止近似化。直接输出，不加前缀。'
)


def _build_loop_summary_input(
    messages: List[Dict[str, Any]],
    stale_indices: List[int],
) -> str:
    """将旧轮次消息格式化为摘要输入文本。"""
    lines: List[str] = []
    for idx in stale_indices:
        msg = messages[idx]
        role = msg.get("role", "")
        content = _extract_text(msg.get("content", "") or "")
        if role == "assistant":
            # 提取工具调用名
            tool_names = [
                tc.get("function", {}).get("name", "?")
                for tc in msg.get("tool_calls", [])
            ]
            if tool_names:
                lines.append(f"AI 调用工具: {', '.join(tool_names)}")
            if content:
                text = content[:200] + "..." if len(content) > 200 else content
                lines.append(f"AI: {text}")
        elif role == "tool":
            text = content[:300] + "..." if len(content) > 300 else content
            lines.append(f"工具结果: {text}")
        elif role == "system":
            # 上下文提示（已识别编码等），简短保留
            if len(content) <= 200:
                lines.append(f"系统: {content}")
    return "\n".join(lines)


def _select_stale_indices(messages: List[Dict[str, Any]]) -> List[int]:
    """选择循环摘要将实际替换的旧工具消息。"""
    turns = _identify_tool_turns(messages)
    if len(turns) <= 2:
        return []
    first_keep_idx = min(
        turn_tools[0] - 1 for turn_tools in turns[-2:]
    )
    stale_indices: List[int] = []
    for index, message in enumerate(messages[:first_keep_idx]):
        role = message.get("role", "")
        if role in ("assistant", "tool"):
            stale_indices.append(index)
        elif role == "system":
            content = message.get("content", "")
            if "已识别编码" in content or "已用工具" in content:
                stale_indices.append(index)
    return stale_indices


def _apply_loop_summary(
    messages: List[Dict[str, Any]],
    stale_indices: List[int],
    summary: str,
) -> None:
    """以临时摘要原子替换已核验的 stale 消息。"""
    for index in reversed(stale_indices):
        messages.pop(index)
    insert_pos = next(
        (
            index for index, message in enumerate(messages)
            if message.get("role") == "assistant"
            and message.get("tool_calls")
        ),
        0,
    )
    messages.insert(insert_pos, {
        "role": "system",
        "content": f"[工具循环摘要] {summary}",
    })


async def compact_loop_with_summary(
    messages: List[Dict[str, Any]],
    max_tokens: int,
    trigger_ratio: float = 0.8,
    *,
    suppression_scope: str | None = None,
) -> bool:
    """超阈值时调 LLM 将旧轮次消息压缩为摘要（原地修改）。

    触发条件：estimate_tokens > max_tokens * trigger_ratio
    失败降级：跳过摘要，靠 enforce_budget 兜底。

    Returns:
        是否执行了摘要压缩
    """
    current = estimate_tokens(messages)
    threshold = int(max_tokens * trigger_ratio)
    if current <= threshold:
        return False

    stale_indices = _select_stale_indices(messages)
    if not stale_indices:
        return False

    # 构建摘要输入
    summary_input = _build_loop_summary_input(messages, stale_indices)
    if not summary_input:
        return False

    from services.agent.runtime.context import (
        acquire_loop_compaction,
        compaction_prefix_fingerprint,
        finish_loop_compaction,
    )

    prefix_fingerprint = compaction_prefix_fingerprint(
        messages,
        stale_indices,
    )
    acquired = False
    if suppression_scope:
        outcome = await acquire_loop_compaction(
            suppression_scope,
            prefix_fingerprint,
        )
        if outcome != "acquired":
            logger.info(
                f"Loop summary skipped | reason={outcome} | "
                f"scope={suppression_scope}"
            )
            return False
        acquired = True

    suppress = False
    try:
        from services.context_summarizer import _call_summary_model
        from core.config import settings

        max_chars = 500

        loop_prompt = _LOOP_SUMMARY_PROMPT.format(max_chars=max_chars)
        summary = await _call_summary_model(
            settings.context_summary_model,
            summary_input,
            system_prompt_override=loop_prompt,
        )
        if not summary:
            summary = await _call_summary_model(
                settings.context_summary_fallback_model,
                summary_input,
                system_prompt_override=loop_prompt,
            )
        if not summary:
            logger.warning("Loop summary failed, skipping (fallback to enforce_budget)")
            suppress = True
            return False

        # 截断超长摘要
        if len(summary) > max_chars:
            summary = summary[:max_chars]

        if compaction_prefix_fingerprint(messages, stale_indices) != prefix_fingerprint:
            logger.info("Loop summary discarded | reason=prefix_changed")
            return False

        _apply_loop_summary(messages, stale_indices, summary)

        logger.info(
            f"Loop summary applied | removed={len(stale_indices)} msgs | "
            f"summary_len={len(summary)} | "
            f"tokens_before={current} | tokens_after={estimate_tokens(messages)}"
        )
        return True
    except Exception as e:
        logger.warning(f"Loop summary error, skipping | error={e}")
        suppress = True
        return False
    finally:
        if acquired:
            await finish_loop_compaction(
                suppression_scope,
                prefix_fingerprint,
                suppress=suppress,
            )
