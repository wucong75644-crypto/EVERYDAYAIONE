"""
知识库服务

提供知识 CRUD、向量检索、种子知识导入。
采用 psycopg 直连 PostgreSQL + pgvector。

去重 / 淘汰逻辑见 knowledge_dedup.py（V2.2 §三 拆分）。
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
from services.knowledge_dedup import (
    dedup_by_hash,
    dedup_by_vector,
    evict_by_dimension,
    evict_global,
)
# 向后兼容：record_metric 已移至 knowledge_metrics.py
from services.knowledge_metrics import record_metric  # noqa: F401


# ===== Schema 白名单（与 PG CHECK 约束对齐 + node_type 应用层约束） =====
#
# category / source：与 023_add_knowledge_base.sql 的 PG CHECK 约束严格对齐。
# 修改时必须同步更新 migration（PG 层是真实白名单，这里只是早期拦截）。
#
# node_type：PG schema 没有 CHECK 约束，但应用层在此收敛命名空间，
# 防止字段被滥用为"什么都往里塞"的杂物间。新增取值需在此追加。
_VALID_CATEGORIES = frozenset({"model", "tool", "experience"})
_VALID_SOURCES = frozenset({"auto", "seed", "manual", "aggregated"})
_VALID_NODE_TYPES = frozenset({
    # seed / extractor 既有取值
    "model", "parameter", "pattern", "capability",
    # 业务模块既有取值
    "performance",        # model_scorer
    # ERPAgent 自学习经验（2026-04-11 方案 C 引入，
    # 替代非法的 category="routing"/"failure"）
    "routing_pattern",
    "failure_pattern",
})


# ===== 知识 CRUD =====


def _validate_node_schema(
    category: str, node_type: str, source: str, title: str,
) -> None:
    """入口校验：category / node_type / source 必须在白名单。

    在 PG CHECK 前拦截，把 schema 违反提到最早可见的位置。
    日志包含完整上下文方便运维 grep。

    Raises:
        ValueError: 任一字段不在白名单
    """
    if category not in _VALID_CATEGORIES:
        logger.error(
            f"Knowledge schema violation | category={category!r} | "
            f"node_type={node_type!r} | source={source!r} | "
            f"title={title[:50]} | "
            f"valid_categories={sorted(_VALID_CATEGORIES)}"
        )
        raise ValueError(
            f"add_knowledge: invalid category={category!r}, "
            f"must be one of {sorted(_VALID_CATEGORIES)}"
        )
    if node_type not in _VALID_NODE_TYPES:
        logger.error(
            f"Knowledge schema violation | category={category} | "
            f"node_type={node_type!r} | source={source!r} | "
            f"title={title[:50]} | "
            f"valid_node_types={sorted(_VALID_NODE_TYPES)}"
        )
        raise ValueError(
            f"add_knowledge: invalid node_type={node_type!r}, "
            f"must be one of {sorted(_VALID_NODE_TYPES)}"
        )
    if source not in _VALID_SOURCES:
        logger.error(
            f"Knowledge schema violation | category={category} | "
            f"node_type={node_type} | source={source!r} | "
            f"title={title[:50]} | "
            f"valid_sources={sorted(_VALID_SOURCES)}"
        )
        raise ValueError(
            f"add_knowledge: invalid source={source!r}, "
            f"must be one of {sorted(_VALID_SOURCES)}"
        )


async def _insert_node_row(
    cur, *, category: str, subcategory: Optional[str], node_type: str,
    title: str, content: str, metadata: Optional[Dict[str, Any]],
    embedding: Optional[list], source: str, confidence: float,
    scope: str, content_hash: str, org_id: Optional[str],
) -> Optional[str]:
    """执行 INSERT 并返回新节点 ID（不 commit，由调用方负责）"""
    emb_value = str(embedding) if embedding else None
    await cur.execute(
        """
        INSERT INTO knowledge_nodes (
            category, subcategory, node_type, title, content,
            metadata, embedding, source, confidence, scope,
            content_hash, org_id
        ) VALUES (
            %(category)s, %(subcategory)s, %(node_type)s, %(title)s,
            %(content)s, %(metadata)s, %(embedding)s::vector, %(source)s,
            %(confidence)s, %(scope)s, %(hash)s, %(org_id)s
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
            "org_id": org_id,
        },
    )
    result = await cur.fetchone()
    return str(result[0]) if result else None


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
    org_id: Optional[str] = None,
    max_per_category: Optional[int] = None,
    max_per_node_type: Optional[int] = None,
) -> Optional[str]:
    """
    添加知识条目（含入口校验 + 去重 + 多维度淘汰 + 向量化）

    流程：入口校验 → hash 去重 → 向量去重 → 多维度淘汰 → INSERT。

    Args:
        max_per_category: 该 category 最多保留多少条活跃节点（None=不限）
        max_per_node_type: 该 node_type 最多保留多少条活跃节点（None=不限）
            两个参数互相独立，共存时谁先触发谁淘汰。

    Raises:
        ValueError: category 或 node_type 不在白名单（schema 违反）

    Returns:
        节点 ID（新增或已有），None 表示 DB 写入失败
    """
    _validate_node_schema(category, node_type, source, title)

    if not is_kb_available():
        return None

    conn_ctx = await get_pg_connection()
    if conn_ctx is None:
        return None

    content_hash = compute_content_hash(category, title, content)

    try:
        async with conn_ctx as conn:
            async with conn.cursor() as cur:
                # Hash 去重
                dup_id = await dedup_by_hash(
                    cur, conn, content_hash, source, org_id=org_id,
                )
                if dup_id:
                    return dup_id

                # 向量去重
                embedding = await compute_embedding(f"{title} {content}")
                if embedding:
                    dup_id = await dedup_by_vector(
                        cur, conn,
                        category=category, embedding=embedding,
                        source=source, title=title, content=content,
                        content_hash=content_hash, metadata=metadata,
                        org_id=org_id,
                    )
                    if dup_id:
                        return dup_id

                # 多维度淘汰（global → per-category → per-node_type）
                await evict_global(cur, settings.kb_max_nodes, org_id=org_id)
                if max_per_category:
                    await evict_by_dimension(
                        cur, dimension="category", value=category,
                        max_count=max_per_category, org_id=org_id,
                    )
                if max_per_node_type:
                    await evict_by_dimension(
                        cur, dimension="node_type", value=node_type,
                        max_count=max_per_node_type, org_id=org_id,
                    )

                # INSERT
                node_id = await _insert_node_row(
                    cur, category=category, subcategory=subcategory,
                    node_type=node_type, title=title, content=content,
                    metadata=metadata, embedding=embedding, source=source,
                    confidence=confidence, scope=scope,
                    content_hash=content_hash, org_id=org_id,
                )
                await conn.commit()
                invalidate_search_cache()
                logger.info(
                    f"Knowledge added | id={node_id} | category={category} | "
                    f"node_type={node_type} | title={title[:50]}"
                )
                return node_id

    except ValueError:
        # 入口校验失败 — 必须穿透 try，让调用方明确感知 schema 违反
        raise
    except Exception as e:
        logger.error(
            f"Knowledge add failed | category={category} | node_type={node_type} | "
            f"title={title[:50]} | error={e}"
        )
        return None


async def search_relevant(
    query: str,
    limit: Optional[int] = None,
    threshold: Optional[float] = None,
    category: Optional[str] = None,
    node_type: Optional[str] = None,
    min_confidence: Optional[float] = None,
    scope: str = "global",
    org_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    向量检索相关知识（用于路由注入）

    排序：similarity * 0.7 + confidence * 0.3 DESC（加权防止低质量经验
    在召回结果里淹没人工高 confidence 的 seed 知识）。

    Args:
        category: 可选，限定到某个 category（model/tool/experience）
        node_type: 可选，限定到某个 node_type（如 routing_pattern）
        min_confidence: 可选，过滤掉 confidence < 该值的节点；
            ERPAgent 自学习经验默认 0.5/0.6 起步，传 0.6 可以屏蔽未被命中过的初始经验

    Returns:
        格式化的知识列表，按加权得分降序
    """
    if not is_kb_available():
        return []

    limit = limit or settings.kb_search_limit
    threshold = threshold or settings.kb_search_threshold

    # 缓存检查（含 org_id + node_type + min_confidence 隔离）
    cache_key = (
        f"{query[:100]}|{category}|{node_type}|{min_confidence}|"
        f"{scope}|{org_id or 'global'}|{limit}"
    )
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
    node_type_filter = "AND node_type = %(node_type)s" if node_type else ""
    min_conf_filter = "AND confidence >= %(min_conf)s" if min_confidence is not None else ""
    # 企业用户看到：系统知识(org_id IS NULL) + 本企业知识
    # 散客看到：系统知识(org_id IS NULL)
    org_filter = "AND (org_id = %(org_id)s OR org_id IS NULL)" if org_id else "AND org_id IS NULL"
    params: Dict[str, Any] = {
        "emb": str(embedding),
        "threshold": threshold,
        "limit": limit,
        "scope": scope,
        "org_id": org_id,
    }
    if category:
        params["category"] = category
    if node_type:
        params["node_type"] = node_type
    if min_confidence is not None:
        params["min_conf"] = min_confidence

    query_sql = f"""
    SELECT id, category, subcategory, node_type, title, content,
           confidence, hit_count, source, metadata,
           1 - (embedding <=> %(emb)s::vector) AS similarity
    FROM knowledge_nodes
    WHERE is_deleted = FALSE
        AND embedding IS NOT NULL
        AND (scope = %(scope)s OR scope = 'global')
        {org_filter}
        {category_filter}
        {node_type_filter}
        {min_conf_filter}
        AND 1 - (embedding <=> %(emb)s::vector) > %(threshold)s
    ORDER BY (1 - (embedding <=> %(emb)s::vector)) * 0.7 + confidence * 0.3 DESC
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
    org_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """根据 metadata 字段查找节点（用于查找模型/工具实体节点）"""
    if not is_kb_available():
        return None

    conn_ctx = await get_pg_connection()
    if conn_ctx is None:
        return None

    category_filter = "AND category = %(category)s" if category else ""
    org_filter = "AND (org_id = %(org_id)s OR org_id IS NULL)" if org_id else "AND org_id IS NULL"
    params: Dict[str, Any] = {"key": key, "value": value, "org_id": org_id}
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
                        {org_filter}
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
                        "DELETE FROM knowledge_nodes WHERE source = 'seed' AND org_id IS NULL"
                    )
                    deleted = cur.rowcount
                    # 清理孤立边（两端节点已不存在的边，全局清理）
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
