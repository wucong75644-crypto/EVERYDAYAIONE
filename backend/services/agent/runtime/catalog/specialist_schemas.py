"""Runtime-only schemas for the 23 AR-17.3 specialists."""

from __future__ import annotations


def _object(properties: dict[str, object], required: list[str] | None = None) -> dict[str, object]:
    return {"type": "object", "additionalProperties": False,
            "properties": properties, "required": required or []}


_QUERY = {"action": {"type": "string"}, "params": {"type": "object"},
          "page": {"type": "integer", "minimum": 1},
          "page_size": {"type": "integer", "minimum": 1, "maximum": 100}}
SPECIALIST_TOOL_SCHEMAS: dict[str, dict[str, object]] = {
    **{name: _object(dict(_QUERY), ["action"]) for name in (
        "erp_product_query", "erp_trade_query", "erp_purchase_query",
        "erp_aftersales_query", "erp_warehouse_query", "erp_info_query",
        "erp_taobao_query")},
    "erp_api_search": _object({"query": {"type": "string"}}, ["query"]),
    "web_search": _object({"query": {"type": "string"}, "provider": {"type": "string"}}, ["query"]),
    "social_crawler": _object({"platform": {"type": "string"}, "query": {"type": "string"}}, ["platform", "query"]),
    "local_data": _object({"doc_type": {"type": "string"}, "mode": {"type": "string"}, "filters": {"type": "array"}}),
    "file_analyze": _object({"file_id": {"type": "string"}, "path": {"type": "string"}, "sheet": {"type": "string"}}, ["file_id"]),
    "fetch_all_pages": _object({"tool_name": {"type": "string"}, "action": {"type": "string"}, "params": {"type": "object"}}, ["tool_name", "action"]),
    "generate_image": _object({"prompt": {"type": "string"}, "model": {"type": "string"}}, ["prompt"]),
    "generate_video": _object({"prompt": {"type": "string"}, "duration": {"type": "integer"}}, ["prompt"]),
    "image_agent": _object({"prompt": {"type": "string"}, "product_id": {"type": "string"}}, ["prompt"]),
    "erp_agent": _object({"query": {"type": "string"}, "messages_ref": {"type": "string"}}, ["query"]),
    "erp_analyze": _object({"query": {"type": "string"}, "analysis": {"type": "string"}}, ["query"]),
    "erp_execute": _object({"category": {"type": "string"}, "action": {"type": "string"}, "params": {"type": "object"}}, ["category", "action"]),
    "trigger_erp_sync": _object({"domain": {"type": "string"}, "full": {"type": "boolean"}}, ["domain"]),
    "file_delete": _object({"deleted_file_id": {"type": "string"}, "file_id": {"type": "string"}}),
    "restore_file": _object({"deleted_file_id": {"type": "string"}, "content_hash": {"type": "string"}}),
    "manage_scheduled_task": _object({"operation": {"type": "string"}, "task_id": {"type": "string"}, "state_version": {"type": "integer"}, "payload": {"type": "object"}}, ["operation"]),
}

__all__ = ["SPECIALIST_TOOL_SCHEMAS"]
