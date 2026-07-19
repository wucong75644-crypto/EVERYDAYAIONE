"""
RRF 混合检索管道

实现通用 Curated Memory 检索：
- pgvector 余弦相似度搜索
- tsvector BM25 关键词搜索
- RRF (Reciprocal Rank Fusion) 融合

移植自腾讯 TencentDB-Agent-Memory search-utils.ts
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Any, Literal

import jieba
from loguru import logger

from .config import get_memory_config
from .embedding import get_embedding
from .recall_policy import normalize_relevance, rank_for_recall


# ============================================================
# Types
# ============================================================

@dataclass
class ScoredMemory:
    """带分数的记忆检索结果"""
    atom_id: str
    content: str
    kind: str
    priority: int
    score: float
    activity_start: str | None = None
    activity_end: str | None = None
    valid_from: str | None = None
    valid_until: str | None = None
    source_message_ids: tuple[str, ...] = ()


# ============================================================
# Pipeline
# ============================================================

class RetrievalPipeline:
    """RRF 混合检索管道"""

    def __init__(self, db_pool=None):
        self._db = db_pool
        self._cfg = get_memory_config()

    async def search(
        self,
        query: str,
        user_id: str,
        org_id: str | None,
        max_results: int | None = None,
        strategy: Literal["hybrid", "embedding", "keyword"] | None = None,
    ) -> list[ScoredMemory]:
        """
        混合检索：向量 + BM25 + RRF 融合

        Args:
            query: 用户查询
            user_id, org_id: 租户隔离
            max_results: 最大返回数（默认5）
            strategy: 检索策略（默认hybrid）
        """
        if not query.strip():
            return []
        cfg = self._cfg
        max_results = max_results or cfg.retrieval_max_results
        strategy = strategy or cfg.retrieval_strategy
        extend_limit = max_results * 3  # 召回更多用于融合

        vector_results: list[dict] = []
        bm25_results: list[dict] = []

        if strategy in ("hybrid", "embedding"):
            embedding = await get_embedding(query)
            if embedding:
                vector_results = await self._search_vector(
                    embedding, user_id, org_id, extend_limit
                )

        if strategy in ("hybrid", "keyword"):
            bm25_results = await self._search_bm25(
                query, user_id, org_id, extend_limit
            )

        # 融合
        if strategy == "hybrid" and vector_results and bm25_results:
            merged = self._rrf_merge(
                vector_results,
                bm25_results,
                extend_limit,
            )
        elif vector_results:
            merged = [
                {
                    **item,
                    "relevance_score": normalize_relevance(
                        vector_score=float(item.get("score") or 0.0),
                    ),
                }
                for item in vector_results
            ]
        elif bm25_results:
            merged = [
                {
                    **item,
                    "relevance_score": normalize_relevance(
                        keyword_score=float(item.get("score") or 0.0),
                    ),
                }
                for item in bm25_results
            ]
        else:
            merged = []

        merged = rank_for_recall(
            merged,
            max_results=max_results,
            score_threshold=cfg.retrieval_score_threshold,
        )

        # V2 阶段 6.1: 召回质量监控
        # 记录每次召回的关键指标, 用于离线评估 Recall@k / Precision@k
        # 字段:
        #   candidates_vector / candidates_bm25 = 各路召回原始候选数
        #   final_top_k = 融合后返回数
        #   score_distribution = 分值最大/最小/中位数 (判断"召回质量")
        if merged:
            scores = [r.get("rrf_score", r.get("score", 0)) for r in merged]
            score_max = max(scores) if scores else 0
            score_min = min(scores) if scores else 0
            score_mid = sorted(scores)[len(scores) // 2] if scores else 0
            logger.info(
                f"curated memory recall | strategy={strategy} | "
                f"vector_n={len(vector_results)} bm25_n={len(bm25_results)} | "
                f"final_top_k={len(merged)} | "
                f"score_max={score_max:.3f} mid={score_mid:.3f} min={score_min:.3f} | "
                f"user={user_id[:8]}"
            )
        else:
            logger.info(
                f"curated memory recall EMPTY | strategy={strategy} | "
                f"vector_n={len(vector_results)} bm25_n={len(bm25_results)} | "
                f"user={user_id[:8]} | query={query[:30]}"
            )

        return [
            ScoredMemory(
                atom_id=r["record_id"],
                content=r["content"],
                kind=str(r.get("kind") or "memory"),
                priority=r["priority"],
                score=float(r.get("score") or 0.0),
                activity_start=r.get("activity_start"),
                activity_end=r.get("activity_end"),
                valid_from=_text_or_none(r.get("valid_from")),
                valid_until=_text_or_none(r.get("valid_until")),
                source_message_ids=tuple(
                    str(value) for value in (r.get("source_message_ids") or [])
                ),
            )
            for r in merged
        ]

    async def get(
        self,
        atom_id: str,
        user_id: str,
        org_id: str | None,
    ) -> ScoredMemory | None:
        """按 ID 严格读取当前仍可用的 Curated Memory。"""
        try:
            row = await asyncio.wait_for(
                self._db.fetchrow(
                    """SELECT id::text AS record_id, content, priority,
                          metadata->>'kind' AS kind,
                          activity_start_time::text AS activity_start,
                          activity_end_time::text AS activity_end,
                          valid_from::text, valid_until::text,
                          source_message_ids
                   FROM memory_atoms
                   WHERE id = $1::uuid
                     AND org_id IS NOT DISTINCT FROM $2::uuid
                     AND user_id = $3::uuid AND NOT is_deleted
                     AND status = 'active'
                     AND (valid_from IS NULL OR valid_from <= NOW())
                     AND (valid_until IS NULL OR valid_until > NOW())
                   LIMIT 1""",
                    atom_id,
                    org_id,
                    user_id,
                ),
                timeout=self._cfg.retrieval_timeout,
            )
        except Exception as exc:
            logger.warning(
                "Memory Get failed | user_id={} | atom_id={} | error_type={}",
                user_id,
                atom_id,
                type(exc).__name__,
            )
            return None
        return _to_scored_memory(dict(row), score=1.0) if row else None

    # ============================
    # 向量检索
    # ============================

    async def _search_vector(
        self,
        query_embedding: list[float],
        user_id: str,
        org_id: str | None,
        limit: int,
    ) -> list[dict]:
        """仅召回状态与有效期均允许注入的 Curated Memory。"""
        try:
            # psycopg %s 不支持同参数复用，所以 embedding 传两次
            embedding_str = f"[{','.join(str(x) for x in query_embedding)}]"
            sql = """
                SELECT id::text as record_id, content, priority,
                       activity_start_time::text as activity_start,
                       activity_end_time::text as activity_end,
                       metadata->>'kind' AS kind,
                       valid_from::text, valid_until::text, updated_at::text,
                       source_message_ids,
                       1 - (embedding <=> $1::vector) as score
                FROM memory_atoms
                WHERE org_id IS NOT DISTINCT FROM $2::uuid
                      AND user_id = $3::uuid
                      AND NOT is_deleted AND status = 'active'
                      AND (valid_from IS NULL OR valid_from <= NOW())
                      AND (valid_until IS NULL OR valid_until > NOW())
                      AND embedding IS NOT NULL
                ORDER BY embedding <=> $4::vector
                LIMIT $5
            """
            params = [embedding_str, org_id, user_id, embedding_str, limit]
            rows = await asyncio.wait_for(
                self._db.fetch(sql, *params),
                timeout=self._cfg.retrieval_timeout,
            )
            return [dict(r) for r in rows]
        except Exception as e:
            logger.warning(f"Retrieval: vector search failed: {e}")
            return []

    # ============================
    # BM25 检索
    # ============================

    async def _search_bm25(
        self,
        query: str,
        user_id: str,
        org_id: str | None,
        limit: int,
    ) -> list[dict]:
        """关键词召回同样执行 Curated 生命周期硬过滤。"""
        try:
            # jieba 分词 → tsquery
            tokens = [t for t in jieba.cut_for_search(query) if len(t) > 1]
            if not tokens:
                return []
            tsquery = " | ".join(tokens)  # OR 逻辑，更宽容

            sql = """
                SELECT id::text as record_id, content, priority,
                       activity_start_time::text as activity_start,
                       activity_end_time::text as activity_end,
                       metadata->>'kind' AS kind,
                       valid_from::text, valid_until::text, updated_at::text,
                       source_message_ids,
                       ts_rank_cd(content_tsv, to_tsquery('simple', $1::text)) as score
                FROM memory_atoms
                WHERE org_id IS NOT DISTINCT FROM $2::uuid
                      AND user_id = $3::uuid
                      AND NOT is_deleted AND status = 'active'
                      AND (valid_from IS NULL OR valid_from <= NOW())
                      AND (valid_until IS NULL OR valid_until > NOW())
                      AND content_tsv @@ to_tsquery('simple', $4::text)
                ORDER BY score DESC
                LIMIT $5
            """
            params = [tsquery, org_id, user_id, tsquery, limit]
            rows = await asyncio.wait_for(
                self._db.fetch(sql, *params),
                timeout=self._cfg.retrieval_timeout,
            )
            return [dict(r) for r in rows]
        except Exception as e:
            logger.warning(f"Retrieval: BM25 search failed: {e}")
            return []

    # ============================
    # RRF 融合
    # ============================

    def _rrf_merge(
        self,
        vector_results: list[dict],
        bm25_results: list[dict],
        max_results: int,
    ) -> list[dict]:
        """
        Reciprocal Rank Fusion (RRF) 算法

        score(i) = Σ 1 / (K + rank + 1)
        K = 60（标准常数）

        同一记录在两个列表中的分值累加。
        """
        k = self._cfg.retrieval_rrf_k
        scored: dict[str, dict] = {}

        # 向量结果
        for rank, item in enumerate(vector_results):
            rid = item["record_id"]
            rrf_score = 1.0 / (k + rank + 1)
            if rid in scored:
                scored[rid]["rrf_score"] += rrf_score
            else:
                scored[rid] = {
                    **item,
                    "rrf_score": rrf_score,
                    "vector_score": float(item.get("score") or 0.0),
                    "keyword_score": 0.0,
                }

        # BM25 结果
        for rank, item in enumerate(bm25_results):
            rid = item["record_id"]
            rrf_score = 1.0 / (k + rank + 1)
            if rid in scored:
                scored[rid]["rrf_score"] += rrf_score
                scored[rid]["keyword_score"] = float(item.get("score") or 0.0)
            else:
                scored[rid] = {
                    **item,
                    "rrf_score": rrf_score,
                    "vector_score": 0.0,
                    "keyword_score": float(item.get("score") or 0.0),
                }

        for item in scored.values():
            item["relevance_score"] = normalize_relevance(
                vector_score=float(item["vector_score"]),
                keyword_score=float(item["keyword_score"]),
                matched_both=bool(
                    item["vector_score"] and item["keyword_score"]
                ),
            )

        # 按 RRF 分值降序
        sorted_results = sorted(scored.values(), key=lambda x: x["rrf_score"], reverse=True)
        return sorted_results[:max_results]

    # ============================
    # 格式化注入
    # ============================

    def format_for_injection(self, memories: list[ScoredMemory]) -> str:
        """
        格式化为注入 user prompt 的文本

        格式：- [kind] content (活动时间: start ~ end)
        """
        if not memories:
            return ""

        lines = []
        for m in memories:
            tag = f"[{m.kind or 'memory'}]"
            time_note = ""
            if m.activity_start and m.activity_end:
                time_note = f" (活动时间: {m.activity_start[:10]} ~ {m.activity_end[:10]})"
            elif m.activity_start:
                time_note = f" (活动时间: {m.activity_start[:10]})"

            lines.append(f"- {tag} {m.content}{time_note}")

        return "\n".join(lines)


def _to_scored_memory(row: dict[str, Any], *, score: float) -> ScoredMemory:
    return ScoredMemory(
        atom_id=str(row["record_id"]),
        content=str(row["content"]),
        kind=str(row.get("kind") or "memory"),
        priority=int(row["priority"]),
        score=score,
        activity_start=_text_or_none(row.get("activity_start")),
        activity_end=_text_or_none(row.get("activity_end")),
        valid_from=_text_or_none(row.get("valid_from")),
        valid_until=_text_or_none(row.get("valid_until")),
        source_message_ids=tuple(
            str(value) for value in (row.get("source_message_ids") or [])
        ),
    )


def _text_or_none(value: Any) -> str | None:
    return str(value) if value is not None else None
