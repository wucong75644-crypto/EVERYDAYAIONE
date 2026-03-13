"""
模型搜索服务

提供按需发现 AI 模型及其能力的搜索。
支持三种搜索模式：
- 精确查询：模型 ID（如 deepseek-v3.2）
- 能力搜索：按能力标签筛选（如 "code" "reasoning"）
- 场景搜索：自然语言匹配（如 "写代码" "数学题"）

数据源：SMART_CONFIG（smart_models.json）。
"""

from typing import Any, Dict, List, Tuple

from config.smart_model_config import SMART_CONFIG

# 搜索结果最大条数
_MAX_RESULTS = 5

# 场景关键词 → 能力标签映射（中文场景搜索用）
_SCENARIO_MAP = {
    "代码": "code", "编程": "code", "写代码": "code", "程序": "code",
    "数学": "math", "计算": "math", "解题": "math",
    "推理": "reasoning", "逻辑": "logic", "分析": "analysis",
    "写作": "writing", "翻译": "translation", "创作": "writing",
    "对话": "chat", "聊天": "chat",
    "图片": "supports_image", "看图": "supports_image",
    "搜索": "supports_search", "联网": "supports_search",
    "长文": "long_context", "长文本": "long_context",
    "中文": "chinese",
}


def search_models(query: str) -> str:
    """搜索可用 AI 模型及其能力

    Args:
        query: 模型名、能力标签或场景描述

    Returns:
        匹配的模型列表及能力说明
    """
    query = query.strip()
    if not query:
        return "请输入搜索关键词（如模型名、能力或场景）"

    # 精确查询：检查是否匹配模型 ID
    exact = _exact_search(query)
    if exact:
        return exact

    # 能力/场景搜索
    return _capability_search(query)


def _exact_search(query: str) -> str | None:
    """精确查询：按模型 ID 匹配"""
    query_lower = query.lower()
    for category in ("chat", "image", "video", "web_search"):
        for m in SMART_CONFIG.get(category, {}).get("models", []):
            if m["id"].lower() == query_lower:
                return _format_model_detail(m, category)
    return None


def _capability_search(query: str) -> str:
    """能力/场景搜索"""
    keywords = query.lower().split()
    matches: List[Tuple[int, Dict[str, Any], str]] = []

    for category in ("chat", "image", "video", "web_search"):
        for m in SMART_CONFIG.get(category, {}).get("models", []):
            score = _calc_score(keywords, m, category)
            if score > 0:
                matches.append((score, m, category))

    if not matches:
        return f"未找到与「{query}」匹配的模型，请尝试其他关键词"

    matches.sort(key=lambda x: x[0], reverse=True)
    top = matches[:_MAX_RESULTS]

    lines = [f"找到 {len(matches)} 个匹配，显示前 {len(top)} 个：\n"]
    for _, m, cat in top:
        lines.append(_format_model_brief(m, cat))
    return "\n".join(lines)


def _calc_score(
    keywords: List[str], model: Dict[str, Any], category: str,
) -> int:
    """计算匹配分数"""
    score = 0
    caps = model.get("capabilities", [])
    desc = model.get("description", "").lower()
    model_id = model["id"].lower()

    for kw in keywords:
        # 场景映射
        mapped = _SCENARIO_MAP.get(kw)
        if mapped:
            if mapped == "supports_image":
                if model.get("supports_image"):
                    score += 3
            elif mapped == "supports_search":
                if model.get("supports_search"):
                    score += 3
            elif mapped in caps:
                score += 3
            continue

        # 直接能力标签匹配
        if kw in caps:
            score += 3
        # 模型 ID 匹配
        elif kw in model_id:
            score += 2
        # 描述匹配
        elif kw in desc:
            score += 1
    return score


def _format_model_detail(model: Dict[str, Any], category: str) -> str:
    """格式化单个模型的完整信息"""
    lines = [
        f"📋 {model['id']}（{category}）",
        f"描述: {model['description']}",
        f"优先级: {model.get('priority', '-')}",
    ]

    caps = model.get("capabilities", [])
    if caps:
        lines.append(f"能力: {', '.join(caps)}")

    img = model.get("supports_image")
    if img is not None:
        lines.append(f"图片理解: {'✓' if img else '✗'}")

    search = model.get("supports_search")
    if search is not None:
        lines.append(f"联网搜索: {'✓' if search else '✗'}")

    if model.get("requires_image"):
        lines.append("需要上传图片: ✓")

    return "\n".join(lines)


def _format_model_brief(model: Dict[str, Any], category: str) -> str:
    """格式化模型简要信息"""
    caps = model.get("capabilities", [])
    tags = []
    if caps:
        tags.append(",".join(caps))
    if model.get("supports_image"):
        tags.append("图片:✓")
    if model.get("supports_search"):
        tags.append("搜索:✓")
    tag_str = f" [{' | '.join(tags)}]" if tags else ""
    return f"- {model['id']}（{category}）— {model['description']}{tag_str}"
