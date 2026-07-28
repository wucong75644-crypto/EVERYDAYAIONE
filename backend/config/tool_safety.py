"""Fail-closed safety registry for legacy chat tool execution."""

from enum import Enum


class SafetyLevel(str, Enum):
    SAFE = "safe"
    CONFIRM = "confirm"
    DANGEROUS = "dangerous"


_SAFETY_LEVELS: dict[str, SafetyLevel] = {
    "get_conversation_context": SafetyLevel.SAFE,
    "search_knowledge": SafetyLevel.SAFE,
    "erp_api_search": SafetyLevel.SAFE,
    "evidence_search": SafetyLevel.SAFE,
    "evidence_get": SafetyLevel.SAFE,
    "artifact_search": SafetyLevel.SAFE,
    "artifact_get": SafetyLevel.SAFE,
    "artifact_read": SafetyLevel.SAFE,
    "memory_search": SafetyLevel.SAFE,
    "memory_get": SafetyLevel.SAFE,
    "file_search": SafetyLevel.SAFE,
    "social_crawler": SafetyLevel.SAFE,
    **{name: SafetyLevel.SAFE for name in (
        "erp_info_query", "erp_product_query", "erp_trade_query",
        "erp_aftersales_query", "erp_warehouse_query", "erp_purchase_query",
        "erp_taobao_query", "local_data", "local_product_identify",
        "local_stock_query", "local_product_stats", "local_platform_map_query",
        "local_compare_stats", "local_shop_list", "local_warehouse_list",
        "local_supplier_list",
    )},
    "file_analyze": SafetyLevel.CONFIRM,
    "fetch_all_pages": SafetyLevel.CONFIRM,
    "erp_agent": SafetyLevel.CONFIRM,
    "erp_analyze": SafetyLevel.CONFIRM,
    "web_search": SafetyLevel.CONFIRM,
    "generate_image": SafetyLevel.CONFIRM,
    "generate_video": SafetyLevel.CONFIRM,
    "image_agent": SafetyLevel.CONFIRM,
    "code_execute": SafetyLevel.DANGEROUS,
    "erp_execute": SafetyLevel.DANGEROUS,
    "trigger_erp_sync": SafetyLevel.DANGEROUS,
    "file_delete": SafetyLevel.DANGEROUS,
    "restore_file": SafetyLevel.DANGEROUS,
    "manage_scheduled_task": SafetyLevel.DANGEROUS,
}


def get_safety_level(tool_name: str) -> SafetyLevel:
    """Return an explicitly registered safety level or reject the tool."""
    try:
        return _SAFETY_LEVELS[tool_name]
    except KeyError as exc:
        raise ValueError("UNKNOWN_TOOL_SAFETY") from exc


def registered_safety_tools() -> frozenset[str]:
    return frozenset(_SAFETY_LEVELS)
