"""
KIE 模型配置

定义所有 KIE 模型的配置信息，包括:
- API 端点
- 成本定价
- 功能特性
- 使用限制
"""

from decimal import Decimal
from typing import Dict, Any, List, Optional
from enum import Enum


class KieModelCategory(str, Enum):
    """模型分类"""
    CHAT = "chat"
    IMAGE = "image"
    VIDEO = "video"


class KieAPIPattern(str, Enum):
    """API 调用模式"""
    SYNC_STREAM = "sync_stream"  # 同步流式 (Chat)
    ASYNC_TASK = "async_task"    # 异步任务 (Image/Video)


# ============================================================
# 模型完整配置
# ============================================================

KIE_MODEL_CONFIGS: Dict[str, Dict[str, Any]] = {
    # ========================================
    # Chat 模型 (OpenAI 兼容格式)
    # ========================================
    "gemini-3-pro": {
        "display_name": "Gemini 3 Pro",
        "description": "Google 最强推理模型，支持 PhD 级别推理、Google Search、函数调用",
        "category": KieModelCategory.CHAT,
        "api_pattern": KieAPIPattern.SYNC_STREAM,
        "provider": "kie",
        "model_id": "gemini-3-pro",
        "api_endpoint": "https://api.kie.ai/gemini-3-pro/v1/chat/completions",

        # 能力配置
        "context_window": 1_000_000,
        "max_output_tokens": 65536,
        "supports_vision": True,
        "supports_streaming": True,
        "supports_function_calling": True,
        "supports_google_search": True,
        "supports_response_format": True,
        "supports_reasoning_effort": True,

        # KIE 成本 (积分)
        "kie_cost_per_1m_input_tokens": 100,
        "kie_cost_per_1m_output_tokens": 700,

        # 用户定价 (成本 + 1 积分利润)
        "user_credits_per_1m_input_tokens": 101,
        "user_credits_per_1m_output_tokens": 701,

        # 状态
        "is_active": True,
        "priority": 10,
    },

    "gemini-3-flash": {
        "display_name": "Gemini 3 Flash",
        "description": "高性能快速推理模型，低延迟高吞吐",
        "category": KieModelCategory.CHAT,
        "api_pattern": KieAPIPattern.SYNC_STREAM,
        "provider": "kie",
        "model_id": "gemini-3-flash",
        "api_endpoint": "https://api.kie.ai/gemini-3-flash/v1/chat/completions",

        # 能力配置
        "context_window": 1_000_000,
        "max_output_tokens": 65536,
        "supports_vision": True,
        "supports_streaming": True,
        "supports_function_calling": True,
        "supports_google_search": False,
        "supports_response_format": False,
        "supports_reasoning_effort": True,

        # KIE 成本 (积分)
        "kie_cost_per_1m_input_tokens": 30,
        "kie_cost_per_1m_output_tokens": 180,

        # 用户定价 (成本 + 1 积分利润)
        "user_credits_per_1m_input_tokens": 31,
        "user_credits_per_1m_output_tokens": 181,

        # 状态
        "is_active": True,
        "priority": 20,
    },

    # ========================================
    # 图像模型 (异步任务)
    # ========================================
    "google/nano-banana": {
        "display_name": "Nano Banana",
        "description": "基础文生图模型，快速生成高质量图像",
        "category": KieModelCategory.IMAGE,
        "api_pattern": KieAPIPattern.ASYNC_TASK,
        "provider": "kie",
        "model_id": "google/nano-banana",
        "api_endpoint": "https://api.kie.ai/api/v1/jobs/createTask",

        # 能力配置
        "max_prompt_length": 20000,
        "requires_image_input": False,
        "supported_sizes": ["1:1", "9:16", "16:9", "3:4", "4:3", "3:2", "2:3", "5:4", "4:5", "21:9", "auto"],
        "supported_formats": ["png", "jpeg"],
        "supports_resolution": False,

        # KIE 成本 (积分)
        "kie_cost_per_image": 4,

        # 用户定价 (成本 + 1 积分利润)
        "user_credits_per_image": 5,

        # 状态
        "is_active": True,
        "priority": 30,
    },

    "google/nano-banana-edit": {
        "display_name": "Nano Banana Edit",
        "description": "图像编辑模型，支持图片修改和增强",
        "category": KieModelCategory.IMAGE,
        "api_pattern": KieAPIPattern.ASYNC_TASK,
        "provider": "kie",
        "model_id": "google/nano-banana-edit",
        "api_endpoint": "https://api.kie.ai/api/v1/jobs/createTask",

        # 能力配置
        "max_prompt_length": 20000,
        "requires_image_input": True,
        "max_images": 10,
        "max_image_size_mb": 10,
        "supported_sizes": ["1:1", "9:16", "16:9", "3:4", "4:3", "3:2", "2:3", "5:4", "4:5", "21:9", "auto"],
        "supported_formats": ["png", "jpeg"],
        "supports_resolution": False,

        # KIE 成本 (积分)
        "kie_cost_per_image": 4,

        # 用户定价 (成本 + 1 积分利润)
        "user_credits_per_image": 5,

        # 状态
        "is_active": True,
        "priority": 31,
    },

    "nano-banana-pro": {
        "display_name": "Nano Banana Pro",
        "description": "高级文生图模型，支持 4K 分辨率和参考图片",
        "category": KieModelCategory.IMAGE,
        "api_pattern": KieAPIPattern.ASYNC_TASK,
        "provider": "kie",
        "model_id": "nano-banana-pro",
        "api_endpoint": "https://api.kie.ai/api/v1/jobs/createTask",

        # 能力配置
        "max_prompt_length": 20000,
        "requires_image_input": False,
        "max_images": 8,  # 参考图片
        "max_image_size_mb": 30,
        "supported_sizes": ["1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9", "auto"],
        "supported_formats": ["png", "jpg"],
        "supports_resolution": True,
        "supported_resolutions": ["1K", "2K", "4K"],

        # KIE 成本 (按分辨率，积分)
        "kie_cost_per_image_by_resolution": {
            "1K": 24,
            "2K": 36,
            "4K": 48,
        },

        # 用户定价 (成本 + 1 积分利润)
        "user_credits_per_image_by_resolution": {
            "1K": 25,
            "2K": 37,
            "4K": 49,
        },

        # 状态
        "is_active": True,
        "priority": 32,
    },

    # ========================================
    # 视频模型 (异步任务)
    # ========================================
    "sora-2-text-to-video": {
        "display_name": "Sora 2 文生视频",
        "description": "文本描述生成视频，支持10/15秒",
        "category": KieModelCategory.VIDEO,
        "api_pattern": KieAPIPattern.ASYNC_TASK,
        "provider": "kie",
        "model_id": "sora-2-text-to-video",
        "api_endpoint": "https://api.kie.ai/api/v1/jobs/createTask",

        # 能力配置
        "max_prompt_length": 10000,
        "requires_image_input": False,
        "requires_prompt": True,
        "supported_durations": [10, 15],
        "supported_aspect_ratios": ["portrait", "landscape"],
        "supports_watermark_removal": True,

        # KIE 成本 (按时长，积分)
        "kie_cost_by_duration": {
            10: 30,
            15: 45,
        },

        # 用户定价 (成本 + 1 积分利润)
        "user_credits_by_duration": {
            10: 31,
            15: 46,
        },

        # 状态
        "is_active": True,
        "priority": 40,
    },

    "sora-2-image-to-video": {
        "display_name": "Sora 2 图生视频",
        "description": "图片作为首帧生成视频，支持10/15秒",
        "category": KieModelCategory.VIDEO,
        "api_pattern": KieAPIPattern.ASYNC_TASK,
        "provider": "kie",
        "model_id": "sora-2-image-to-video",
        "api_endpoint": "https://api.kie.ai/api/v1/jobs/createTask",

        # 能力配置
        "max_prompt_length": 10000,
        "requires_image_input": True,
        "requires_prompt": True,
        "max_image_size_mb": 10,
        "supported_durations": [10, 15],
        "supported_aspect_ratios": ["portrait", "landscape"],
        "supports_watermark_removal": True,

        # KIE 成本 (按时长，积分)
        "kie_cost_by_duration": {
            10: 30,
            15: 45,
        },

        # 用户定价 (成本 + 1 积分利润)
        "user_credits_by_duration": {
            10: 31,
            15: 46,
        },

        # 状态
        "is_active": True,
        "priority": 41,
    },

    "sora-2-pro-storyboard": {
        "display_name": "Sora 2 Pro 故事板",
        "description": "专业故事板视频生成，支持25秒长视频",
        "category": KieModelCategory.VIDEO,
        "api_pattern": KieAPIPattern.ASYNC_TASK,
        "provider": "kie",
        "model_id": "sora-2-pro-storyboard",
        "api_endpoint": "https://api.kie.ai/api/v1/jobs/createTask",

        # 能力配置
        "requires_image_input": False,
        "requires_prompt": False,
        "max_image_size_mb": 10,
        "supported_durations": [10, 15, 25],
        "supported_aspect_ratios": ["portrait", "landscape"],
        "supports_watermark_removal": False,

        # KIE 成本 (按时长，积分)
        "kie_cost_by_duration": {
            10: 90,
            15: 135,
            25: 225,
        },

        # 用户定价 (成本 + 1 积分利润)
        "user_credits_by_duration": {
            10: 91,
            15: 136,
            25: 226,
        },

        # 状态
        "is_active": True,
        "priority": 42,
    },
}


# ============================================================
# 便捷访问函数
# ============================================================

def get_model_config(model_name: str) -> Optional[Dict[str, Any]]:
    """获取模型配置"""
    return KIE_MODEL_CONFIGS.get(model_name)


def get_models_by_category(category: KieModelCategory) -> List[Dict[str, Any]]:
    """按分类获取模型列表"""
    return [
        {"name": name, **config}
        for name, config in KIE_MODEL_CONFIGS.items()
        if config["category"] == category
    ]


def get_chat_models() -> List[Dict[str, Any]]:
    """获取所有 Chat 模型"""
    return get_models_by_category(KieModelCategory.CHAT)


def get_image_models() -> List[Dict[str, Any]]:
    """获取所有图像模型"""
    return get_models_by_category(KieModelCategory.IMAGE)


def get_video_models() -> List[Dict[str, Any]]:
    """获取所有视频模型"""
    return get_models_by_category(KieModelCategory.VIDEO)


def get_active_models() -> List[Dict[str, Any]]:
    """获取所有激活的模型"""
    return [
        {"name": name, **config}
        for name, config in KIE_MODEL_CONFIGS.items()
        if config.get("is_active", False)
    ]


def is_async_task_model(model_name: str) -> bool:
    """判断是否为异步任务模型"""
    config = get_model_config(model_name)
    if not config:
        return False
    return config.get("api_pattern") == KieAPIPattern.ASYNC_TASK


def is_chat_model(model_name: str) -> bool:
    """判断是否为 Chat 模型"""
    config = get_model_config(model_name)
    if not config:
        return False
    return config.get("category") == KieModelCategory.CHAT


# ============================================================
# 价格计算（用户定价 = KIE 成本 + 1 积分利润）
# ============================================================

def calculate_chat_cost(
    model_name: str,
    input_tokens: int,
    output_tokens: int,
) -> Dict[str, Any]:
    """
    计算 Chat 模型用户积分消耗

    Returns:
        {
            "kie_cost": int,        # KIE 成本（积分）
            "user_credits": int,    # 用户支付（积分）
            "profit": int,          # 利润（积分）
            "breakdown": {...}
        }
    """
    config = get_model_config(model_name)
    if not config or config["category"] != KieModelCategory.CHAT:
        raise ValueError(f"Invalid chat model: {model_name}")

    # KIE 成本
    kie_input = int(input_tokens * config["kie_cost_per_1m_input_tokens"] / 1_000_000)
    kie_output = int(output_tokens * config["kie_cost_per_1m_output_tokens"] / 1_000_000)
    kie_total = kie_input + kie_output

    # 用户定价
    user_input = int(input_tokens * config["user_credits_per_1m_input_tokens"] / 1_000_000)
    user_output = int(output_tokens * config["user_credits_per_1m_output_tokens"] / 1_000_000)
    user_total = user_input + user_output

    return {
        "kie_cost": kie_total,
        "user_credits": user_total,
        "profit": user_total - kie_total,
        "breakdown": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "kie_input_cost": kie_input,
            "kie_output_cost": kie_output,
            "user_input_credits": user_input,
            "user_output_credits": user_output,
        }
    }


def calculate_image_cost(
    model_name: str,
    image_count: int = 1,
    resolution: Optional[str] = None,
) -> Dict[str, Any]:
    """
    计算图像模型用户积分消耗

    Returns:
        {
            "kie_cost": int,        # KIE 成本（积分）
            "user_credits": int,    # 用户支付（积分）
            "profit": int,          # 利润（积分）
            "breakdown": {...}
        }
    """
    config = get_model_config(model_name)
    if not config or config["category"] != KieModelCategory.IMAGE:
        raise ValueError(f"Invalid image model: {model_name}")

    if config.get("supports_resolution") and resolution:
        kie_per_image = config["kie_cost_per_image_by_resolution"].get(
            resolution, config["kie_cost_per_image_by_resolution"]["1K"]
        )
        user_per_image = config["user_credits_per_image_by_resolution"].get(
            resolution, config["user_credits_per_image_by_resolution"]["1K"]
        )
    else:
        kie_per_image = config["kie_cost_per_image"]
        user_per_image = config["user_credits_per_image"]

    kie_total = kie_per_image * image_count
    user_total = user_per_image * image_count

    return {
        "kie_cost": kie_total,
        "user_credits": user_total,
        "profit": user_total - kie_total,
        "breakdown": {
            "image_count": image_count,
            "resolution": resolution,
            "kie_cost_per_image": kie_per_image,
            "user_credits_per_image": user_per_image,
        }
    }


def calculate_video_cost(
    model_name: str,
    duration_seconds: int,
) -> Dict[str, Any]:
    """
    计算视频模型用户积分消耗

    Returns:
        {
            "kie_cost": int,        # KIE 成本（积分）
            "user_credits": int,    # 用户支付（积分）
            "profit": int,          # 利润（积分）
            "breakdown": {...}
        }
    """
    config = get_model_config(model_name)
    if not config or config["category"] != KieModelCategory.VIDEO:
        raise ValueError(f"Invalid video model: {model_name}")

    # 视频按固定时长计费
    kie_cost = config["kie_cost_by_duration"].get(duration_seconds)
    user_credits = config["user_credits_by_duration"].get(duration_seconds)

    if kie_cost is None or user_credits is None:
        supported = list(config["kie_cost_by_duration"].keys())
        raise ValueError(
            f"Unsupported duration: {duration_seconds}s. Supported: {supported}"
        )

    return {
        "kie_cost": kie_cost,
        "user_credits": user_credits,
        "profit": user_credits - kie_cost,
        "breakdown": {
            "duration_seconds": duration_seconds,
            "kie_cost": kie_cost,
            "user_credits": user_credits,
        }
    }
