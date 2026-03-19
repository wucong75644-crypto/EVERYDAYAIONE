"""
模型选择器 — 规则匹配模型

根据 Phase 1 分类结果（domain + signals）选择最佳模型。
匹配优先级：品牌命中 → 硬约束过滤 → 能力打分 → priority 排序。
新增模型只改 JSON，新增能力维度只加一行映射。
"""

from typing import Any, Dict, List, Optional

from loguru import logger

from config.smart_model_config import (
    DEFAULT_CHAT_MODEL,
    DEFAULT_IMAGE_MODEL,
    DEFAULT_VIDEO_MODEL,
    SMART_CONFIG,
    get_model_keywords,
)

# Phase 1 信号 → capabilities 能力标签映射
SIGNAL_TO_CAPABILITY: Dict[str, str] = {
    "needs_code": "code",
    "needs_reasoning": "reasoning",
    "needs_math": "math",
}


def select_model(
    domain: str,
    signals: Dict[str, Any],
    has_image: bool = False,
    thinking_mode: Optional[str] = None,
) -> str:
    """信号 → 标签匹配 → 选模型

    Args:
        domain: Phase 1 分类域（chat/erp/crawler/image/video）
        signals: Phase 1 提取的信号
        has_image: 用户消息是否包含图片
        thinking_mode: 用户要求的思考模式

    Returns:
        选中的 model_id
    """
    if domain == "image":
        return _select_image_model(signals)
    if domain == "video":
        return _select_video_model(signals)
    # chat/erp/crawler → 从 chat 模型列表选
    return _select_chat_model(signals, has_image, thinking_mode)


def _select_image_model(signals: Dict[str, Any]) -> str:
    """Image domain（needs_edit → 编辑模型，needs_hd → 高清模型）"""
    image_models = SMART_CONFIG.get("image", {}).get("models", [])
    if not image_models:
        return DEFAULT_IMAGE_MODEL

    if signals.get("needs_edit"):
        candidates = [m for m in image_models if m.get("requires_image")]
        if candidates:
            return candidates[0]["id"]

    if signals.get("needs_hd"):
        candidates = [
            m for m in image_models if m["id"] != DEFAULT_IMAGE_MODEL
        ]
        if candidates:
            return candidates[0]["id"]

    return DEFAULT_IMAGE_MODEL


def _select_video_model(signals: Dict[str, Any]) -> str:
    """Video domain（needs_pro → 专业级模型）"""
    video_models = SMART_CONFIG.get("video", {}).get("models", [])
    if not video_models:
        return DEFAULT_VIDEO_MODEL

    if signals.get("needs_pro"):
        pro_candidates = [
            m for m in video_models if not m.get("requires_image")
        ]
        if pro_candidates:
            pro_candidates.sort(
                key=lambda m: m.get("priority", 0), reverse=True,
            )
            return pro_candidates[0]["id"]

    return DEFAULT_VIDEO_MODEL


def _select_chat_model(
    signals: Dict[str, Any],
    has_image: bool,
    thinking_mode: Optional[str],
) -> str:
    """Chat/ERP/Crawler domain 模型选择

    匹配优先级：
    1. 品牌命中（keywords 精确匹配，尊重用户品牌偏好）
    2. 硬约束过滤（has_image/needs_search/thinking_mode）
    3. 能力打分（capabilities 交集最大）
    4. priority 排序（无特殊需求时选默认首选）
    """
    chat_models: List[Dict[str, Any]] = SMART_CONFIG.get(
        "chat", {},
    ).get("models", [])
    if not chat_models:
        return DEFAULT_CHAT_MODEL

    # 1. 品牌命中（O(1) 查找）
    brand_hint = (signals.get("brand_hint") or "").lower().strip()
    if brand_hint:
        keyword_map = get_model_keywords("chat")
        matched = keyword_map.get(brand_hint)
        if matched:
            logger.debug(f"Brand match | hint={brand_hint} → {matched}")
            return matched

    # 2. 硬约束过滤
    candidates = list(chat_models)

    if has_image:
        candidates = [
            m for m in candidates if m.get("supports_image", True)
        ]
    if signals.get("needs_search"):
        candidates = [
            m for m in candidates if m.get("supports_search", False)
        ]
    if thinking_mode == "deep":
        candidates = [
            m for m in candidates if m.get("supports_thinking", False)
        ]

    if not candidates:
        logger.warning(
            "No models after hard constraints, fallback to default",
        )
        return DEFAULT_CHAT_MODEL

    # 3. 能力打分
    required_caps: set = set()
    for signal_key, cap_tag in SIGNAL_TO_CAPABILITY.items():
        if signals.get(signal_key):
            required_caps.add(cap_tag)

    if required_caps:
        scored = []
        for m in candidates:
            model_caps = set(m.get("capabilities", []))
            score = len(model_caps & required_caps)
            scored.append((score, m.get("priority", 99), m))
        # 高分优先，同分按 priority（数字小 = 优先）
        scored.sort(key=lambda x: (-x[0], x[1]))
        return scored[0][2]["id"]

    # 4. priority 排序
    candidates.sort(key=lambda m: m.get("priority", 99))
    return candidates[0]["id"]
