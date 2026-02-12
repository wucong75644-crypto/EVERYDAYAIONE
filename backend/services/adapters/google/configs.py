"""
Google Gemini 模型配置

集中管理 Google 模型的参数、速率限制等配置信息。
"""

from decimal import Decimal
from typing import Dict, Any

# Google Gemini 模型配置
GOOGLE_MODEL_CONFIGS: Dict[str, Dict[str, Any]] = {
    "gemini-2.5-flash": {
        "display_name": "Gemini 2.5 Flash",
        "context_window": 1_000_000,
        "max_output_tokens": 8192,
        "supports_vision": True,
        "supports_video": True,
        "supports_audio": True,
        "supports_tools": True,
        # 成本（免费层）
        "cost_per_1k_input": Decimal("0"),
        "cost_per_1k_output": Decimal("0"),
        # 速率限制（免费层）
        "rate_limit_rpm": 10,  # 10 requests per minute
        "rate_limit_tpm": 250_000,  # 250k tokens per minute
        "rate_limit_rpd": 250,  # 250 requests per day
    },
    "gemini-2.5-pro": {
        "display_name": "Gemini 2.5 Pro",
        "context_window": 2_000_000,
        "max_output_tokens": 8192,
        "supports_vision": True,
        "supports_video": True,
        "supports_audio": True,
        "supports_tools": True,
        # 成本（免费层）
        "cost_per_1k_input": Decimal("0"),
        "cost_per_1k_output": Decimal("0"),
        # 速率限制（免费层）
        "rate_limit_rpm": 2,  # 2 requests per minute
        "rate_limit_tpm": 32_000,  # 32k tokens per minute
        "rate_limit_rpd": 50,  # 50 requests per day
    },
}


def get_model_config(model_id: str) -> Dict[str, Any]:
    """
    获取模型配置

    Args:
        model_id: 模型 ID

    Returns:
        模型配置字典

    Raises:
        ValueError: 模型不存在
    """
    if model_id not in GOOGLE_MODEL_CONFIGS:
        raise ValueError(f"Unsupported Google model: {model_id}")
    return GOOGLE_MODEL_CONFIGS[model_id]
