"""
消息价值打分器

两层评估：
A 层（规则）：零成本过滤明显废话，零成本识别明显高价值
B 层（Embedding）：当前 query 与历史消息的语义相关度

对标：豆包 TASE 语义熵打分 + 信息密度过滤
替代：不需要本地小模型，复用 DashScope text-embedding-v3

设计文档：docs/document/TECH_上下文工程重构.md §六
"""

import re
from typing import Any, Dict, List, Optional

from loguru import logger


# ============================================================
# A 层：规则打分（覆盖两端：明显废话 + 明显高价值）
# ============================================================

_LOW_VALUE_EXACT = frozenset({
    '好的', '嗯', '知道了', 'ok', 'OK', '好', '是的', '对',
    '谢谢', '感谢', '了解', '明白', '收到', '行', '可以',
    '没问题', '好吧', '嗯嗯', '哦', '噢', '哈哈', '666',
    '👍', '👌', '🙏',
})

_HIGH_VALUE_PATTERNS = [
    (re.compile(r'\d{10,}'), 0.25),                              # 订单号/长数字编码
    (re.compile(r'[A-Z]{2,}\d{3,}'), 0.20),                     # 商品编码 (SKU123)
    (re.compile(r'[¥￥]\s*[\d,.]+|[\d,.]+\s*元'), 0.15),         # 金额
    (re.compile(r'\d{4}[-/]\d{1,2}[-/]\d{1,2}'), 0.15),         # 日期
    (re.compile(r'订单|库存|退[货款]|发货|物流|商品|采购'), 0.10), # ERP 实体
    (re.compile(r'https?://\S+'), 0.05),                          # URL
]


def _extract_text(msg: Dict[str, Any]) -> str:
    """从消息中提取纯文本"""
    content = msg.get("content", "")
    if isinstance(content, list):
        return " ".join(
            p.get("text", "") for p in content if isinstance(p, dict)
        )
    return str(content)


def _rule_score(msg: Dict[str, Any]) -> float:
    """A 层：规则打分（0.0~1.0）

    返回值含义：
    - 0.0~0.2: 明确废话
    - 0.3~0.5: 规则无法判断（需要 B 层）
    - 0.6~1.0: 明确高价值
    """
    text = _extract_text(msg).strip()
    role = msg.get("role", "")

    # system / tool 消息不参与打分（各有专门的预算桶）
    if role in ("system", "tool"):
        return 1.0

    # 精确匹配低价值
    if text in _LOW_VALUE_EXACT:
        return 0.1

    # 极短消息
    if len(text) < 8:
        return 0.2

    # 基础分
    score = 0.4

    # 用户消息比助手消息更重要
    if role == "user":
        score += 0.1

    # 高价值模式匹配
    for pattern, bonus in _HIGH_VALUE_PATTERNS:
        if pattern.search(text):
            score += bonus

    # 长度加分（有上限）
    if len(text) > 50:
        score += 0.05
    if len(text) > 200:
        score += 0.05

    return min(1.0, score)


# ============================================================
# B 层：Embedding 相关度（复用 DashScope text-embedding-v3）
# ============================================================


# 模块级 DashScope 客户端（复用连接池，避免每次创建/销毁）
_ds_client: Optional["DashScopeClient"] = None


def _get_ds_client() -> "DashScopeClient":
    """延迟初始化 DashScope 客户端（模块级单例）"""
    global _ds_client
    if _ds_client is None:
        from services.dashscope_client import DashScopeClient
        _ds_client = DashScopeClient("context_summary_timeout", default_timeout=10.0)
    return _ds_client


async def _batch_compute_embeddings(
    texts: List[str],
) -> List[Optional[List[float]]]:
    """批量计算文本向量（DashScope 一次调用最多 25 条）

    延迟与单条相当（~80ms），解决逐条调用 N×80ms 的性能问题。
    空文本返回 None。复用模块级 DashScopeClient 连接池。
    """
    from core.config import settings
    from services.knowledge_config import EMBEDDING_MODEL, EMBEDDING_DIMS

    if not settings.dashscope_api_key:
        return [None] * len(texts)

    # 过滤空文本，记录原始位置
    valid = [(i, t[:2000]) for i, t in enumerate(texts) if t.strip()]
    if not valid:
        return [None] * len(texts)

    results: List[Optional[List[float]]] = [None] * len(texts)

    try:
        client = await _get_ds_client().get()
        BATCH_MAX = 25
        for chunk_start in range(0, len(valid), BATCH_MAX):
            chunk = valid[chunk_start:chunk_start + BATCH_MAX]
            resp = await client.post(
                "/embeddings",
                json={
                    "model": EMBEDDING_MODEL,
                    "input": [t for _, t in chunk],
                    "dimensions": EMBEDDING_DIMS,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            for j, emb_data in enumerate(data["data"]):
                orig_idx = chunk[j][0]
                results[orig_idx] = emb_data["embedding"]
    except Exception as e:
        logger.warning(f"Batch embedding failed | error={type(e).__name__}: {e or 'no detail'}")

    return results


async def _compute_relevance_scores(
    messages: List[Dict[str, Any]],
    query: str,
) -> List[float]:
    """B 层：批量计算消息与当前 query 的语义相关度

    返回 0.0~1.0 的相关度分数列表（与 messages 一一对应）。
    失败时返回全 0.5（退化为只用 A 层）。
    """
    if not query:
        return [0.5] * len(messages)

    try:
        from services.knowledge_config import compute_embedding
        import numpy as np

        # 提取消息文本
        texts = [_extract_text(msg)[:500] for msg in messages]

        # 计算 query embedding（单条）
        query_emb = await compute_embedding(query[:500])
        if query_emb is None:
            return [0.5] * len(messages)

        # 批量计算消息 embedding
        msg_embeddings = await _batch_compute_embeddings(texts)

        # 计算余弦相似度
        q = np.array(query_emb)
        q_norm = np.linalg.norm(q)
        if q_norm < 1e-8:
            return [0.5] * len(messages)

        scores = []
        for msg_emb in msg_embeddings:
            if msg_emb is None:
                scores.append(0.5)
                continue
            m = np.array(msg_emb)
            cos_sim = float(np.dot(q, m) / (q_norm * np.linalg.norm(m) + 1e-8))
            # 映射到 0~1（余弦相似度范围 -1~1，实际多在 0.3~0.9）
            scores.append(max(0.0, min(1.0, (cos_sim + 1) / 2)))

        return scores

    except Exception as e:
        logger.warning(f"Embedding relevance scoring failed, fallback to rules | error={type(e).__name__}: {e or 'no detail'}")
        return [0.5] * len(messages)


# ============================================================
# 综合打分（A + B 融合）
# ============================================================


def score_messages_sync(
    messages: List[Dict[str, Any]],
) -> List[float]:
    """同步版：只用 A 层规则打分（用于 enforce_budget 等同步场景）"""
    return [_rule_score(msg) for msg in messages]


_EMBEDDING_TIMEOUT = 3.0  # Embedding B 层总超时（秒），超时退化为纯规则


async def score_messages(
    messages: List[Dict[str, Any]],
    current_query: str = "",
) -> List[float]:
    """异步版：A 层规则 + B 层 Embedding 融合

    融合公式：final = 0.4 × rule_score + 0.6 × relevance_score

    权重说明：
    - 相关度权重更高（0.6），因为"跟当前问题相关"比"信息密度高"更重要
    - 但规则层可以一票否决：rule_score < 0.2 时直接定为低分
    - B 层总超时 3 秒，超时退化为纯规则打分（不阻塞首次响应）
    """
    import asyncio

    rule_scores = [_rule_score(msg) for msg in messages]

    # 如果没有 query 或只有少量消息，跳过 Embedding 层
    if not current_query or len(messages) <= 5:
        return rule_scores

    try:
        relevance_scores = await asyncio.wait_for(
            _compute_relevance_scores(messages, current_query),
            timeout=_EMBEDDING_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.warning(
            f"Embedding scoring timeout ({_EMBEDDING_TIMEOUT}s), "
            f"fallback to rules | messages={len(messages)}"
        )
        return rule_scores

    final_scores = []
    for rule, relevance in zip(rule_scores, relevance_scores):
        # 规则层一票否决
        if rule < 0.2:
            final_scores.append(rule)
            continue
        # 融合
        final = 0.4 * rule + 0.6 * relevance
        final_scores.append(min(1.0, final))

    return final_scores
