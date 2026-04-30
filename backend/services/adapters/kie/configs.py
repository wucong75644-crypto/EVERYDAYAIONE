"""
KIE 模型配置

从各适配器文件提取的模型配置常量。
"""

from decimal import Decimal


# ============================================================
# Chat 模型配置
# ============================================================

CHAT_MODEL_CONFIGS = {
    "gemini-3-pro": {
        "context_window": 1_000_000,
        "max_output_tokens": 65536,
        "supports_vision": True,
        "supports_google_search": True,
        "supports_function_calling": True,
        "supports_response_format": True,
        "cost_per_1k_input": Decimal("0.0005"),   # $0.50 / 1M
        "cost_per_1k_output": Decimal("0.0035"),  # $3.50 / 1M
        "credits_per_1k_input": 1,   # 1 积分 / 1K input
        "credits_per_1k_output": 7,  # 7 积分 / 1K output
    },
    "gemini-3-flash": {
        "context_window": 1_000_000,
        "max_output_tokens": 65536,
        "supports_vision": True,
        "supports_google_search": True,
        "supports_function_calling": True,
        "supports_response_format": False,
        "cost_per_1k_input": Decimal("0.00015"),   # $0.15 / 1M
        "cost_per_1k_output": Decimal("0.0009"),   # $0.90 / 1M
        "credits_per_1k_input": Decimal("0.3"),    # 0.3 积分 / 1K input
        "credits_per_1k_output": Decimal("1.8"),   # 1.8 积分 / 1K output
    },
}


# ============================================================
# Image 模型配置
# ============================================================

IMAGE_MODEL_CONFIGS = {
    "google/nano-banana": {
        "model_id": "google/nano-banana",
        "description": "基础文生图",
        "requires_image_input": False,
        "max_prompt_length": 20000,
        "supported_sizes": [
            "1:1", "9:16", "16:9", "3:4", "4:3",
            "3:2", "2:3", "5:4", "4:5", "21:9", "auto"
        ],
        "supported_formats": ["png", "jpeg"],
        "supports_resolution": False,
        "cost_per_image": Decimal("0.02"),
        "credits_per_image": 4,
    },
    "google/nano-banana-edit": {
        "model_id": "google/nano-banana-edit",
        "description": "图像编辑",
        "requires_image_input": True,
        "max_images": 10,
        "max_image_size_mb": 10,
        "max_prompt_length": 5000,
        "supported_sizes": [
            "1:1", "9:16", "16:9", "3:4", "4:3",
            "3:2", "2:3", "5:4", "4:5", "21:9", "auto"
        ],
        "supported_formats": ["png", "jpeg", "webp"],
        "supports_resolution": False,
        "cost_per_image": Decimal("0.02"),
        "credits_per_image": 6,
    },
    "nano-banana-pro": {
        "model_id": "nano-banana-pro",
        "description": "高级文生图 (支持4K)",
        "requires_image_input": False,
        "max_images": 8,  # 参考图片
        "max_image_size_mb": 30,
        "max_prompt_length": 10000,
        "supported_sizes": [
            "1:1", "2:3", "3:2", "3:4", "4:3",
            "4:5", "5:4", "9:16", "16:9", "21:9", "auto"
        ],
        "supported_formats": ["png", "jpg"],
        "supports_resolution": True,
        "supported_resolutions": ["1K", "2K", "4K"],
        "cost_per_image": {
            "1K": Decimal("0.09"),
            "2K": Decimal("0.09"),
            "4K": Decimal("0.12"),
        },
        "credits_per_image": {
            "1K": 18,
            "2K": 18,
            "4K": 24,
        },
    },
    "gpt-image-2-text-to-image": {
        "model_id": "gpt-image-2-text-to-image",
        "description": "GPT Image 2 文生图（OpenAI 最强图片生成）",
        "requires_image_input": False,
        "max_prompt_length": 20000,
        "supported_sizes": [
            "1:1", "9:16", "16:9", "3:4", "4:3", "auto"
        ],
        "supported_formats": ["png"],
        "supports_resolution": True,
        "supported_resolutions": ["1K", "2K", "4K"],
        "cost_per_image": {
            "1K": Decimal("0.03"),
            "2K": Decimal("0.05"),
            "4K": Decimal("0.08"),
        },
        "credits_per_image": {
            "1K": 6,
            "2K": 10,
            "4K": 16,
        },
    },
}


# ============================================================
# Video 模型配置
# ============================================================

VIDEO_MODEL_CONFIGS = {
    "sora-2-text-to-video": {
        "model_id": "sora-2-text-to-video",
        "description": "文本生成视频",
        "requires_image_input": False,
        "requires_prompt": True,
        "max_prompt_length": 10000,
        "supported_frames": ["10", "15"],
        "supports_watermark_removal": True,
        "cost_per_second": Decimal("0.015"),
        "credits_per_second": 3,  # 30 credits/10秒
    },
    "sora-2-image-to-video": {
        "model_id": "sora-2-image-to-video",
        "description": "图片生成视频",
        "requires_image_input": True,
        "requires_prompt": True,
        "max_prompt_length": 10000,
        "max_image_size_mb": 10,
        "supported_frames": ["10", "15"],
        "supports_watermark_removal": True,
        "cost_per_second": Decimal("0.015"),
        "credits_per_second": 3,  # 30 credits/10秒
    },
    "sora-2-pro-storyboard": {
        "model_id": "sora-2-pro-storyboard",
        "description": "故事板视频生成 (专业版)",
        "requires_image_input": False,  # 可选
        "requires_prompt": False,  # 无 prompt
        "supported_frames": ["10", "15", "25"],
        "supports_watermark_removal": False,
        "cost_per_second": Decimal("0.054"),
        # 阶梯定价：10秒=150, 15秒=270, 25秒=270
        "credits_by_duration": {"10": 150, "15": 270, "25": 270},
    },
}
