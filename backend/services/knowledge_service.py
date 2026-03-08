"""
知识库服务

提供知识 CRUD、去重、向量检索、种子知识导入。
采用 psycopg 直连 PostgreSQL + pgvector。
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from core.config import settings
from services.graph_service import graph_service
from services.knowledge_config import (
    compute_content_hash,
    compute_embedding,
    format_knowledge_node,
    get_cached_search,
    get_pg_connection,
    invalidate_search_cache,
    is_kb_available,
    set_cached_search,
)
# 向后兼容：record_metric 已移至 knowledge_metrics.py
from services.knowledge_metrics import record_metric  # noqa: F401


# ===== 知识 CRUD =====


async def _dedup_by_hash(cur, conn, content_hash: str, source: str) -> Optional[str]:
    """Hash 完全匹配去重：匹配则更新 confidence，返回已有节点 ID"""
    await cur.execute(
        """
        SELECT id, source, confidence FROM knowledge_nodes
        WHERE content_hash = %(hash)s AND is_deleted = FALSE;
        """,
        {"hash": content_hash},
    )
    existing = await cur.fetchone()
    if not existing:
        return None

    existing_id, existing_source, existing_conf = existing
    if existing_source == "seed" and source == "auto":
        return str(existing_id)

    new_conf = min(existing_conf + settings.kb_confidence_boost, 1.0)
    await cur.execute(
        """
        UPDATE knowledge_nodes
        SET confidence = %(conf)s, updated_at = NOW(), hit_count = hit_count + 1
        WHERE id = %(id)s;
        """,
        {"conf": new_conf, "id": existing_id},
    )
    await conn.commit()
    invalidate_search_cache()
    return str(existing_id)


async def _dedup_by_vector(
    cur, conn, *, category: str, embedding: list,
    source: str, title: str, content: str,
    content_hash: str, metadata: Optional[Dict[str, Any]],
) -> Optional[str]:
    """向量相似度 > 0.9 去重：匹配则合并内容，返回已有节点 ID"""
    await cur.execute(
        """
        SELECT id, source, confidence
        FROM knowledge_nodes
        WHERE is_deleted = FALSE
            AND category = %(category)s
            AND embedding IS NOT NULL
            AND 1 - (embedding <=> %(emb)s::vector) > 0.9
        ORDER BY embedding <=> %(emb)s::vector ASC
        LIMIT 1;
        """,
        {"category": category, "emb": str(embedding)},
    )
    similar = await cur.fetchone()
    if not similar:
        return None

    sim_id, sim_source, sim_conf = similar
    if sim_source == "seed" and source == "auto":
        return str(sim_id)

    new_conf = min(sim_conf + settings.kb_confidence_boost, 1.0)
    await cur.execute(
        """
        UPDATE knowledge_nodes
        SET content = %(content)s, title = %(title)s,
            content_hash = %(hash)s, confidence = %(conf)s,
            embedding = %(emb)s::vector,
            metadata = %(meta)s, updated_at = NOW()
        WHERE id = %(id)s;
        """,
        {
            "content": content, "title": title,
            "hash": content_hash, "conf": new_conf,
            "emb": str(embedding),
            "meta": json.dumps(metadata or {}),
            "id": sim_id,
        },
    )
    await conn.commit()
    invalidate_search_cache()
    return str(sim_id)


async def add_knowledge(
    *,
    category: str,
    subcategory: Optional[str] = None,
    node_type: str,
    title: str,
    content: str,
    metadata: Optional[Dict[str, Any]] = None,
    source: str = "auto",
    confidence: float = 0.5,
    scope: str = "global",
) -> Optional[str]:
    """
    添加知识条目（含去重 + 向量化）

    去重规则：
    1. content_hash 完全匹配 → 更新 confidence/updated_at
    2. 向量相似度 > 0.9 → 合并到已有节点
    3. 无重复 → 插入新节点

    Returns:
        节点 ID（新增或已有），None 表示失败
    """
    if not is_kb_available():
        return None

    conn_ctx = await get_pg_connection()
    if conn_ctx is None:
        return None

    content_hash = compute_content_hash(category, title, content)

    try:
        async with conn_ctx as conn:
            async with conn.cursor() as cur:
                # 1. Hash 去重
                dup_id = await _dedup_by_hash(cur, conn, content_hash, source)
                if dup_id:
                    return dup_id

                # 2. 向量去重
                embedding = await compute_embedding(f"{title} {content}")
                if embedding:
                    dup_id = await _dedup_by_vector(
                        cur, conn,
                        category=category, embedding=embedding,
                        source=source, title=title, content=content,
                        content_hash=content_hash, metadata=metadata,
                    )
                    if dup_id:
                        return dup_id

                # 3. 节点数量上限淘汰
                await cur.execute(
                    "SELECT COUNT(*) FROM knowledge_nodes WHERE is_deleted = FALSE;"
                )
                count_row = await cur.fetchone()
                if count_row and count_row[0] >= settings.kb_max_nodes:
                    await cur.execute(
                        """
                        UPDATE knowledge_nodes SET is_deleted = TRUE
                        WHERE id = (
                            SELECT id FROM knowledge_nodes
                            WHERE is_deleted = FALSE AND source != 'seed'
                            ORDER BY confidence ASC, updated_at ASC
                            LIMIT 1
                        );
                        """
                    )

                # 4. 插入新节点
                emb_value = str(embedding) if embedding else None
                await cur.execute(
                    """
                    INSERT INTO knowledge_nodes (
                        category, subcategory, node_type, title, content,
                        metadata, embedding, source, confidence, scope, content_hash
                    ) VALUES (
                        %(category)s, %(subcategory)s, %(node_type)s, %(title)s,
                        %(content)s, %(metadata)s, %(embedding)s::vector, %(source)s,
                        %(confidence)s, %(scope)s, %(hash)s
                    )
                    RETURNING id;
                    """,
                    {
                        "category": category,
                        "subcategory": subcategory,
                        "node_type": node_type,
                        "title": title,
                        "content": content,
                        "metadata": json.dumps(metadata or {}),
                        "embedding": emb_value,
                        "source": source,
                        "confidence": confidence,
                        "scope": scope,
                        "hash": content_hash,
                    },
                )
                result = await cur.fetchone()
                await conn.commit()
                invalidate_search_cache()
                node_id = str(result[0]) if result else None
                logger.info(
                    f"Knowledge added | id={node_id} | category={category} | "
                    f"title={title[:50]}"
                )
                return node_id

    except Exception as e:
        logger.error(f"Knowledge add failed | title={title[:50]} | error={e}")
        return None


async def search_relevant(
    query: str,
    limit: Optional[int] = None,
    threshold: Optional[float] = None,
    category: Optional[str] = None,
    scope: str = "global",
) -> List[Dict[str, Any]]:
    """
    向量检索相关知识（用于路由注入）

    Returns:
        格式化的知识列表，按相似度降序
    """
    if not is_kb_available():
        return []

    limit = limit or settings.kb_search_limit
    threshold = threshold or settings.kb_search_threshold

    # 缓存检查
    cache_key = f"{query[:100]}|{category}|{scope}|{limit}"
    cached = get_cached_search(cache_key)
    if cached is not None:
        return cached

    embedding = await compute_embedding(query)
    if not embedding:
        return []

    conn_ctx = await get_pg_connection()
    if conn_ctx is None:
        return []

    category_filter = "AND category = %(category)s" if category else ""
    params: Dict[str, Any] = {
        "emb": str(embedding),
        "threshold": threshold,
        "limit": limit,
        "scope": scope,
    }
    if category:
        params["category"] = category

    query_sql = f"""
    SELECT id, category, subcategory, node_type, title, content,
           confidence, hit_count, source, metadata,
           1 - (embedding <=> %(emb)s::vector) AS similarity
    FROM knowledge_nodes
    WHERE is_deleted = FALSE
        AND embedding IS NOT NULL
        AND (scope = %(scope)s OR scope = 'global')
        {category_filter}
        AND 1 - (embedding <=> %(emb)s::vector) > %(threshold)s
    ORDER BY similarity DESC
    LIMIT %(limit)s;
    """

    try:
        async with conn_ctx as conn:
            async with conn.cursor() as cur:
                await cur.execute(query_sql, params)
                rows = await cur.fetchall()
                columns = [desc.name for desc in cur.description]
                results = [dict(zip(columns, row)) for row in rows]

                # 命中计数 +1
                if results:
                    hit_ids = [r["id"] for r in results]
                    await cur.execute(
                        """
                        UPDATE knowledge_nodes
                        SET hit_count = hit_count + 1,
                            confidence = LEAST(confidence + %(boost)s, 1.0)
                        WHERE id = ANY(%(ids)s);
                        """,
                        {"ids": hit_ids, "boost": settings.kb_confidence_boost},
                    )
                    await conn.commit()

                formatted = [format_knowledge_node(r) for r in results]
                set_cached_search(cache_key, formatted)
                return formatted

    except Exception as e:
        logger.error(f"Knowledge search failed | query={query[:50]} | error={e}")
        return []


async def get_node_by_metadata(
    key: str,
    value: str,
    category: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """根据 metadata 字段查找节点（用于查找模型/工具实体节点）"""
    if not is_kb_available():
        return None

    conn_ctx = await get_pg_connection()
    if conn_ctx is None:
        return None

    category_filter = "AND category = %(category)s" if category else ""
    params: Dict[str, Any] = {"key": key, "value": value}
    if category:
        params["category"] = category

    try:
        async with conn_ctx as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                    SELECT id, category, subcategory, node_type, title, content,
                           confidence, hit_count, source, metadata
                    FROM knowledge_nodes
                    WHERE is_deleted = FALSE
                        AND metadata->>%(key)s = %(value)s
                        {category_filter}
                    LIMIT 1;
                    """,
                    params,
                )
                row = await cur.fetchone()
                if not row:
                    return None
                columns = [desc.name for desc in cur.description]
                return dict(zip(columns, row))
    except Exception as e:
        logger.error(f"Knowledge get_node_by_metadata failed | {key}={value} | error={e}")
        return None


# ===== 种子知识导入 =====


async def load_seed_knowledge(seed_file: Optional[str] = None) -> int:
    """
    从 JSON 文件导入种子知识（先清理旧种子再重新导入，确保内容最新）

    Returns:
        成功导入的条目数
    """
    if not is_kb_available():
        return 0

    if seed_file is None:
        seed_file = str(Path(__file__).parent.parent / "data" / "seed_knowledge.json")

    seed_path = Path(seed_file)
    if not seed_path.exists():
        logger.warning(f"Seed knowledge file not found | path={seed_file}")
        return 0

    try:
        with open(seed_path, encoding="utf-8") as f:
            seeds = json.load(f)
    except Exception as e:
        logger.error(f"Seed knowledge parse failed | error={e}")
        return 0

    # 清理旧种子（source='seed'），确保内容更新后不产生重复
    conn_ctx = await get_pg_connection()
    if conn_ctx:
        try:
            async with conn_ctx as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        "DELETE FROM knowledge_nodes WHERE source = 'seed'"
                    )
                    deleted = cur.rowcount
                    # 同时清理孤立的边
                    await cur.execute("""
                        DELETE FROM knowledge_edges
                        WHERE source_id NOT IN (SELECT id FROM knowledge_nodes)
                           OR target_id NOT IN (SELECT id FROM knowledge_nodes)
                    """)
                await conn.commit()
                if deleted:
                    logger.info(f"Old seed knowledge cleared | deleted={deleted}")
        except Exception as e:
            logger.warning(f"Failed to clear old seeds | error={e}")

    imported = 0
    for item in seeds:
        node_id = await add_knowledge(
            category=item["category"],
            subcategory=item.get("subcategory"),
            node_type=item.get("node_type", "model"),
            title=item["title"],
            content=item["content"],
            metadata=item.get("metadata"),
            source="seed",
            confidence=item.get("confidence", 1.0),
        )
        if node_id:
            imported += 1

    # 构建种子知识之间的关系边
    await _build_seed_edges(seeds)

    # 清理搜索缓存（种子更新后旧缓存失效）
    invalidate_search_cache()

    logger.info(f"Seed knowledge loaded | total={len(seeds)} | imported={imported}")
    return imported


async def _build_seed_edges(seeds: List[Dict[str, Any]]) -> None:
    """根据种子知识的 metadata.related_models 构建关系边"""
    for item in seeds:
        meta = item.get("metadata", {})
        model_id = meta.get("model_id")
        related_models = meta.get("related_models", [])

        if not model_id and not related_models:
            continue

        # 查找当前节点
        current = await get_node_by_metadata("model_id", model_id) if model_id else None
        if not current:
            continue

        for related_model_id in related_models:
            related = await get_node_by_metadata("model_id", related_model_id)
            if related:
                await graph_service.add_edge(
                    source_id=str(current["id"]),
                    target_id=str(related["id"]),
                    relation_type="related_to",
                )
