"""层6: Token 预算管理 — 整体预算、工具桶预算、历史桶预算

包含：
- enforce_budget: 整体预算反向累积切点
- enforce_tool_budget: 工具结果桶（最旧的 tool 优先归档）
- enforce_history_budget / enforce_history_budget_sync: 历史消息桶（按打分淘汰）

依赖：tokens（基础工具） + archive（轮次识别 + 归档元数据提取）
"""

from typing import Any, Dict, List

from loguru import logger

from services.handlers.context_compressor.tokens import (
    _extract_text,
    _is_archived,
    _msg_tokens,
    deduplicate_system_prompts,
    estimate_tokens,
)
from services.handlers.context_compressor.archive import (
    _build_tc_name_map,
    _extract_archive_meta,
    _identify_tool_turns,
)


# ============================================================
# 整体预算：反向累积找最佳切点
# ============================================================


def _find_reverse_accumulation_cut(
    messages: List[Dict[str, Any]],
    max_tokens: int,
) -> int:
    """反向累积 token 找最佳切点（对标 Claude calculateMessagesToKeepIndex）。

    从 messages 末尾向前累积 token，返回第一条需要保留的消息 index。
    切点之前的非 system 消息全部归档。
    保证 assistant(tool_calls) 和紧跟的 tool 消息不被拆分。
    """
    if not messages:
        return 0

    # 从后往前累积
    accumulated = 0
    cut_point = len(messages)  # 默认保留全部
    for i in range(len(messages) - 1, -1, -1):
        msg_tokens = _msg_tokens(messages[i])
        if accumulated + msg_tokens > max_tokens:
            break
        accumulated += msg_tokens
        cut_point = i

    # 调整切点：保证 tool_use/tool_result 配对不拆分
    # 如果 cut_point 落在一组 tool 消息中间，向前扩展到 assistant(tool_calls)
    cut_point = _adjust_cut_for_tool_pairs(messages, cut_point)

    return cut_point


def _adjust_cut_for_tool_pairs(
    messages: List[Dict[str, Any]],
    cut_point: int,
) -> int:
    """确保 cut_point 不会拆分 assistant(tool_calls) + tool 消息组。

    如果 cut_point 的消息是 tool 角色，说明切到了一组工具调用中间，
    需要向前回退到对应的 assistant(tool_calls) 消息（包含它）。
    """
    if cut_point >= len(messages):
        return cut_point

    # 如果切点是 tool 消息，向前找对应的 assistant(tool_calls)
    if messages[cut_point].get("role") == "tool":
        for j in range(cut_point - 1, -1, -1):
            msg = messages[j]
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                return j
            if msg.get("role") != "tool":
                # 没找到匹配的 assistant，保持原切点
                break

    return cut_point


def enforce_budget(
    messages: List[Dict[str, Any]],
    max_tokens: int,
) -> None:
    """超预算时逐步降级（原地修改）

    策略1: 去重 system prompts
    策略2: 反向累积 token 找最佳切点，切点之前的消息归档
           （对标 Claude reverse token accumulation）
    """
    # 策略1
    deduplicate_system_prompts(messages)
    if estimate_tokens(messages) <= max_tokens:
        return

    # 策略2: 反向累积找切点
    cut_point = _find_reverse_accumulation_cut(messages, max_tokens)

    # 切点之前的非 system 消息归档
    compacted = 0
    for i in range(cut_point):
        msg = messages[i]
        if msg.get("role") == "system":
            continue  # system 消息始终保留
        if _is_archived(msg):
            continue
        messages[i]["content"] = "[已归档]"
        compacted += 1

    if compacted:
        logger.info(
            f"Budget enforced (reverse accumulation) | "
            f"cut_point={cut_point}/{len(messages)} | "
            f"compacted={compacted} | "
            f"tokens_after={estimate_tokens(messages)} | max={max_tokens}"
        )

    if estimate_tokens(messages) > max_tokens:
        logger.warning(
            f"Context still over budget after enforcement | "
            f"tokens={estimate_tokens(messages)} | max={max_tokens}"
        )


# ============================================================
# 工具桶预算
# ============================================================


def enforce_tool_budget(
    messages: List[Dict[str, Any]],
    max_tokens: int,
) -> None:
    """工具结果桶：超预算时从最旧的 tool 消息开始归档（原地修改）

    对标 Claude Code L3 Microcompact：旧工具结果替换为占位符。
    保护最近 2 轮的 tool 结果不被压缩。
    """
    turns = _identify_tool_turns(messages)
    if not turns:
        return

    # 计算 tool 消息总 token（排除已归档的）
    all_tool_indices: set = set()
    for turn in turns:
        all_tool_indices.update(turn)
    tool_tokens = sum(
        _msg_tokens(messages[i]) for i in all_tool_indices
        if not _is_archived(messages[i])
    )

    if tool_tokens <= max_tokens:
        return

    # 保护最近 2 轮
    protected: set = set()
    for turn in turns[-2:]:
        protected.update(turn)

    tc_id_to_name = _build_tc_name_map(messages)

    # 从最旧的开始归档（短结果也归档——token 预算已超限，必须压缩）
    compacted = 0
    for turn in turns[:-2]:
        if tool_tokens <= max_tokens:
            break
        for idx in turn:
            if tool_tokens <= max_tokens:
                break
            if _is_archived(messages[idx]):
                continue
            old = messages[idx].get("content", "")
            saved = _msg_tokens(messages[idx])
            tool_name = tc_id_to_name.get(messages[idx].get("tool_call_id", ""), "")
            messages[idx]["content"] = _extract_archive_meta(old, tool_name)
            tool_tokens -= max(0, saved - _msg_tokens(messages[idx]))
            compacted += 1

    if compacted:
        logger.info(
            f"Tool budget enforced | compacted={compacted} | "
            f"remaining_tokens={tool_tokens} | max={max_tokens}"
        )


# ============================================================
# 历史桶预算：按打分淘汰
# ============================================================


def _enforce_history_budget_core(
    messages: List[Dict[str, Any]],
    max_tokens: int,
    scores: List[float],
    hist_indices: List[int],
) -> None:
    """历史桶核心逻辑：按分数排序淘汰（内部函数）"""
    hist_tokens = sum(
        _msg_tokens(messages[i]) for i in hist_indices
        if not _is_archived(messages[i])
    )
    if hist_tokens <= max_tokens:
        return

    # 保护最后 4 条（当前轮 user+assistant + 上一轮）
    protected_tail = 4
    scoreable_indices = hist_indices[:-protected_tail] if len(hist_indices) > protected_tail else []
    scoreable_scores = scores[:len(scoreable_indices)]
    if not scoreable_indices:
        return

    ranked = sorted(
        zip(scoreable_indices, scoreable_scores),
        key=lambda x: x[1],  # 低分先删
    )

    compacted = 0
    for idx, _score in ranked:
        if hist_tokens <= max_tokens:
            break
        saved = _msg_tokens(messages[idx])
        messages[idx]["content"] = "[已归档]"
        hist_tokens -= saved
        compacted += 1

    if compacted:
        logger.info(
            f"History budget enforced | compacted={compacted} | "
            f"remaining_tokens={hist_tokens} | max={max_tokens}"
        )


def enforce_history_budget_sync(
    messages: List[Dict[str, Any]],
    max_tokens: int,
) -> None:
    """同步版（工具循环内部用）：只用 A 层规则打分"""
    from services.handlers.message_scorer import score_messages_sync

    hist = [
        (i, msg) for i, msg in enumerate(messages)
        if msg.get("role") in ("user", "assistant")
        and not _is_archived(msg)
    ]
    if not hist:
        return
    indices = [i for i, _ in hist]
    scores = score_messages_sync([msg for _, msg in hist])
    _enforce_history_budget_core(messages, max_tokens, scores, indices)


async def enforce_history_budget(
    messages: List[Dict[str, Any]],
    max_tokens: int,
    current_query: str = "",
) -> None:
    """异步版（_build_llm_messages 用）：A 层规则 + B 层 Embedding"""
    from services.handlers.message_scorer import score_messages

    hist = [
        (i, msg) for i, msg in enumerate(messages)
        if msg.get("role") in ("user", "assistant")
        and not _is_archived(msg)
    ]
    if not hist:
        return
    indices = [i for i, _ in hist]
    scores = await score_messages([msg for _, msg in hist], current_query=current_query)
    _enforce_history_budget_core(messages, max_tokens, scores, indices)
