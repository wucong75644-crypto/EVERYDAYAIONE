"""知识库去重与淘汰逻辑

从 knowledge_service.py 拆出，遵守 V2.2 §三 500 行红线。

包含：
- dedup_by_hash: content_hash 精确匹配去重
- dedup_by_vector: 向量相似度去重（>0.9 合并）
- evict_global: 全局节点上限淘汰（kb_max_nodes）
- evict_by_dimension: 通用单维度淘汰（按 category 或 node_type）

所有函数都接收已打开的 cursor 和 conn，不创建新连接。
所有写操作都在函数内自行 commit，调用方无需关心事务边界。
"""

from typing import Any, Dict, Optional

from loguru import logger

from core.config import settings
from services.knowledge_config import invalidate_search_cache


# ============ 去重 ============


async def dedup_by_hash(
    cur, conn, content_hash: str, source: str,
    org_id: Optional[str] = None,
) -> Optional[str]:
    """Hash 完全匹配去重：匹配则更新 confidence + hit_count，返回已有节点 ID。

    seed → auto 命中时不变更 confidence（保留人工 seed 的高 confidence）。
    """
    org_filter = "AND org_id = %(org_id)s" if org_id else "AND org_id IS NULL"
    await cur.execute(
        f"""
        SELECT id, source, confidence FROM knowledge_nodes
        WHERE content_hash = %(hash)s AND is_deleted = FALSE {org_filter};
        """,
        {"hash": content_hash, "org_id": org_id},
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


async def dedup_by_vector(
    cur, conn, *, category: str, embedding: list,
    source: str, title: str, content: str,
    content_hash: str, metadata: Optional[Dict[str, Any]],
    org_id: Optional[str] = None,
) -> Optional[str]:
    """向量相似度 > 0.9 去重：匹配则合并新内容，返回已有节点 ID。

    seed → auto 命中时返回已有节点不更新（保留 seed 不被覆盖）。
    """
    import json

    org_filter = "AND org_id = %(org_id)s" if org_id else "AND org_id IS NULL"
    await cur.execute(
        f"""
        SELECT id, source, confidence
        FROM knowledge_nodes
        WHERE is_deleted = FALSE
            AND category = %(category)s
            AND embedding IS NOT NULL
            AND 1 - (embedding <=> %(emb)s::vector) > 0.9
            {org_filter}
        ORDER BY embedding <=> %(emb)s::vector ASC
        LIMIT 1;
        """,
        {"category": category, "emb": str(embedding), "org_id": org_id},
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


# ============ 淘汰 ============


async def evict_global(cur, max_nodes: int, org_id: Optional[str] = None) -> None:
    """全局节点上限淘汰：count >= max_nodes 时按 confidence 升序软删一条非 seed 节点。

    淘汰按 org 隔离计数（每个 org 独立配额）。
    """
    org_filter = "AND org_id = %(oid)s" if org_id else "AND org_id IS NULL"
    await cur.execute(
        f"SELECT COUNT(*) FROM knowledge_nodes WHERE is_deleted = FALSE {org_filter};",
        {"oid": org_id},
    )
    count_row = await cur.fetchone()
    if not count_row or count_row[0] < max_nodes:
        return

    await cur.execute(
        f"""
        UPDATE knowledge_nodes SET is_deleted = TRUE
        WHERE id = (
            SELECT id FROM knowledge_nodes
            WHERE is_deleted = FALSE AND source != 'seed'
            {org_filter}
            ORDER BY confidence ASC, updated_at ASC
            LIMIT 1
        );
        """,
        {"oid": org_id},
    )


async def evict_by_dimension(
    cur,
    *,
    dimension: str,
    value: str,
    max_count: int,
    org_id: Optional[str] = None,
) -> None:
    """单维度淘汰：当 (dimension=value) 的活跃节点数 >= max_count 时软删最低 confidence 节点。

    Args:
        dimension: 列名，必须是 "category" 或 "node_type" 之一（白名单防注入）
        value: 该列匹配值
        max_count: 该维度允许的最大活跃节点数
        org_id: 按 org 隔离淘汰（None=全局散客）

    淘汰策略：confidence ASC + updated_at ASC（最旧最低质量优先），跳过 seed。
    """
    if dimension not in ("category", "node_type"):
        raise ValueError(
            f"evict_by_dimension: invalid dimension={dimension!r}, "
            f"must be 'category' or 'node_type'"
        )

    org_filter = "AND org_id = %(oid)s" if org_id else "AND org_id IS NULL"
    dim_filter = f"AND {dimension} = %(val)s {org_filter}"

    await cur.execute(
        f"""
        SELECT COUNT(*) FROM knowledge_nodes
        WHERE is_deleted = FALSE {dim_filter};
        """,
        {"val": value, "oid": org_id},
    )
    cnt_row = await cur.fetchone()
    if not cnt_row or cnt_row[0] < max_count:
        return

    await cur.execute(
        f"""
        UPDATE knowledge_nodes SET is_deleted = TRUE
        WHERE id = (
            SELECT id FROM knowledge_nodes
            WHERE is_deleted = FALSE AND source != 'seed'
            {dim_filter}
            ORDER BY confidence ASC, updated_at ASC
            LIMIT 1
        );
        """,
        {"val": value, "oid": org_id},
    )
    logger.debug(
        f"Knowledge evicted | dimension={dimension} | value={value} | "
        f"reason=cap_reached({max_count})"
    )
