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

import re
from typing import Any, Dict, List, Optional

from loguru import logger

# staging 路径正则（与 tool_digest.py 保持一致）
_STAGING_PATH_RE = re.compile(
    r'STAGING_DIR\s*\+\s*"/(tool_result_[^"]+\.txt)"'
)


# ============================================================
# 工具函数
# ============================================================


def _extract_text(content: Any) -> str:
    """从 message content 提取纯文本（兼容 str 和 list[dict] 两种格式）。

    AgentResult.to_message_content() 返回 list[dict]，压缩器各环节
    需要统一用此函数提取文本，避免对 list 做字符串操作导致 TypeError。
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return str(content) if content else ""


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
# 层6-B: 工具结果分桶预算控制（Phase 2）
# 设计文档：docs/document/TECH_上下文工程重构.md §五
# ============================================================


def _is_archived(msg: Dict[str, Any]) -> bool:
    """检查消息是否已被归档（兼容 str / list[dict] 两种 content 格式）"""
    text = _extract_text(msg.get("content", ""))
    return text.startswith("[已归档")


def _msg_tokens(msg: Dict[str, Any]) -> int:
    """单条消息的 token 估算"""
    return estimate_tokens([msg])


def _build_tc_name_map(messages: List[Dict[str, Any]]) -> Dict[str, str]:
    """从 messages 中构建 tool_call_id → tool_name 映射。

    层4（compact_stale_tool_results）和层6（enforce_tool_budget）共用。
    """
    tc_map: Dict[str, str] = {}
    for msg in messages:
        if msg.get("role") == "assistant":
            for tc in msg.get("tool_calls", []):
                tc_id = tc.get("id", "")
                name = tc.get("function", {}).get("name", "")
                if tc_id and name:
                    tc_map[tc_id] = name
    return tc_map


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
# 层6-C: 历史消息分桶预算控制（Phase 2 + Phase 3 打分）
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
    """将旧轮次的 tool 结果智能归档（原地修改）。

    短结果（≤2000 字符）：不压缩（数字/时间/汇总都是关键信息）。
    大结果（>2000 字符）：保留元数据（staging 路径、字段名、记录数），压缩数据行。

    Args:
        messages: 消息列表
        keep_turns: 保留最近 N 轮的工具结果原文（安全网兜底）

    Returns:
        被压缩的 tool 消息条数
    """
    turns = _identify_tool_turns(messages)
    if len(turns) <= keep_turns:
        return 0

    tc_id_to_name = _build_tc_name_map(messages)

    stale_turns = turns[:-keep_turns]
    compacted = 0
    for tool_indices in stale_turns:
        for idx in tool_indices:
            msg = messages[idx]
            old_content = msg.get("content", "")
            old_text = _extract_text(old_content)
            if old_text.startswith("[已归档"):
                continue  # 已经压缩过

            # 短结果不压缩：压缩收益低，但可能丢失关键汇总数字/时间范围
            if len(old_text) <= 2000:
                continue

            # 大结果：保留元数据，压缩数据行
            tool_name = tc_id_to_name.get(msg.get("tool_call_id", ""), "")
            meta = _extract_archive_meta(old_text, tool_name)
            msg["content"] = meta
            compacted += 1

    if compacted:
        logger.info(
            f"Stale tool results compacted | "
            f"total_turns={len(turns)} | compacted={compacted}"
        )
    return compacted


def _extract_archive_meta(content: str, tool_name: str = "") -> str:
    """从大结果中提取元数据，生成归档文本。

    容错设计：提取失败时降级为"前 200 字符摘要"，不会 crash。
    """

    # 尝试从 <persisted-output> 提取 staging 信息
    staged_path = None
    original_size = len(content)
    fields_line = ""

    try:
        # 提取 staging 文件路径
        path_match = _STAGING_PATH_RE.search(content)
        if path_match:
            staged_path = path_match.group(1)

        # 提取原始大小
        size_match = re.search(r'Output too large \((\d+) chars\)', content)
        if size_match:
            original_size = int(size_match.group(1))

        # 从文件名提取工具名（兜底）
        if not tool_name and staged_path:
            name_match = re.match(r'tool_result_(.+?)_[a-f0-9]+\.txt', staged_path)
            if name_match:
                tool_name = name_match.group(1)

        # 提取字段名（preview 首行通常是列标题）
        preview_match = re.search(r'Preview.*?:\n(.+)', content)
        if preview_match:
            first_line = preview_match.group(1).strip()
            # 检测是否是列标题行（含 | 分隔符或 tab）
            if '|' in first_line or '\t' in first_line:
                cols = [c.strip() for c in re.split(r'[|\t]', first_line) if c.strip()]
                if cols:
                    fields_line = f"字段: {', '.join(cols[:8])}"

        # 提取记录数
        count_match = re.search(r'共\s*(\d[\d,]*)\s*(?:条|行|件|项|笔)', content)
        if count_match and fields_line:
            fields_line += f" | 共 {count_match.group(1)} 条记录"
        elif count_match:
            fields_line = f"共 {count_match.group(1)} 条记录"

    except Exception as e:
        logger.debug(f"Archive meta extraction failed | error={e}")
        # 最终降级：极少触发（>2000字符且无 <persisted-output> 的场景几乎不存在）
        label = tool_name or "工具"
        return f"[已归档] {label} 结果（原始 {len(content)} 字符）\n{content[:200]}..."

    # 组装归档文本
    label = tool_name or "工具"
    lines = [f"[已归档] {label} 查询结果（原始 {original_size} 字符）"]
    if staged_path:
        lines.append(f'数据文件: STAGING_DIR + "/{staged_path}"')
    if fields_line:
        lines.append(fields_line)

    # 如果什么元数据都没提取到
    if not staged_path and not fields_line:
        # 最终降级：极少触发（>2000字符且无 <persisted-output> 的场景几乎不存在）
        lines.append(content[:200] + "...")

    return "\n".join(lines)


# ============================================================
# 层5: 循环内 LLM 摘要（触发式）
# ============================================================

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
