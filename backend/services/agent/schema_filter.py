"""
Schema 智能过滤器

根据用户消息筛选相关的 staging 文件 schema，供上下文注入。

主路径：text-embedding-v3 余弦相似度 >0.65 → 返回相关文件索引
降级：全部 <0.65 → 调 Qwen-Flash 判断
兜底：超时 → 返回最近 3 个 last_used 的文件

复用 memory_filter.py 架构（DashScopeClient + 降级链）。
设计文档：docs/document/TECH_data_query工具设计.md §五
"""

from __future__ import annotations

import math
from typing import Any

from loguru import logger

# 余弦相似度阈值：≥ 此值认为 schema 与用户消息相关
_SIMILARITY_THRESHOLD = 0.65

# 降级 LLM 判断的系统提示词
_FILTER_SYSTEM_PROMPT = """你是一个数据文件相关性判断器。

给定用户的当前问题和候选数据文件列表（含列名、行数等信息），
判断用户问题需要查询哪些文件。

输出格式（只输出相关文件的编号，逗号分隔）：
相关文件: 1, 3

如果没有文件相关，输出：
相关文件: 无

只输出结果，不要解释。"""

# 模块级 DashScope 客户端（延迟导入避免测试时拉 config）
_ds_client = None


def _get_ds_client():
    global _ds_client
    if _ds_client is None:
        from services.dashscope_client import DashScopeClient
        _ds_client = DashScopeClient("schema_filter_timeout", default_timeout=3.0)
    return _ds_client


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """计算两个向量的余弦相似度。"""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


async def filter_schemas(
    query: str,
    schema_entries: list[tuple[str, Any, str, list[float] | None]],
    recent_entries: list[tuple[str, Any, str]],
) -> list[tuple[str, Any, str]]:
    """筛选与用户消息相关的 schema 条目。

    Args:
        query: 用户消息文本
        schema_entries: [(key, file_ref, schema_text, embedding), ...]
                        来自 registry.get_schema_entries()
        recent_entries: [(key, file_ref, schema_text), ...]
                        来自 registry.get_recent_schema_entries(3)，兜底用

    Returns:
        [(key, file_ref, schema_text), ...] 过滤后的相关条目
    """
    if not query or not schema_entries:
        return []

    # 只有 1 个文件时直接返回
    if len(schema_entries) == 1:
        k, ref, text, _ = schema_entries[0]
        return [(k, ref, text)]

    # 主路径：embedding 余弦相似度
    has_all_embeddings = all(emb is not None for _, _, _, emb in schema_entries)

    if has_all_embeddings:
        matched = await _filter_by_embedding(query, schema_entries)
        if matched:
            logger.info(
                f"Schema filter: embedding matched | "
                f"input={len(schema_entries)} | output={len(matched)}"
            )
            return matched
        # 全部 <0.65 → 降级到 Qwen-Flash
        logger.debug("Schema filter: all below threshold, falling back to LLM")

    # 降级：Qwen-Flash 判断
    llm_result = await _filter_by_llm(query, schema_entries)
    if llm_result is not None:
        logger.info(
            f"Schema filter: LLM fallback matched | "
            f"input={len(schema_entries)} | output={len(llm_result)}"
        )
        return llm_result

    # 兜底：返回最近 3 个 last_used 的文件
    logger.info(
        f"Schema filter: using recent fallback | count={len(recent_entries)}"
    )
    return recent_entries


async def _filter_by_embedding(
    query: str,
    entries: list[tuple[str, Any, str, list[float] | None]],
) -> list[tuple[str, Any, str]]:
    """用 embedding 余弦相似度过滤。

    Returns:
        匹配的条目列表（空列表 = 全部低于阈值，需降级）
    """
    try:
        from services.knowledge_config import compute_embedding
        query_emb = await compute_embedding(query[:500])
        if not query_emb:
            return []
    except Exception as e:
        logger.warning(f"Schema filter: query embedding failed | error={e}")
        return []

    matched: list[tuple[str, Any, str]] = []
    for key, ref, text, emb in entries:
        if emb is None:
            continue
        sim = _cosine_similarity(query_emb, emb)
        if sim >= _SIMILARITY_THRESHOLD:
            matched.append((key, ref, text))
            logger.debug(
                f"Schema filter: match | file={ref.filename} | sim={sim:.3f}"
            )

    return matched


async def _filter_by_llm(
    query: str,
    entries: list[tuple[str, Any, str, list[float] | None]],
) -> list[tuple[str, Any, str]] | None:
    """用 Qwen-Flash 判断相关文件。

    Returns:
        匹配的条目列表，或 None（失败时降级到兜底）
    """
    from core.config import settings

    if not settings.dashscope_api_key:
        return None

    # 构建文件列表提示
    file_descs = []
    for i, (_, ref, text, _) in enumerate(entries, 1):
        # 截取 schema 前 200 字符作为摘要
        brief = text[:200].replace("\n", " ")
        file_descs.append(f"文件 {i}: {ref.filename}（{brief}）")
    files_text = "\n".join(file_descs)

    user_prompt = f"用户问题：{query}\n\n候选文件：\n{files_text}"

    try:
        client = await _get_ds_client().get()
        response = await client.post(
            "/chat/completions",
            json={
                "model": settings.schema_filter_model,
                "messages": [
                    {"role": "system", "content": _FILTER_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.0,
                "max_tokens": 100,
                "enable_thinking": False,
            },
        )
        response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        return _parse_llm_response(content, entries)
    except Exception as e:
        logger.warning(f"Schema filter LLM failed | error={type(e).__name__}: {e}")
        return None


def _parse_llm_response(
    text: str,
    entries: list[tuple[str, Any, str, list[float] | None]],
) -> list[tuple[str, Any, str]] | None:
    """解析 LLM 返回的相关文件编号。"""
    import re

    text = text.strip()
    if text == "无" or text.endswith(": 无") or text.endswith(":无"):
        return []

    # 匹配 "相关文件: 1, 3" 或直接的数字列表
    numbers = re.findall(r"\d+", text)
    if not numbers:
        return None

    matched = []
    for num_str in numbers:
        idx = int(num_str) - 1  # 转为 0-based
        if 0 <= idx < len(entries):
            key, ref, schema_text, _ = entries[idx]
            matched.append((key, ref, schema_text))

    return matched if matched else None


async def close() -> None:
    """关闭 HTTP 客户端"""
    if _ds_client is not None:
        await _ds_client.close()
