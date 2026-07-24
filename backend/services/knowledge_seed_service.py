"""Seed knowledge import and graph-edge construction."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from services.graph_service import graph_service
from services.knowledge_config import (
    get_pg_connection,
    invalidate_search_cache,
    is_kb_available,
)


async def load_seed_knowledge(
    seed_file: Optional[str] = None,
    *,
    db_source: Any = None,
) -> int:
    """从 JSON 文件导入种子知识并重建关系边。"""
    if not is_kb_available():
        return 0

    if seed_file is None:
        seed_file = str(
            Path(__file__).parent.parent / "data" / "seed_knowledge.json"
        )
    seed_path = Path(seed_file)
    if not seed_path.exists():
        logger.warning(f"Seed knowledge file not found | path={seed_file}")
        return 0

    try:
        with seed_path.open(encoding="utf-8") as seed_stream:
            seeds = json.load(seed_stream)
    except Exception as exc:
        logger.error(f"Seed knowledge parse failed | error={exc}")
        return 0

    conn_ctx = await get_pg_connection(db_source)
    if conn_ctx:
        try:
            async with conn_ctx as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        "DELETE FROM knowledge_nodes "
                        "WHERE source = 'seed' AND org_id IS NULL"
                    )
                    deleted = cur.rowcount
                    await cur.execute("""
                        DELETE FROM knowledge_edges
                        WHERE source_id NOT IN (SELECT id FROM knowledge_nodes)
                           OR target_id NOT IN (SELECT id FROM knowledge_nodes)
                    """)
                if deleted:
                    logger.info(
                        f"Old seed knowledge cleared | deleted={deleted}"
                    )
        except Exception as exc:
            logger.warning(f"Failed to clear old seeds | error={exc}")

    from services.knowledge_service import add_knowledge

    imported = 0
    for item in seeds:
        node_id = await add_knowledge(
            db_source=db_source,
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

    await _build_seed_edges(seeds, db_source=db_source)
    invalidate_search_cache()
    logger.info(
        f"Seed knowledge loaded | total={len(seeds)} | imported={imported}"
    )
    return imported


async def _build_seed_edges(
    seeds: List[Dict[str, Any]],
    *,
    db_source: Any = None,
) -> None:
    """根据种子知识的 related_models 构建关系边。"""
    from services.knowledge_service import get_node_by_metadata

    for item in seeds:
        meta = item.get("metadata", {})
        model_id = meta.get("model_id")
        related_models = meta.get("related_models", [])
        if not model_id and not related_models:
            continue

        current = (
            await get_node_by_metadata(
                "model_id", model_id, db_source=db_source,
            )
            if model_id else None
        )
        if not current:
            continue

        for related_model_id in related_models:
            related = await get_node_by_metadata(
                "model_id", related_model_id, db_source=db_source,
            )
            if related:
                await graph_service.add_edge(
                    db_source=db_source,
                    source_id=str(current["id"]),
                    target_id=str(related["id"]),
                    relation_type="related_to",
                )
