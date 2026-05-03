"""
智能模型配置

从 smart_models.json 加载模型配置，动态生成：
- ROUTER_TOOLS（千问 Function Calling 工具定义）
- 模型映射表（model_id → GenerationType）
- 重试工具（过滤已失败模型）
"""

import copy
import json
import os
from typing import Any, Dict, List, Optional

from loguru import logger

from schemas.message import GenerationType


# ============================================================
# 配置加载
# ============================================================

_CONFIG_PATH = os.path.join(
    os.path.dirname(__file__), "smart_models.json"
)

# 工具名 → GenerationType 映射
TOOL_TO_TYPE: Dict[str, GenerationType] = {
    # Agent Loop 新路由工具
    "route_to_image": GenerationType.IMAGE,
    "route_to_video": GenerationType.VIDEO,
    "route_to_chat": GenerationType.CHAT,
    # IntentRouter 旧工具名（向后兼容）
    "generate_image": GenerationType.IMAGE,
    "generate_video": GenerationType.VIDEO,
    "web_search": GenerationType.CHAT,
    "text_chat": GenerationType.CHAT,
}


def _load_config() -> Dict[str, Any]:
    """加载模型配置（启动时一次性加载）"""
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load smart_models.json | error={e}")
        return {}


def _build_mappings(config: Dict[str, Any]) -> tuple:
    """从 JSON 配置构建模型映射表"""
    model_to_gen_type: Dict[str, GenerationType] = {}
    auto_defaults: Dict[GenerationType, str] = {}

    type_map = {
        "chat": GenerationType.CHAT,
        "image": GenerationType.IMAGE,
        "video": GenerationType.VIDEO,
        "web_search": GenerationType.CHAT,
    }

    for category, gen_type in type_map.items():
        cat_config = config.get(category, {})
        for m in cat_config.get("models", []):
            model_to_gen_type[m["id"]] = gen_type
        default = cat_config.get("default")
        if default and gen_type not in auto_defaults:
            auto_defaults[gen_type] = default

    return model_to_gen_type, auto_defaults


# 模块级变量（启动时初始化）
SMART_CONFIG = _load_config()
MODEL_TO_GEN_TYPE, AUTO_MODEL_DEFAULTS = _build_mappings(SMART_CONFIG)

# 具名默认模型常量（供外部引用，数据源 = smart_models.json.default）
DEFAULT_CHAT_MODEL = AUTO_MODEL_DEFAULTS.get(GenerationType.CHAT, "gemini-3-pro")
DEFAULT_IMAGE_MODEL = AUTO_MODEL_DEFAULTS.get(GenerationType.IMAGE, "google/nano-banana")
DEFAULT_VIDEO_MODEL = AUTO_MODEL_DEFAULTS.get(GenerationType.VIDEO, "sora-2-text-to-video")


def get_image_to_video_model() -> str:
    """获取图生视频模型（requires_image=true 的视频模型）"""
    for m in SMART_CONFIG.get("video", {}).get("models", []):
        if m.get("requires_image"):
            return m["id"]
    return "sora-2-image-to-video"


def get_model_keywords(category: str = "chat") -> Dict[str, str]:
    """获取品牌关键词 → model_id 映射（按 priority 排序，先注册优先）

    用于 model_selector 品牌命中（O(1) 查找）和 Phase 1 提示词动态生成。

    Returns:
        {"gpt": "openai/gpt-4.1", "claude": "anthropic/claude-sonnet-4", ...}
    """
    mapping: Dict[str, str] = {}
    models = SMART_CONFIG.get(category, {}).get("models", [])
    for m in models:
        for kw in m.get("keywords", []):
            kw_lower = kw.lower()
            if kw_lower not in mapping:
                mapping[kw_lower] = m["id"]
    return mapping


def _find_model_config(model_id: str) -> Optional[Dict[str, Any]]:
    """在 chat 模型列表中查找指定模型配置"""
    for m in SMART_CONFIG.get("chat", {}).get("models", []):
        if m["id"] == model_id:
            return m
    return None


def _get_models_with_capability(
    capability: str, value: bool = True,
) -> List[str]:
    """获取具有指定能力的 chat 模型列表（按 priority 排序）"""
    return [
        m["id"]
        for m in SMART_CONFIG.get("chat", {}).get("models", [])
        if m.get(capability, not value) == value
    ]


def validate_model_choice(
    model_id: str,
    has_image: bool = False,
    needs_search: bool = False,
) -> Optional[str]:
    """校验模型选择是否匹配需求，不匹配时返回警告文本

    Args:
        model_id: 选择的模型 ID
        has_image: 用户是否发送了图片
        needs_search: 是否需要联网搜索

    Returns:
        None=校验通过，str=警告文本（包含建议模型列表）
    """
    config = _find_model_config(model_id)
    if not config:
        return None  # 不在 chat 列表中的模型不做校验

    if has_image and not config.get("supports_image", True):
        alternatives = _get_models_with_capability("supports_image")[:5]
        return (
            f"模型 {model_id} 不支持图片理解，但用户发送了图片。"
            f"建议改用: {', '.join(alternatives)}"
        )

    if needs_search and not config.get("supports_search", False):
        alternatives = _get_models_with_capability("supports_search")[:5]
        return (
            f"模型 {model_id} 不支持联网搜索，但需要实时信息。"
            f"建议改用: {', '.join(alternatives)}"
        )

    return None


# ============================================================
# 工具构建
# ============================================================


def _get_model_enum(category: str) -> List[str]:
    """获取指定类别的模型 ID 列表"""
    return [m["id"] for m in SMART_CONFIG.get(category, {}).get("models", [])]


def _get_model_desc(category: str) -> str:
    """获取指定类别的模型描述文本（供千问阅读）

    chat 类型模型会自动附加能力标签（从 capabilities/supports_image/supports_search 生成），
    其他类型（image/video/web_search）沿用纯描述。
    """
    models = SMART_CONFIG.get(category, {}).get("models", [])
    lines: List[str] = []
    for m in models:
        base = f'{m["id"]} — {m["description"]}'
        if category == "chat":
            tags = _build_capability_tags(m)
            if tags:
                base += f" [{tags}]"
        lines.append(base)
    return "\n".join(lines)


def _build_capability_tags(model: Dict[str, Any]) -> str:
    """从模型配置生成能力标签字符串

    示例输出：'code,math,reasoning | 图片:✓ | 搜索:✗ | 深度思考:✓'
    缺失字段时使用默认值（supports_image=True, supports_search=False,
    supports_thinking=False）。
    """
    caps = model.get("capabilities", [])
    img = model.get("supports_image", True)
    search = model.get("supports_search", False)
    thinking = model.get("supports_thinking", False)

    parts: List[str] = []
    if caps:
        parts.append(",".join(caps))
    parts.append(f"图片:{'✓' if img else '✗'}")
    parts.append(f"搜索:{'✓' if search else '✗'}")
    parts.append(f"深度思考:{'✓' if thinking else '✗'}")
    return " | ".join(parts)


def build_router_tools() -> List[Dict[str, Any]]:
    """从 JSON 配置动态构建 ROUTER_TOOLS"""
    return [
        {
            "type": "function",
            "function": {
                "name": "generate_image",
                "description": (
                    "处理所有图片相关的操作请求：创建新图片、编辑已有图片、"
                    "调整尺寸比例、风格转换等。只要用户的目标是得到一张图片，就用此工具。"
                    "不适用：纯文字讨论图片话题（如分析风格、评价构图）。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prompt": {"type": "string", "description": "优化后的图片生成英文提示词"},
                        "model": {
                            "type": "string",
                            "enum": _get_model_enum("image"),
                            "description": _get_model_desc("image"),
                        },
                        "aspect_ratio": {
                            "type": "string",
                            "enum": ["1:1", "9:16", "16:9", "3:4", "4:3", "3:2", "2:3", "5:4", "4:5", "21:9"],
                            "description": (
                                "图片宽高比。"
                                "竖版/手机壁纸/海报=9:16，横版/桌面壁纸/风景=16:9，"
                                "社交媒体头像/方形=1:1，A4竖版=3:4，A4横版=4:3。"
                                "未明确要求时默认1:1"
                            ),
                        },
                        "resolution": {
                            "type": "string",
                            "enum": ["1K", "2K", "4K"],
                            "description": (
                                "图片分辨率（仅 nano-banana-pro 支持）。"
                                "1K=标准(24积分)，2K=高清(36积分)，4K=超高清(48积分)。"
                                "用户说高清/精细/大图时选2K或4K，日常生成默认1K"
                            ),
                        },
                        "output_format": {
                            "type": "string",
                            "enum": ["png", "jpg"],
                            "description": "输出格式。需要透明背景时选png，否则默认png",
                        },
                    },
                    "required": ["prompt", "model", "aspect_ratio"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "generate_video",
                "description": "用户需要生成、制作、创作视频时调用",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prompt": {"type": "string", "description": "优化后的视频生成英文提示词"},
                        "model": {
                            "type": "string",
                            "enum": _get_model_enum("video"),
                            "description": _get_model_desc("video"),
                        },
                    },
                    "required": ["prompt", "model"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "用户的问题需要搜索互联网获取最新信息、实时数据、新闻事件、价格行情等时调用",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "search_query": {"type": "string", "description": "搜索关键词"},
                        "system_prompt": {"type": "string", "description": "适合回答该问题的角色设定(一句话)"},
                        "model": {
                            "type": "string",
                            "enum": _get_model_enum("web_search"),
                            "description": _get_model_desc("web_search"),
                        },
                    },
                    "required": ["search_query", "system_prompt", "model"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "text_chat",
                "description": "普通对话、问答、分析、翻译、写作、代码、讨论图片风格、分析设计等文本交互时调用",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "system_prompt": {"type": "string", "description": "适合当前对话的角色设定(一句话)"},
                        "model": {
                            "type": "string",
                            "enum": _get_model_enum("chat"),
                            "description": _get_model_desc("chat"),
                        },
                    },
                    "required": ["system_prompt", "model"],
                },
            },
        },
    ]


# 模块级常量
ROUTER_TOOLS = build_router_tools()


def _get_available_model_set(failed_models: List[str]) -> set:
    """获取可用模型 ID 集合（排除失败模型 + 熔断 Provider 的模型）

    对于不在任何 Registry 中的模型 ID（如 smart_models.json 有但 Registry 没注册的），
    默认视为可用（不做熔断过滤），仅排除 failed_models。
    """
    # 先收集 smart_models.json 中所有模型 ID
    all_model_ids = {
        m["id"]
        for cat in SMART_CONFIG.values()
        for m in (cat.get("models", []) if isinstance(cat, dict) else [])
        if m["id"] not in failed_models
    }

    try:
        from services.circuit_breaker import is_provider_available
        from services.adapters.factory import (
            MODEL_REGISTRY, IMAGE_MODEL_REGISTRY, VIDEO_MODEL_REGISTRY,
        )

        # 合并所有 Registry 中有 provider 信息的模型
        provider_map = {}
        for mid, cfg in MODEL_REGISTRY.items():
            provider_map[mid] = cfg.provider
        for mid, cfg in IMAGE_MODEL_REGISTRY.items():
            provider_map[mid] = cfg["provider"]
        for mid, cfg in VIDEO_MODEL_REGISTRY.items():
            provider_map[mid] = cfg["provider"]

        # 过滤：Registry 中能查到 provider 的走熔断检查，查不到的直接放行
        return {
            mid for mid in all_model_ids
            if mid not in provider_map or is_provider_available(provider_map[mid])
        }
    except Exception:
        return all_model_ids


def build_retry_tools(
    gen_type: GenerationType,
    failed_models: List[str],
) -> List[Dict[str, Any]]:
    """构建重试用的工具列表（移除已失败的模型 + 添加 give_up）"""
    type_to_tool = {
        GenerationType.IMAGE: "generate_image",
        GenerationType.VIDEO: "generate_video",
        GenerationType.CHAT: "text_chat",
    }
    target_tool_name = type_to_tool.get(gen_type, "text_chat")

    # 获取可用模型集合（排除失败模型 + 熔断 Provider）
    available_models = _get_available_model_set(failed_models)

    retry_tools = []
    for tool in ROUTER_TOOLS:
        if tool["function"]["name"] != target_tool_name:
            continue
        tool_copy = copy.deepcopy(tool)
        props = tool_copy["function"]["parameters"]["properties"]
        if "model" in props and "enum" in props["model"]:
            props["model"]["enum"] = [
                m for m in props["model"]["enum"] if m in available_models
            ]
            if not props["model"]["enum"]:
                continue
        retry_tools.append(tool_copy)

    retry_tools.append({
        "type": "function",
        "function": {
            "name": "give_up",
            "description": "没有合适的替代模型可以重试时调用此工具放弃",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string", "description": "放弃的原因"},
                },
                "required": ["reason"],
            },
        },
    })

    return retry_tools


def get_remaining_models(
    gen_type: GenerationType,
    failed_models: List[str],
) -> List[str]:
    """获取同类型中未失败的模型（按优先级排序）"""
    gen_type_to_categories = {
        GenerationType.CHAT: ["chat", "web_search"],
        GenerationType.IMAGE: ["image"],
        GenerationType.VIDEO: ["video"],
    }
    categories = gen_type_to_categories.get(gen_type, [])

    # 获取可用模型集合（排除失败模型 + 熔断 Provider）
    available_models = _get_available_model_set(failed_models)

    seen: set = set()
    remaining: List[str] = []
    for cat in categories:
        for m in SMART_CONFIG.get(cat, {}).get("models", []):
            mid = m["id"]
            if mid in available_models and mid not in seen:
                seen.add(mid)
                remaining.append(mid)
    return remaining


# ============================================================
# 智能模型解析
# ============================================================

# 智能模型 ID（前端 smartModel.ts 对应）
SMART_MODEL_ID = "auto"


def resolve_auto_model(
    gen_type: GenerationType,
    content: list,
    recommended_model: Optional[str] = None,
) -> str:
    """将智能模型 ID ("auto") 解析为实际工作模型

    优先使用推荐模型（如 Phase1 选定），否则按 gen_type 返回默认模型。
    """
    from schemas.message import ImagePart

    if recommended_model and recommended_model in MODEL_TO_GEN_TYPE:
        if MODEL_TO_GEN_TYPE[recommended_model] == gen_type:
            return recommended_model
        logger.warning(
            f"Model type mismatch | recommended={recommended_model} | "
            f"gen_type={gen_type.value} | falling back to default"
        )

    if gen_type == GenerationType.VIDEO:
        has_images = any(isinstance(p, ImagePart) for p in content)
        if has_images:
            return get_image_to_video_model()
    # IMAGE_ECOM 复用 IMAGE 的默认模型
    if gen_type == GenerationType.IMAGE_ECOM:
        return AUTO_MODEL_DEFAULTS.get(GenerationType.IMAGE, DEFAULT_IMAGE_MODEL)
    return AUTO_MODEL_DEFAULTS.get(gen_type, DEFAULT_CHAT_MODEL)
