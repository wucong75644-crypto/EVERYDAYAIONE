"""
记忆智能过滤器（评分制）

通过千问 LLM 对 Mem0 初筛记忆逐条评分（1-10），只保留高相关记忆。
基于 LlamaIndex / Mem0 Reranker 最佳实践，用评分代替编号列表，精度更高。
降级链：qwen-turbo → qwen-plus → 跳过精排（直接用初筛结果）
"""

import re
from typing import Any, Dict, List, Optional, Tuple

import httpx
from loguru import logger

from core.config import settings

# 相关性评分阈值：≥ 此分数的记忆才注入（1-10 分制）
RELEVANCE_THRESHOLD = 7

FILTER_SYSTEM_PROMPT = """你是一个记忆相关性评分器。用户的记忆只有三类：

1. 身份信息：职业、公司、所在行业
2. 持久偏好：常用工具、风格偏好、工作习惯
3. 业务方向：主营业务、目标市场、发展计划

给定用户的当前问题和候选记忆，评估每条记忆对回答该问题的价值。

评分标准（1-10）：
- 9-10：直接决定回答方向
  问"推荐电商选品工具" + "用户在亚马逊卖家居" → 9（决定推荐哪类工具）
  问"AI绘图怎么用" + "用户常用Midjourney" → 9（可以基于已有工具回答）
- 7-8：提供有价值的个性化上下文
  问"怎么提升销量" + "用户是跨境电商" → 8（影响建议侧重点）
- 4-6：同领域但对当前问题帮助不大
  问"推荐AI绘图工具" + "用户偏好简约风格" → 5（风格偏好不影响工具选择）
  问"物流方案" + "用户职业是产品经理" → 4（职业不影响物流建议）
- 1-3：与当前问题无关
  问"怎么做短视频" + "用户主营家居用品" → 2

核心原则：如果AI不知道这条记忆，回答质量会下降吗？
- 会 → 7分以上
- 不会 → 6分以下

输出格式（每条一行）：
Doc: 1, Relevance: 8
Doc: 2, Relevance: 3

只输出评分，不要解释。"""

# 模块级 HTTP 客户端（延迟初始化）
_client: Optional[httpx.AsyncClient] = None


async def _get_client() -> httpx.AsyncClient:
    """获取或创建 HTTP 客户端"""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            base_url=settings.dashscope_base_url,
            headers={
                "Authorization": f"Bearer {settings.dashscope_api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(settings.memory_filter_timeout),
        )
    return _client


def _build_filter_prompt(
    query: str, memories: List[Dict[str, Any]]
) -> str:
    """构建精排用户消息"""
    lines = []
    for i, m in enumerate(memories, 1):
        lines.append(f"Doc {i}: {m['memory']}")
    memory_block = "\n".join(lines)
    return f"用户问题：{query}\n\n候选记忆：\n{memory_block}"


def _parse_score_response(
    text: str, total: int
) -> Optional[List[Tuple[int, int]]]:
    """
    解析评分响应，返回 [(0-based index, score), ...] 按分数降序排列。
    失败返回 None。
    """
    text = text.strip()
    # 匹配 "Doc: N, Relevance: M" 或 "Doc N, Relevance: M" 等变体
    pattern = r"Doc[:\s]*(\d+)[,\s]*Relevance[:\s]*(\d+)"
    matches = re.findall(pattern, text, re.IGNORECASE)

    if not matches:
        return None

    results = []
    for doc_str, score_str in matches:
        doc_id = int(doc_str)
        score = int(score_str)
        # 校验范围
        if 1 <= doc_id <= total and 1 <= score <= 10:
            results.append((doc_id - 1, score))  # 转为 0-based

    if not results:
        return None

    # 按分数降序排列
    results.sort(key=lambda x: x[1], reverse=True)
    return results


async def _call_filter_model(
    model: str, query: str, memories: List[Dict[str, Any]]
) -> Optional[List[Tuple[int, int]]]:
    """调用单个模型做评分，返回 [(0-based index, score), ...]，失败返回 None"""
    client = await _get_client()
    user_prompt = _build_filter_prompt(query, memories)

    try:
        response = await client.post(
            "/chat/completions",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": FILTER_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.0,
                "max_tokens": 500,
            },
        )
        response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        scored = _parse_score_response(content, len(memories))

        if scored is not None:
            above = sum(1 for _, s in scored if s >= RELEVANCE_THRESHOLD)
            logger.info(
                f"Memory filter done | model={model} | "
                f"input={len(memories)} | "
                f"above_threshold(>={RELEVANCE_THRESHOLD})={above}"
            )
        return scored

    except httpx.TimeoutException:
        logger.warning(f"Memory filter timeout | model={model}")
        return None
    except Exception as e:
        logger.warning(f"Memory filter failed | model={model} | error={e}")
        return None


async def filter_memories(
    query: str, memories: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    对候选记忆做千问评分式精排。

    只保留评分 ≥ RELEVANCE_THRESHOLD 的记忆，按分数降序排列。
    降级链：qwen-turbo → qwen-plus → 返回原始列表
    """
    if not memories or not query or not query.strip():
        return memories

    # ≤3 条无需精排，向量相似度已经够用
    if len(memories) <= 3:
        return memories

    if not settings.dashscope_api_key:
        logger.warning("Memory filter skipped: no dashscope_api_key")
        return memories

    logger.info(
        f"Memory filter start | query={query[:50]} | "
        f"candidates={len(memories)}"
    )

    # 第一级：qwen-turbo
    scored = await _call_filter_model(
        settings.memory_filter_model, query, memories
    )
    if scored is not None:
        result = [
            memories[idx] for idx, score in scored
            if score >= RELEVANCE_THRESHOLD
        ]
        if not result:
            logger.info("Memory filter: no memory above threshold, keeping top-1")
            # 取最高分的一条兜底
            return [memories[scored[0][0]]]
        return result

    # 第二级：qwen-plus
    logger.info("Memory filter: falling back to secondary model")
    scored = await _call_filter_model(
        settings.memory_filter_fallback_model, query, memories
    )
    if scored is not None:
        result = [
            memories[idx] for idx, score in scored
            if score >= RELEVANCE_THRESHOLD
        ]
        if not result:
            return [memories[scored[0][0]]]
        return result

    # 第三级：跳过精排，直接用 Mem0 初筛结果
    logger.warning("Memory filter: all models failed, using raw results")
    return memories


async def close() -> None:
    """关闭 HTTP 客户端"""
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
        _client = None
