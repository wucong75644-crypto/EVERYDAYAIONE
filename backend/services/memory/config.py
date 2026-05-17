"""
记忆系统 V2 配置集中管理

所有记忆相关配置项统一在此，避免散落在多个文件中。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MemoryV2Config:
    """记忆系统 V2 全量配置"""

    # --- 开关 ---
    enabled: bool = True

    # --- L1 提取 ---
    l1_extraction_model: str = "qwen-plus"
    l1_dedup_model: str = "qwen-turbo"
    l1_max_messages_per_extraction: int = 10
    l1_max_background_messages: int = 5
    l1_max_memories_per_session: int = 20
    l1_extraction_timeout: float = 30.0   # 秒
    l1_dedup_timeout: float = 20.0        # 秒

    # --- L2 场景 ---
    l2_scene_model: str = "qwen-plus"
    l2_max_scenes: int = 15
    l2_scene_timeout: float = 60.0        # 秒

    # --- L3 画像 ---
    l3_persona_model: str = "qwen-plus"
    l3_trigger_every_n: int = 50
    l3_max_chars: int = 2000
    l3_persona_timeout: float = 60.0      # 秒

    # --- 管道调度 ---
    pipeline_every_n_conversations: int = 5
    pipeline_enable_warmup: bool = True
    pipeline_l1_idle_timeout: int = 60      # 秒
    pipeline_l2_delay_after_l1: int = 90    # 秒
    pipeline_l2_min_interval: int = 900     # 秒（15分钟）
    pipeline_l2_max_interval: int = 3600    # 秒（1小时）
    pipeline_session_active_hours: int = 24

    # --- 检索 ---
    retrieval_strategy: str = "hybrid"      # hybrid / embedding / keyword
    retrieval_max_results: int = 5
    retrieval_score_threshold: float = 0.3
    retrieval_rrf_k: int = 60
    retrieval_timeout: float = 5.0          # 秒

    # --- 上下文压缩 ---
    compress_mild_threshold: float = 0.50
    compress_aggressive_threshold: float = 0.85
    compress_emergency_threshold: float = 0.95
    compress_context_window: int = 200000

    # --- Embedding ---
    embedding_model: str = "text-embedding-v3"
    embedding_dimensions: int = 1024
    embedding_timeout: float = 10.0         # 秒

    # --- 千问 API ---
    dashscope_base_url: str = ""
    dashscope_api_key: str = ""


# 全局单例
_config: MemoryV2Config | None = None


def get_memory_config() -> MemoryV2Config:
    """获取记忆系统配置（懒加载，从 app config 读取）"""
    global _config
    if _config is None:
        _config = _load_from_app_config()
    return _config


def _load_from_app_config() -> MemoryV2Config:
    """从应用配置加载记忆系统配置"""
    try:
        from core.config import settings
        return MemoryV2Config(
            dashscope_base_url=getattr(settings, "dashscope_base_url", ""),
            dashscope_api_key=getattr(settings, "dashscope_api_key", ""),
            embedding_model=getattr(settings, "memory_embedding_model", "text-embedding-v3"),
        )
    except Exception:
        return MemoryV2Config()


def reset_config() -> None:
    """重置配置（测试用）"""
    global _config
    _config = None
