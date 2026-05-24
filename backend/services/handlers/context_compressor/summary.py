"""层5: 循环内 LLM 摘要（触发式）

超阈值时调便宜模型把旧轮次消息压缩为结构化摘要。
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


async def compact_loop_with_summary(
    messages: List[Dict[str, Any]],
    max_tokens: int,
    trigger_ratio: float = 0.8,
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

    # 识别轮次，保留最近 2 轮
    turns = _identify_tool_turns(messages)
    if len(turns) <= 2:
        return False  # 轮次不够，没有可压缩的

    # 收集旧轮次的所有消息 index（assistant + tool + 中间 system）
    keep_turns = turns[-2:]
    # 找到保留区域的最小 index（assistant 在 tool[0] 前一条）
    first_keep_idx = min(
        turn_tools[0] - 1 for turn_tools in keep_turns
    )

    # 收集 stale 区域：非 system(非上下文) 的 assistant/tool 消息
    stale_indices: List[int] = []
    for i, msg in enumerate(messages):
        if i >= first_keep_idx:
            break
        role = msg.get("role", "")
        if role in ("assistant", "tool"):
            stale_indices.append(i)
        elif role == "system":
            content = msg.get("content", "")
            if "已识别编码" in content or "已用工具" in content:
                stale_indices.append(i)

    if not stale_indices:
        return False

    # 构建摘要输入
    summary_input = _build_loop_summary_input(messages, stale_indices)
    if not summary_input:
        return False

    # Phase 5: 优先使用增量记忆（如果有，零 LLM 调用）
    try:
        from services.handlers.session_memory import format_session_memory
        pre_built = format_session_memory()
    except Exception:
        pre_built = None

    try:
        from services.context_summarizer import _call_summary_model
        from core.config import settings

        max_chars = 500

        if pre_built:
            # 增量记忆可用，跳过 LLM 调用
            summary = pre_built[:max_chars]
            logger.info(f"Loop summary: using pre-built session memory | len={len(summary)}")
        else:
            # 退化为 LLM 摘要
            loop_prompt = _LOOP_SUMMARY_PROMPT.format(max_chars=max_chars)
            summary = await _call_summary_model(
                settings.context_summary_model, summary_input,
                system_prompt_override=loop_prompt,
            )
            if not summary:
                summary = await _call_summary_model(
                    settings.context_summary_fallback_model, summary_input,
                    system_prompt_override=loop_prompt,
                )
            if not summary:
                logger.warning("Loop summary failed, skipping (fallback to enforce_budget)")
                return False

        # 截断超长摘要
        if len(summary) > max_chars:
            summary = summary[:max_chars]

    except Exception as e:
        logger.warning(f"Loop summary error, skipping | error={e}")
        return False

    # 用摘要替换旧消息：删除 stale 消息，插入一条 system 摘要
    summary_msg = {
        "role": "system",
        "content": f"[工具循环摘要] {summary}",
    }

    # 从后往前删除 stale 消息（保持 index 稳定）
    for idx in reversed(stale_indices):
        messages.pop(idx)

    # 找到第一条 assistant(tool_calls) 的位置，在其前面插入摘要
    insert_pos = 0
    for i, msg in enumerate(messages):
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            insert_pos = i
            break
    messages.insert(insert_pos, summary_msg)

    logger.info(
        f"Loop summary applied | removed={len(stale_indices)} msgs | "
        f"summary_len={len(summary)} | "
        f"tokens_before={current} | tokens_after={estimate_tokens(messages)}"
    )
    return True
