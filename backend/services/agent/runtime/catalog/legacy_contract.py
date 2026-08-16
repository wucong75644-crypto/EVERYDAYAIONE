"""Transitional metadata lookup for the legacy Chat tool surface."""

from __future__ import annotations

from functools import lru_cache
import os
from typing import Any


# This first batch contains tools whose legacy argument names are identical to
# the current Runtime executor contract.  Semantic adapters for renamed or
# expanded arguments are intentionally handled in later batches.
LEGACY_SCHEMA_COMPATIBLE_TOOLS = frozenset({
    "artifact_get", "artifact_read", "artifact_search",
    "code_execute", "evidence_get", "evidence_search",
    "erp_api_search", "file_search", "get_conversation_context",
    "local_product_identify", "memory_get", "memory_search",
    "search_knowledge", "web_search",
})


@lru_cache(maxsize=1)
def legacy_tool_definitions() -> dict[str, dict[str, Any]]:
    """Return the legacy model-facing definitions indexed by tool name."""
    definitions: dict[str, dict[str, Any]] = {}
    from config.artifact_tools import build_artifact_tools
    from config.chat_tools import get_chat_tools
    from config.erp_tools import build_fetch_all_pages_tool
    from config.evidence_tools import build_evidence_tools
    from config.memory_tools import build_memory_tools

    sources = (
        get_chat_tools("__runtime_catalog__"),
        build_artifact_tools(), build_evidence_tools(), build_memory_tools(),
        [build_fetch_all_pages_tool()],
    )
    for tools in sources:
        for item in tools:
            function = item.get("function")
            if not isinstance(function, dict):
                continue
            name = str(function.get("name") or "")
            if not name:
                continue
            if name in definitions and definitions[name] != function:
                raise ValueError(f"RUNTIME_LEGACY_TOOL_DEFINITION_CONFLICT:{name}")
            definitions[name] = dict(function)

    from config.tool_registry import TOOL_REGISTRY

    for name, entry in TOOL_REGISTRY.items():
        if name not in definitions:
            definitions[name] = {"name": name, "description": entry.description}
    return definitions


def legacy_tool_description(tool_name: str) -> str:
    """Return a stable legacy description, or empty for test-only tools."""
    definition = (
        _runtime_safe_definition(tool_name)
        if os.environ.get("AGENT_RUNTIME_PROCESS_ROLE") == "agent_runtime"
        else None
    )
    if definition is None:
        definition = legacy_tool_definitions().get(tool_name)
    return str(definition.get("description") or "") if definition else ""


def legacy_tool_parameters(tool_name: str) -> dict[str, Any] | None:
    """Return the exact legacy parameters for the compatible first batch."""
    if tool_name not in LEGACY_SCHEMA_COMPATIBLE_TOOLS:
        return None
    definition = (
        _runtime_safe_definition(tool_name)
        if os.environ.get("AGENT_RUNTIME_PROCESS_ROLE") == "agent_runtime"
        else None
    )
    if definition is None:
        definition = legacy_tool_definitions().get(tool_name)
    parameters = definition.get("parameters") if definition else None
    return dict(parameters) if isinstance(parameters, dict) else None


@lru_cache(maxsize=1)
def _runtime_safe_definitions() -> dict[str, dict[str, Any]]:
    """Read Runtime's built-in legacy definition without app wiring.

    The Runtime worker is intentionally denied the application ``.env``.
    ``legacy_tool_definitions`` remains the compatibility path for the
    full ChatHandler surface, but importing it also imports provider
    registries that require application Settings.  Runtime composition uses
    only pure tool-definition builders plus the frozen descriptions below;
    the full legacy path stays lazy for the application process.
    """
    from config.artifact_tools import build_artifact_tools
    from config.code_tools import build_code_tools
    from config.common_tools import build_common_tools
    from config.crawler_tools import build_crawler_tools
    from config.erp_local_tools import build_local_tools
    from config.evidence_tools import build_evidence_tools
    from config.file_tools import build_file_tools
    from config.memory_tools import build_memory_tools
    from config.tool_registry import TOOL_REGISTRY

    definitions: dict[str, dict[str, Any]] = {}
    builders = (
        build_artifact_tools, build_code_tools, build_common_tools,
        build_crawler_tools, build_local_tools, build_evidence_tools,
        build_file_tools, build_memory_tools,
    )
    for builder in builders:
        items = builder(include_workspace=True) if builder is build_code_tools else builder()
        for item in items:
            function = item.get("function")
            if isinstance(function, dict) and function.get("name"):
                definitions[str(function["name"])] = dict(function)

    definitions["fetch_all_pages"] = {
        "name": "fetch_all_pages",
        "description": (
            "全量翻页工具。包装任意 erp_* 远程查询工具，自动翻页拉取全部数据。"
            "适合：导出Excel、全量数据分析、跨数据源关联等需要完整数据的场景。"
            "结果自动存为 staging 文件，返回文件路径。"
            "配合 code_execute 使用：先用本工具拿全量数据，"
            "再用 code_execute 的 read_file 读取并计算/导出。"
            "⚠ 翻页耗时较长（100条/页，每页约1秒），"
            "请根据预估数据量合理设置 max_pages。"
            "⚠ 使用前需先通过 erp_* 工具的两步协议确认参数格式。"
        ),
    }
    for name, description in _RUNTIME_SAFE_REMOTE_DESCRIPTIONS.items():
        definitions[name] = {"name": name, "description": description}
    for name, entry in TOOL_REGISTRY.items():
        definitions.setdefault(
            name, {"name": name, "description": entry.description},
        )
    return definitions


def _runtime_safe_definition(tool_name: str) -> dict[str, Any] | None:
    return _runtime_safe_definitions().get(tool_name)


_RUNTIME_SAFE_REMOTE_DESCRIPTIONS = {
    "erp_info_query": "远程API查询ERP基础信息：仓库、店铺、标签、客户、分销商。",
    "erp_product_query": "远程API查询ERP商品/SKU/库存/标签/分类/品牌信息。适合本地工具不支持的字段或需要实时数据。",
    "erp_trade_query": "远程API查询ERP订单/出库/物流/波次/唯一码信息。适合本地工具不支持的操作或需要实时数据。",
    "erp_aftersales_query": "远程API查询ERP售后工单/退货/维修单/补款/日志。适合本地工具不支持的操作。",
    "erp_warehouse_query": "远程API查询ERP调拨/入出库/盘点/下架/货位/加工单信息。仓储操作无本地工具，必须使用此远程API。",
    "erp_purchase_query": "远程API查询ERP供应商/采购单/收货单/采退单/上架单/采购建议。适合本地工具不支持的操作。",
    "erp_taobao_query": "远程API查询淘宝/天猫平台的订单和售后单（通过奇门接口）。返回平台原始数据 {total, trades/workOrders[]}。page_size最小20。支持 shop_id 按店铺筛选。",
}


__all__ = [
    "LEGACY_SCHEMA_COMPATIBLE_TOOLS", "legacy_tool_definitions",
    "legacy_tool_description", "legacy_tool_parameters",
]
