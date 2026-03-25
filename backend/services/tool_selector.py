"""
工具智能筛选器 — 三级匹配 + action 筛选

替换 Phase 2 的全量工具加载，按用户输入动态筛选：
- Level 1：同义词表扩展（~0.1ms）
- Level 2：tags/action 子串匹配（~1ms）
- Level 3：qwen-turbo 语义匹配（~200ms，仅 L1+L2 命中 < 3 时触发）

设计文档: docs/document/TECH_工具系统统一架构方案.md §十二
"""

import copy
import json
from typing import Any, Dict, List, Optional, Set, Tuple

from loguru import logger

from config.tool_registry import (
    TOOL_REGISTRY,
    ToolEntry,
    expand_synonyms,
    get_domain_tools,
)


# ============================================================
# Level 2: 工具筛选（子串匹配 tags）
# ============================================================


def _match_tool_tags(
    tool: ToolEntry, user_input: str, synonym_words: Set[str],
) -> int:
    """计算工具 tags 与用户输入的匹配分数

    tag 是多字词（如"库存"、"订单"），用子串匹配用户输入是安全的。
    同义词用集合精确匹配（不用子串），避免"统计" in "统计表" 之类的误命中。
    """
    return sum(
        1 for tag in tool.tags
        if tag in user_input or tag in synonym_words
    )


def select_tools(
    domain: str, user_input: str, top_k: int = 8,
) -> Tuple[List[ToolEntry], Set[str]]:
    """Level 1+2 工具筛选

    Returns:
        (selected_tools, match_words) — 筛选结果 + 匹配词集合
    """
    synonym_words = expand_synonyms(user_input)
    match_words = {user_input} | synonym_words

    all_tools = get_domain_tools(domain)
    non_always = [t for t in all_tools if not t.always_include]
    always = [t for t in all_tools if t.always_include]

    # Level 2: 子串匹配 + priority 排序
    scored: List[Tuple[int, int, ToolEntry]] = []
    for tool in non_always:
        hits = _match_tool_tags(tool, user_input, synonym_words)
        scored.append((tool.priority, -hits, tool))
    scored.sort(key=lambda x: (x[0], x[1]))

    selected = [t for _, _, t in scored[:top_k]]

    # 有命中的远程工具不受 top_k 限制（防止被本地工具挤掉）
    selected_names = {t.name for t in selected}
    for priority, neg_hits, tool in scored[top_k:]:
        if neg_hits < 0 and tool.name not in selected_names:
            selected.append(tool)

    # 常驻工具追加
    for tool in always:
        if tool not in selected:
            selected.append(tool)

    matched_count = sum(1 for p, h, _ in scored[:top_k] if h < 0)
    return selected, match_words


# ============================================================
# Level 3: qwen-turbo 语义匹配（兜底）
# ============================================================


async def _semantic_tool_match(
    user_input: str, candidate_tools: List[ToolEntry],
) -> List[str]:
    """Level 3: 用 qwen-turbo 从候选列表中选工具"""
    try:
        from core.config import get_settings
        import httpx

        settings = get_settings()
        tool_list = "\n".join(
            f"- {t.name}: {t.description}" for t in candidate_tools
        )
        prompt = (
            "从以下工具列表中选出与用户问题最相关的工具"
            "（只返回工具名，逗号分隔，最多5个）：\n\n"
            f"用户：{user_input}\n\n工具列表：\n{tool_list}"
        )

        base_url = settings.dashscope_base_url
        api_key = settings.dashscope_api_key

        async with httpx.AsyncClient(
            base_url=base_url, timeout=3.0,
        ) as client:
            resp = await client.post(
                "/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": "qwen-turbo",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 100,
                },
            )
            data = resp.json()
            text = data["choices"][0]["message"]["content"]
            return [n.strip() for n in text.split(",") if n.strip()]
    except Exception as e:
        logger.debug(f"Level 3 semantic match failed | error={e}")
        return []


# ============================================================
# Action 筛选（子串匹配 description）
# ============================================================


def _score_actions(
    tool_name: str, user_input: str, match_words: Set[str],
    max_actions: int = 8,
) -> Optional[List[str]]:
    """筛选工具内的 action，返回匹配的 action 名列表

    Returns:
        None — 工具无 action（本地工具等）
        List[str] — 筛选后的 action 名列表
    """
    from services.kuaimai.registry import TOOL_REGISTRIES

    registry = TOOL_REGISTRIES.get(tool_name)
    if not registry:
        return None

    scored: Dict[str, int] = {}
    for action_name, entry in registry.items():
        if entry.is_write:
            continue
        score = 0
        for kw in match_words:
            if kw in action_name:
                score += 3
            elif kw in entry.description:
                score += 2
        scored[action_name] = score

    # 有命中的按分数排
    hit = [k for k, v in sorted(scored.items(), key=lambda x: -x[1]) if v > 0]

    # 命中太少时兜底补充
    if len(hit) < 3:
        remaining = [k for k in scored if k not in hit][:3 - len(hit)]
        hit.extend(remaining)

    return hit[:max_actions]


def _filter_tool_schema_actions(
    schema: Dict[str, Any],
    allowed_actions: List[str],
) -> Dict[str, Any]:
    """深拷贝 tool schema 并过滤 action enum + description"""
    schema = copy.deepcopy(schema)
    props = schema.get("function", {}).get("parameters", {}).get("properties", {})
    action_prop = props.get("action")
    if not action_prop or "enum" not in action_prop:
        return schema

    old_enum = action_prop["enum"]
    new_enum = [a for a in old_enum if a in allowed_actions]
    if not new_enum:
        return schema  # 保底不过滤

    action_prop["enum"] = new_enum

    # 精简 description（只保留匹配的 action 描述）
    old_desc = action_prop.get("description", "")
    if old_desc:
        parts = []
        for segment in old_desc.split(", "):
            action_name = segment.split("=")[0].strip()
            if action_name in new_enum:
                parts.append(segment)
        if parts:
            action_prop["description"] = ", ".join(parts)

    return schema


# ============================================================
# 主入口：筛选并构建 Phase 2 工具列表
# ============================================================


async def select_and_filter_tools(
    domain: str,
    user_input: str,
    all_tool_schemas: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """三级匹配 + action 筛选，返回过滤后的 OpenAI tool schemas

    Args:
        domain: 域名（erp/crawler）
        user_input: 用户输入文本
        all_tool_schemas: build_domain_tools() 返回的全量工具 schema

    Returns:
        过滤后的工具 schema 列表
    """
    if not user_input.strip():
        return all_tool_schemas  # 无输入 → 全量降级

    # Level 1+2: 同义词扩展 + tags 子串匹配
    selected_entries, match_words = select_tools(domain, user_input)
    selected_names = {e.name for e in selected_entries}

    # 计算 L1+L2 命中数（排除 always_include）
    matched_non_always = sum(
        1 for e in selected_entries
        if not e.always_include and _match_tool_tags(e, user_input, match_words - {user_input}) > 0
    )

    # Level 3: qwen-turbo 语义补充（命中不足时触发）
    if matched_non_always < 3:
        logger.info(
            f"Level 3 triggered | matched={matched_non_always} | input={user_input[:50]}"
        )
        non_always = [
            t for t in get_domain_tools(domain) if not t.always_include
        ]
        semantic_names = await _semantic_tool_match(user_input, non_always)
        for name in semantic_names:
            if name in TOOL_REGISTRY and name not in selected_names:
                selected_entries.append(TOOL_REGISTRY[name])
                selected_names.add(name)

    # 补充 action 筛选用的关键词：把命中的 tags 加入 match_words
    # 解决：match_words 含完整用户输入（如"库存多少"）无法匹配 description
    # 的问题，tags（如"库存"）能精准匹配 description 子串
    action_match_words = set(match_words)
    synonym_words = match_words - {user_input}
    for entry in selected_entries:
        for tag in entry.tags:
            if tag in user_input or tag in synonym_words:
                action_match_words.add(tag)

    # 从全量 schemas 中筛选 + action 过滤
    result: List[Dict[str, Any]] = []
    schema_map = {
        s["function"]["name"]: s for s in all_tool_schemas
    }

    for entry in selected_entries:
        schema = schema_map.get(entry.name)
        if not schema:
            continue

        # 有 action 的工具做 action 筛选
        if entry.has_actions:
            allowed = _score_actions(
                entry.name, user_input, action_match_words,
            )
            if allowed:
                schema = _filter_tool_schema_actions(schema, allowed)

        result.append(schema)

    logger.info(
        f"Tool selector | domain={domain} | input={user_input[:50]} "
        f"| tools={len(result)}/{len(all_tool_schemas)} "
        f"| selected={[e.name for e in selected_entries if not e.always_include]}"
    )

    return result if result else all_tool_schemas  # 全空时降级
