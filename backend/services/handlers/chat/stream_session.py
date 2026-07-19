"""Provider 流的通用请求级累计状态。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class StreamTotals:
    text: str = ""
    thinking: str = ""
    usage: dict[str, Any] = field(
        default_factory=lambda: {
            "prompt_tokens": 0,
            "completion_tokens": 0,
        }
    )
    chunk_count: int = 0
    last_finish_reason: str | None = None


def accumulate_cache_usage(totals: StreamTotals, chunk: Any) -> None:
    """累积 Provider 返回的缓存命中与创建 Token。"""
    if getattr(chunk, "cached_tokens", 0):
        totals.usage["cached_tokens"] = (
            totals.usage.get("cached_tokens", 0) + chunk.cached_tokens
        )
    if getattr(chunk, "cache_creation_tokens", 0):
        totals.usage["cache_creation_tokens"] = (
            totals.usage.get("cache_creation_tokens", 0)
            + chunk.cache_creation_tokens
        )
