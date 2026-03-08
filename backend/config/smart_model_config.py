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
from typing import Any, Dict, List

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


# ============================================================
# 工具构建
# ============================================================


def _get_model_enum(category: str) -> List[str]:
    """获取指定类别的模型 ID 列表"""
    return [m["id"] for m in SMART_CONFIG.get(category, {}).get("models", [])]


def _get_model_desc(category: str) -> str:
    """获取指定类别的模型描述文本（供千问阅读）"""
    models = SMART_CONFIG.get(category, {}).get("models", [])
    return "\n".join(f'{m["id"]} — {m["description"]}' for m in models)


def build_router_tools() -> List[Dict[str, Any]]:
    """从 JSON 配置动态构建 ROUTER_TOOLS"""
    return [
        {
            "type": "function",
            "function": {
                "name": "generate_image",
                "description": (
                    "用户需要生成、绘制、画、创作、修改、编辑图片时调用。"
                    "注意：如果用户只是在讨论图片相关话题（如分析图片风格、"
                    "讨论设计理念）而非要求生成图片，请勿调用此工具。"
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

    retry_tools = []
    for tool in ROUTER_TOOLS:
        if tool["function"]["name"] != target_tool_name:
            continue
        tool_copy = copy.deepcopy(tool)
        props = tool_copy["function"]["parameters"]["properties"]
        if "model" in props and "enum" in props["model"]:
            props["model"]["enum"] = [
                m for m in props["model"]["enum"] if m not in failed_models
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

    seen: set = set()
    remaining: List[str] = []
    for cat in categories:
        for m in SMART_CONFIG.get(cat, {}).get("models", []):
            mid = m["id"]
            if mid not in failed_models and mid not in seen:
                seen.add(mid)
                remaining.append(mid)
    return remaining
