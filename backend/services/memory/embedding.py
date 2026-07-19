"""记忆运行时的批量文本向量边界。"""

from __future__ import annotations

import math

from loguru import logger

from .config import get_memory_config


async def get_embedding(text: str) -> list[float] | None:
    """生成单条 embedding，保持旧调用方协议。"""
    embeddings = await get_embeddings([text])
    return embeddings[0] if embeddings else None


async def get_embeddings(texts: list[str]) -> list[list[float]] | None:
    """单次请求批量生成 embedding；任一结果缺失时整批失败。"""
    if not texts:
        return []
    try:
        from openai import AsyncOpenAI

        cfg = get_memory_config()
        client = AsyncOpenAI(
            api_key=cfg.dashscope_api_key,
            base_url=(
                cfg.dashscope_base_url
                or "https://dashscope.aliyuncs.com/compatible-mode/v1"
            ),
        )
        response = await client.embeddings.create(
            model=cfg.embedding_model,
            input=[text[:5000] for text in texts],
            dimensions=cfg.embedding_dimensions,
        )
        embeddings = [item.embedding for item in response.data]
        if (
            len(embeddings) != len(texts)
            or any(not item for item in embeddings)
            or any(
                not math.isfinite(float(value))
                for item in embeddings
                for value in item
            )
        ):
            raise RuntimeError("embedding batch result incomplete")
        return embeddings
    except Exception as exc:
        logger.warning(
            "Memory embedding batch failed | count={} | error_type={}",
            len(texts),
            type(exc).__name__,
        )
        return None
