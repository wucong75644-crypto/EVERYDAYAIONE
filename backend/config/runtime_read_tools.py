"""Runtime-only schemas for the AR-17.2 read Executor catalog.

These schemas are intentionally separate from legacy ChatHandler tool wiring.
They do not add tools to an EffectiveToolset or a production seed.
"""

from __future__ import annotations

from typing import Any


def _object(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object", "additionalProperties": False,
        "properties": properties, "required": required or [],
    }


RUNTIME_READ_TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "get_conversation_context": _object({"limit": {"type": "integer"}}),
    "search_knowledge": _object({"query": {"type": "string"}}, ["query"]),
    "evidence_search": _object({"query": {"type": "string"}, "limit": {"type": "integer"}}),
    "evidence_get": _object({"artifact_id": {"type": "string"}, "selector": {"type": "string"}, "max_tokens": {"type": "integer"}}, ["artifact_id"]),
    "memory_search": _object({"query": {"type": "string"}, "limit": {"type": "integer"}}, ["query"]),
    "memory_get": _object({"memory_ref": {"type": "string"}}, ["memory_ref"]),
    "artifact_search": _object({"query": {"type": "string"}, "limit": {"type": "integer"}}),
    "artifact_get": _object({"artifact_id": {"type": "string"}}, ["artifact_id"]),
    "artifact_read": _object({"artifact_id": {"type": "string"}, "cursor": {"type": "integer"}, "max_tokens": {"type": "integer"}}, ["artifact_id"]),
    "file_search": _object({"path": {"type": "string"}, "keyword": {"type": "string"}, "file_pattern": {"type": "string"}, "scope": {"type": "string", "enum": ["current", "workspace"]}}),
    "local_product_identify": _object({"code": {"type": "string"}, "name": {"type": "string"}, "spec": {"type": "string"}}),
    "local_stock_query": _object({"product_code": {"type": "string"}}, ["product_code"]),
    "local_product_stats": _object({"product_code": {"type": "string"}}, ["product_code"]),
    "local_platform_map_query": _object({"product_code": {"type": "string"}, "num_iid": {"type": "string"}}),
    "local_compare_stats": _object({"doc_type": {"type": "string"}, "compare_kind": {"type": "string"}, "current_period": {"type": "string"}}, ["doc_type", "compare_kind", "current_period"]),
    "local_shop_list": _object({"platform": {"type": "string"}}),
    "local_warehouse_list": _object({"is_virtual": {"type": "boolean"}}),
    "local_supplier_list": _object({"category": {"type": "string"}, "status": {"type": "integer"}}),
}
