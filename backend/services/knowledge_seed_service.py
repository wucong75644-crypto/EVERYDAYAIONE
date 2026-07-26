"""Seed knowledge import through the Worker capability facade."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from services.knowledge_config import (
    get_pg_connection,
    invalidate_search_cache,
    is_kb_available,
)


_VALID_CATEGORIES = frozenset({"model", "tool", "experience"})
_VALID_NODE_TYPES = frozenset({
    "model", "parameter", "pattern", "capability", "performance",
    "routing_pattern", "failure_pattern",
})
_SEED_NODE_FIELDS = frozenset({
    "category", "subcategory", "node_type", "title", "content",
    "metadata", "source", "confidence",
})


def _require_seed(condition: bool, error_code: str) -> None:
    if not condition:
        raise ValueError(error_code)


def _normalize_seed_node(
    index: int,
    item: Any,
) -> tuple[Dict[str, Any], str | None]:
    _require_seed(isinstance(item, dict), "SEED_NODE_INVALID")
    _require_seed(not set(item) - _SEED_NODE_FIELDS, "SEED_NODE_FIELD_INVALID")
    category = item.get("category")
    node_type = item.get("node_type", "model")
    title = item.get("title")
    content = item.get("content")
    metadata = item.get("metadata", {})
    confidence = item.get("confidence", 1.0)
    subcategory = item.get("subcategory")
    _require_seed(category in _VALID_CATEGORIES, "SEED_NODE_INVALID")
    _require_seed(node_type in _VALID_NODE_TYPES, "SEED_NODE_INVALID")
    _require_seed(isinstance(title, str), "SEED_NODE_INVALID")
    _require_seed(1 <= len(title) <= 100, "SEED_NODE_INVALID")
    _require_seed(isinstance(content, str), "SEED_NODE_INVALID")
    _require_seed(1 <= len(content) <= 1000, "SEED_NODE_INVALID")
    _require_seed(isinstance(metadata, dict), "SEED_NODE_INVALID")
    _require_seed(
        isinstance(confidence, (int, float)) and not isinstance(confidence, bool),
        "SEED_NODE_INVALID",
    )
    _require_seed(0 <= confidence <= 1, "SEED_NODE_INVALID")
    _require_seed(
        subcategory is None or isinstance(subcategory, str),
        "SEED_NODE_INVALID",
    )
    _require_seed(item.get("source", "seed") == "seed", "SEED_NODE_INVALID")
    model_id = metadata.get("model_id")
    _require_seed(
        model_id is None or isinstance(model_id, str),
        "SEED_MODEL_ID_INVALID",
    )
    _require_seed(model_id is None or bool(model_id), "SEED_MODEL_ID_INVALID")
    return ({
        "seed_key": f"node:{index}",
        "category": category,
        "subcategory": subcategory,
        "node_type": node_type,
        "title": title,
        "content": content,
        "metadata": metadata,
        "confidence": float(confidence),
    }, model_id)


def _build_seed_payload(seeds: Any) -> Dict[str, Any]:
    """Validate the seed file and build the restricted RPC payload."""
    _require_seed(isinstance(seeds, list), "SEED_SCHEMA_INVALID")

    nodes: List[Dict[str, Any]] = []
    model_keys: Dict[str, str] = {}
    for index, item in enumerate(seeds):
        node, model_id = _normalize_seed_node(index, item)
        if model_id is not None:
            _require_seed(
                model_id not in model_keys,
                "SEED_MODEL_ID_DUPLICATE",
            )
            model_keys[model_id] = node["seed_key"]
        nodes.append(node)

    edges: List[Dict[str, str]] = []
    edge_keys: set[tuple[str, str, str]] = set()
    for index, item in enumerate(seeds):
        related_models = item.get("metadata", {}).get("related_models", [])
        _require_seed(
            isinstance(related_models, list),
            "SEED_RELATED_MODELS_INVALID",
        )
        _require_seed(
            all(
                isinstance(model_id, str) and bool(model_id)
                for model_id in related_models
            ),
            "SEED_RELATED_MODELS_INVALID",
        )
        for model_id in related_models:
            target_key = model_keys.get(model_id)
            _require_seed(target_key is not None, "SEED_EDGE_TARGET_MISSING")
            edge_key = (f"node:{index}", target_key, "related_to")
            _require_seed(edge_key not in edge_keys, "SEED_EDGE_DUPLICATE")
            edge_keys.add(edge_key)
            edges.append({
                "source_key": edge_key[0],
                "target_key": edge_key[1],
                "relation_type": edge_key[2],
            })
    return {"version": 1, "nodes": nodes, "edges": edges}


async def load_seed_knowledge(
    seed_file: Optional[str] = None,
    *,
    db_source: Any = None,
) -> int:
    """Atomically replace the global seed snapshot through the Worker RPC."""
    if not is_kb_available():
        return 0

    seed_path = Path(seed_file) if seed_file else (
        Path(__file__).parent.parent / "data" / "seed_knowledge.json"
    )
    if not seed_path.exists():
        logger.warning(f"Seed knowledge file not found | path={seed_path}")
        return 0
    try:
        with seed_path.open(encoding="utf-8") as seed_stream:
            payload = _build_seed_payload(json.load(seed_stream))
    except (OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
        logger.error(
            "Seed knowledge validation failed | "
            f"error_type={type(exc).__name__}"
        )
        return 0

    conn_ctx = await get_pg_connection(db_source)
    if conn_ctx is None:
        return 0
    try:
        async with conn_ctx as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT worker_replace_global_knowledge_seed(%s::jsonb)",
                    (json.dumps(payload, ensure_ascii=False),),
                )
                row = await cur.fetchone()
        result = row[0] if row else None
        if not isinstance(result, dict):
            raise RuntimeError("SEED_RPC_RESULT_INVALID")
        imported = result.get("imported_count")
        if not isinstance(imported, int) or isinstance(imported, bool):
            raise RuntimeError("SEED_RPC_RESULT_INVALID")
    except Exception as exc:
        logger.warning(
            "Seed knowledge import failed | "
            f"error_type={type(exc).__name__}"
        )
        return 0

    invalidate_search_cache()
    logger.info(
        f"Seed knowledge loaded | total={len(payload['nodes'])} | "
        f"imported={imported}"
    )
    return imported
