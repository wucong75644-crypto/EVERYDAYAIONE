"""
上下文压缩器

六层压缩：
- 层1: tool_result_envelope.wrap() — 工具结果截断+信号（chat_tool_mixin / erp_agent）
- 层2: 滑动窗口 N=10（config.chat_context_limit，chat_context_mixin）
- 层3: 对话级滚动摘要（context_summarizer → DB，chat_context_mixin）
- 层4: compact_stale_tool_results — 旧轮次工具结果归档（零 API）   ← NEW
- 层5: compact_loop_with_summary — 循环内 LLM 摘要（触发式）      ← NEW
- 层6: enforce_budget — Token 预算兜底 + deduplicate_system_prompts
"""

from typing import Any, Dict, List, Optional

from loguru import logger


# ============================================================
# 层4: Token 预算管理 + System Prompt 去重
# ============================================================

# 中英混合约 2.5 字符/token
_CHARS_PER_TOKEN = 2.5


def estimate_tokens(messages: List[Dict[str, Any]]) -> int:
    """估算 messages 列表的总 token 数（基于字符数，偏保守）"""
    total_chars = 0
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str):
            total_chars += len(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    total_chars += len(str(part.get("text", "")))
                    total_chars += len(str(part.get("url", "")))
        # tool_calls 参数也计入
        for tc in msg.get("tool_calls", []):
            func = tc.get("function", {})
            total_chars += len(func.get("arguments", ""))
    return int(total_chars / _CHARS_PER_TOKEN)


def deduplicate_system_prompts(messages: List[Dict[str, Any]]) -> None:
    """移除工具循环中累积的重复 system prompt（原地修改）

    tool_context.build_context_prompt() 每轮 append 新的 system 消息，
    新一条包含旧一条的全部信息，旧的完全冗余。
    只保留最新一条含"已识别编码"/"已用工具"的 system 消息。
    """
    # 找到所有工具循环上下文 system 消息的索引
    ctx_indices = []
    for i, msg in enumerate(messages):
        if msg.get("role") == "system":
            content = msg.get("content", "")
            if "已识别编码" in content or "已用工具" in content or "失败工具" in content:
                ctx_indices.append(i)

    # 只保留最后一条，删除更早的
    if len(ctx_indices) > 1:
        for idx in reversed(ctx_indices[:-1]):
            messages.pop(idx)


def enforce_budget(
    messages: List[Dict[str, Any]],
    max_tokens: int,
) -> None:
    """超预算时逐步降级（原地修改）

    策略1: 去重 system prompts
    策略2: 从最旧的历史消息开始移除
    """
    # 策略1
    deduplicate_system_prompts(messages)
    if estimate_tokens(messages) <= max_tokens:
        return

    # 策略2: 移除最旧的非 system 消息（跳过 system 和最后几条）
    # 保护最后 6 条消息（当前轮的 user + assistant + tool）
    protected_tail = 6
    removable = []
    for i, msg in enumerate(messages):
        if i >= len(messages) - protected_tail:
            break
        if msg.get("role") in ("user", "assistant", "tool"):
            removable.append(i)

    # 从最旧的开始移除
    for idx in removable:
        if estimate_tokens(messages) <= max_tokens:
            break
        messages[idx]["content"] = "[已归档]"

    if estimate_tokens(messages) > max_tokens:
        logger.warning(
            f"Context still over budget after enforcement | "
            f"tokens={estimate_tokens(messages)} | max={max_tokens}"
        )


# ============================================================
# 层4: 旧轮次工具结果归档（零 API 调用）
# ============================================================


def _identify_tool_turns(
    messages: List[Dict[str, Any]],
) -> List[List[int]]:
    """识别工具循环的轮次边界，返回每轮的 tool 消息 index 列表。

    轮次边界 = role=assistant 且有 tool_calls 的消息。
    紧跟其后的连续 role=tool 消息属于同一轮。
    """
    turns: List[List[int]] = []
    i = 0
    while i < len(messages):
        msg = messages[i]
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            # 找到一轮的起点，收集后续 tool 消息
            tool_indices: List[int] = []
            j = i + 1
            while j < len(messages) and messages[j].get("role") == "tool":
                tool_indices.append(j)
                j += 1
            if tool_indices:
                turns.append(tool_indices)
            i = j
        else:
            i += 1
    return turns


def compact_stale_tool_results(
    messages: List[Dict[str, Any]],
    keep_turns: int = 2,
) -> int:
    """将旧轮次的 tool 结果替换为单行摘要（原地修改）。

    Args:
        messages: 消息列表
        keep_turns: 保留最近 N 轮的工具结果原文

    Returns:
        被压缩的 tool 消息条数
    """
    turns = _identify_tool_turns(messages)
    if len(turns) <= keep_turns:
        return 0

    stale_turns = turns[:-keep_turns]
    compacted = 0
    for tool_indices in stale_turns:
        for idx in tool_indices:
            msg = messages[idx]
            old_content = msg.get("content", "")
            if old_content.startswith("[已归档"):
                continue  # 已经压缩过
            msg["content"] = f"[已归档] 工具结果已压缩（原始 {len(old_content)} 字符）"
            compacted += 1

    if compacted:
        logger.info(
            f"Stale tool results compacted | "
            f"total_turns={len(turns)} | compacted={compacted}"
        )
    return compacted


# ============================================================
# 层5: 循环内 LLM 摘要（触发式）
# ============================================================

_LOOP_SUMMARY_PROMPT = (
    "你是工具调用记录压缩器。请将以下工具调用过程压缩为简洁摘要（最多{max_chars}字）。\n\n"
    "必须保留：\n"
    "- 已查到的关键数据（金额、数量、编码、状态）\n"
    "- 已确认的编码映射（模糊名→精确编码）\n"
    "- 失败的操作及原因\n\n"
    "可以丢弃：\n"
    "- 中间查询过程、API 参数\n"
    "- 重复的数据格式化\n"
    "- 工具截断信号\n\n"
    "直接输出摘要，不加前缀。"
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
        content = msg.get("content", "") or ""
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

    # 调用 LLM 生成摘要（复用 context_summarizer 的降级链）
    try:
        from services.context_summarizer import _call_summary_model
        from core.config import settings

        max_chars = 500
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
