"""
RRF 混合检索管道

替代原有的 Mem0 向量搜索 + 千问精排，实现：
- pgvector 余弦相似度搜索
- tsvector BM25 关键词搜索
- RRF (Reciprocal Rank Fusion) 融合

移植自腾讯 TencentDB-Agent-Memory search-utils.ts
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Literal

import jieba

from .config import get_memory_config
from .l1_extractor import _get_embedding

logger = logging.getLogger(__name__)


# ============================================================
# Types
# ============================================================

@dataclass
class ScoredMemory:
    """带分数的记忆检索结果"""
    atom_id: str
    content: str
    type: str
    priority: int
    scene_name: str
    score: float
    activity_start: str | None = None
    activity_end: str | None = None


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
        org_id: str,
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
        cfg = self._cfg
        max_results = max_results or cfg.retrieval_max_results
        strategy = strategy or cfg.retrieval_strategy
        extend_limit = max_results * 3  # 召回更多用于融合

        vector_results: list[dict] = []
        bm25_results: list[dict] = []

        if strategy in ("hybrid", "embedding"):
            embedding = await _get_embedding(query)
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
            merged = self._rrf_merge(vector_results, bm25_results, max_results)
        elif vector_results:
            merged = vector_results[:max_results]
        elif bm25_results:
            merged = bm25_results[:max_results]
        else:
            merged = []

        return [
            ScoredMemory(
                atom_id=r["record_id"],
                content=r["content"],
                type=r["type"],
                priority=r["priority"],
                scene_name=r.get("scene_name", ""),
                score=r.get("rrf_score", r.get("score", 0)),
                activity_start=r.get("activity_start"),
                activity_end=r.get("activity_end"),
            )
            for r in merged
        ]

    # ============================
    # 向量检索
    # ============================

    async def _search_vector(
        self,
        query_embedding: list[float],
        user_id: str,
        org_id: str,
        limit: int,
    ) -> list[dict]:
        """pgvector 余弦相似度搜索"""
        try:
            # psycopg %s 不支持同参数复用，所以 embedding 传两次
            embedding_str = f"[{','.join(str(x) for x in query_embedding)}]"
            sql = """
                SELECT id::text as record_id, content, type, priority, scene_name,
                       activity_start_time::text as activity_start,
                       activity_end_time::text as activity_end,
                       1 - (embedding <=> $1::vector) as score
                FROM memory_atoms
                WHERE org_id = $2 AND user_id = $3 AND NOT is_deleted
                      AND embedding IS NOT NULL
                ORDER BY embedding <=> $4::vector
                LIMIT $5
            """
            rows = await self._db.fetch(
                sql,
                embedding_str, org_id, user_id, embedding_str, limit,
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
        org_id: str,
        limit: int,
    ) -> list[dict]:
        """tsvector BM25 关键词搜索"""
        try:
            # jieba 分词 → tsquery
            tokens = [t for t in jieba.cut_for_search(query) if len(t) > 1]
            if not tokens:
                return []
            tsquery = " | ".join(tokens)  # OR 逻辑，更宽容

            # $1 出现两次（rank + WHERE），psycopg %s 需要传两次
            sql = """
                SELECT id::text as record_id, content, type, priority, scene_name,
                       activity_start_time::text as activity_start,
                       activity_end_time::text as activity_end,
                       ts_rank_cd(content_tsv, to_tsquery('simple', $1::text)) as score
                FROM memory_atoms
                WHERE org_id = $2 AND user_id = $3 AND NOT is_deleted
                      AND content_tsv @@ to_tsquery('simple', $4::text)
                ORDER BY score DESC
                LIMIT $5
            """
            rows = await self._db.fetch(
                sql,
                tsquery, org_id, user_id, tsquery, limit,
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
                scored[rid] = {**item, "rrf_score": rrf_score}

        # BM25 结果
        for rank, item in enumerate(bm25_results):
            rid = item["record_id"]
            rrf_score = 1.0 / (k + rank + 1)
            if rid in scored:
                scored[rid]["rrf_score"] += rrf_score
            else:
                scored[rid] = {**item, "rrf_score": rrf_score}

        # 按 RRF 分值降序
        sorted_results = sorted(scored.values(), key=lambda x: x["rrf_score"], reverse=True)
        return sorted_results[:max_results]

    # ============================
    # 格式化注入
    # ============================

    def format_for_injection(self, memories: list[ScoredMemory]) -> str:
        """
        格式化为注入 user prompt 的文本

        格式：- [type|scene] content (活动时间: start ~ end)
        """
        if not memories:
            return ""

        lines = []
        for m in memories:
            # 类型标签
            if m.scene_name:
                tag = f"[{m.type}|{m.scene_name}]"
            else:
                tag = f"[{m.type}]"

            # 时间标注（episodic）
            time_note = ""
            if m.activity_start and m.activity_end:
                time_note = f" (活动时间: {m.activity_start[:10]} ~ {m.activity_end[:10]})"
            elif m.activity_start:
                time_note = f" (活动时间: {m.activity_start[:10]})"

            lines.append(f"- {tag} {m.content}{time_note}")

        return "\n".join(lines)
