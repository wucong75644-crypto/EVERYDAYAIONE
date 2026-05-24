"""层4: 旧轮次工具结果归档（零 API 调用）

包含两种归档策略：
- compact_stale_tool_results: 按 assistant+tool_calls 算轮次（企微链路用）
- compact_stale_by_user_turns:  按 user 消息算轮次 + 容量触发（Web 链路用）

设计文档：docs/document/TECH_Web端上下文压缩改造.md
"""

import re
from typing import Any, Dict, List, Tuple

from loguru import logger

from services.handlers.context_compressor.tokens import (
    _extract_text,
    estimate_tokens,
)


# staging 路径正则（匹配 STAGING_DIR + '/xxx.parquet' 或 "/xxx.txt" 等任意格式）
_STAGING_PATH_RE = re.compile(
    r"STAGING_DIR\s*\+\s*['\"]/?([^'\"]+)['\"]"
)


# ============================================================
# 轮次识别
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


def _identify_user_turns(
    messages: List[Dict[str, Any]],
) -> List[Tuple[int, int]]:
    """按 role=user 切分用户对话回合（Web 端压缩用）。

    与 _identify_tool_turns 区别：
    - _identify_tool_turns：每个 assistant+tool_calls 算一轮（工具调用粒度）
    - _identify_user_turns：每个 user 消息开始一轮（用户对话粒度）

    Returns:
        List[(start_idx, end_idx)]：每一项是 [起点, 终点) 半开区间
        起点 = role=user 的消息 index
        终点 = 下一条 user 的 index（或 len(messages)）
        没有 user 消息时返回空列表。
    """
    user_indices: List[int] = [
        i for i, msg in enumerate(messages)
        if msg.get("role") == "user"
    ]
    if not user_indices:
        return []

    turns: List[Tuple[int, int]] = []
    for i, start in enumerate(user_indices):
        end = user_indices[i + 1] if i + 1 < len(user_indices) else len(messages)
        turns.append((start, end))
    return turns


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


# ============================================================
# 归档压缩
# ============================================================


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


def compact_stale_by_user_turns(
    messages: List[Dict[str, Any]],
    keep_user_turns: int = 10,
    capacity_trigger: float = 0.7,
    max_tokens: int = 200000,
) -> int:
    """Web 端工具结果归档：按用户对话回合 + 容量触发（原地修改）。

    与 compact_stale_tool_results 的差异：
    1. 轮次按 user 消息切分（不是 assistant+tool_calls）
    2. 容量未到 capacity_trigger 时直接 return 0（不动 messages）
    3. 复用现有 _extract_archive_meta 压缩逻辑，输出格式与企微一致

    Args:
        messages: 消息列表
        keep_user_turns: 保留最近 N 次用户对话回合的工具结果原文
        capacity_trigger: 上下文使用率触发阈值（0~1），低于此值不压缩
        max_tokens: 真实模型容量基数（用于阈值计算）

    Returns:
        被压缩的 tool 消息条数（容量未到或轮次不足时为 0）
    """
    # 1. 容量检查：未到阈值直接放行
    current = estimate_tokens(messages)
    threshold = int(max_tokens * capacity_trigger)
    if current < threshold:
        return 0

    # 2. 按用户对话切分
    user_turns = _identify_user_turns(messages)
    if len(user_turns) <= keep_user_turns:
        return 0

    # 3. 收集旧轮次的所有 tool 消息 index
    stale_turns = user_turns[:-keep_user_turns]
    tc_id_to_name = _build_tc_name_map(messages)

    compacted = 0
    for start, end in stale_turns:
        for idx in range(start, end):
            msg = messages[idx]
            if msg.get("role") != "tool":
                continue

            old_content = msg.get("content", "")
            old_text = _extract_text(old_content)
            if old_text.startswith("[已归档"):
                continue  # 已经压缩过
            if len(old_text) <= 2000:
                continue  # 短结果不压缩

            # 大结果：保留元数据，压缩数据行
            tool_name = tc_id_to_name.get(msg.get("tool_call_id", ""), "")
            meta = _extract_archive_meta(old_text, tool_name)
            msg["content"] = meta
            compacted += 1

    if compacted:
        logger.info(
            f"Web stale tool results compacted | "
            f"user_turns={len(user_turns)} | compacted={compacted} | "
            f"tokens_before={current} | tokens_after={estimate_tokens(messages)} | "
            f"threshold={threshold}"
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
        lines.append(f"数据文件: STAGING_DIR + '/{staged_path}'")
    if fields_line:
        lines.append(fields_line)

    # 如果什么元数据都没提取到
    if not staged_path and not fields_line:
        # 最终降级：极少触发（>2000字符且无 <persisted-output> 的场景几乎不存在）
        lines.append(content[:200] + "...")

    return "\n".join(lines)
