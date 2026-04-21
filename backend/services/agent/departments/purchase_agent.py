"""
采购部门Agent。

负责：采购单查询、到货进度、供应商查询、采退单。
不负责：库存、订单、售后、财务。

设计文档: docs/document/TECH_多Agent单一职责重构.md §8.1
"""
from __future__ import annotations

from typing import Any

from services.agent.department_agent import DepartmentAgent
from services.agent.department_types import ValidationResult
from services.agent.tool_output import OutputStatus, ToolOutput


class PurchaseAgent(DepartmentAgent):
    """采购Agent — 采购单/供应商/采退。"""

    FIELD_MAP = {
        "outer_id": "product_code",
        "sku_outer_id": "sku_code",
    }

    allowed_doc_types = ["purchase", "purchase_return"]

    @property
    def domain(self) -> str:
        return "purchase"

    @property
    def tools(self) -> list[str]:
        return [
            "local_data",
            "erp_purchase_query",
            "local_supplier_list",
        ]

    @property
    def system_prompt(self) -> str:
        return (
            "你是采购专家Agent。你负责：\n"
            "- 采购单查询（采购单号/供应商/到货进度）\n"
            "- 供应商查询\n"
            "- 采退单查询\n"
            "- 到货进度跟踪（SKU到货率、预计到货时间）\n"
            "\n"
            "你不负责：库存、订单、售后、财务\n"
            "\n"
            "参数规则：\n"
            "- 到货进度查询必须指定：SKU列表 或 采购单号\n"
            "- 供应商查询必须指定：供应商名称 或 ID\n"
            "- 时间范围不能超过90天"
        )

    def validate_params(self, action: str, params: dict) -> ValidationResult:
        """采购域参数校验。"""
        if action == "arrival_progress":
            if not params.get("sku_list") and not params.get("po_no"):
                return ValidationResult.missing(
                    ["SKU列表 或 采购单号"],
                    prompt="查到货进度需要提供 SKU 编码或采购单号，请补充。",
                )

        elif action == "supplier_query":
            if not params.get("supplier_name") and not params.get("supplier_id"):
                return ValidationResult.missing(
                    ["供应商名称 或 ID"],
                    prompt="请告诉我要查哪个供应商？提供名称或编号即可。",
                )

        elif action == "purchase_list":
            if not params.get("time_range") and not params.get("po_no"):
                return ValidationResult.missing(
                    ["时间范围 或 采购单号"],
                    prompt="请告诉我要查哪个时间段的采购单，或提供具体采购单号。",
                )
            tr = params.get("time_range", "")
            if tr:
                result = self._validate_time_range(tr)
                if result is not None:
                    return result

        elif action == "purchase_return":
            if not params.get("time_range") and not params.get("product_code"):
                return ValidationResult.missing(
                    ["时间范围 或 商品编码"],
                    prompt="查采购退货需要时间范围或商品编码，请补充。",
                )

        return ValidationResult.ok()

    # ── DAG 分发 ──

    def _classify_action(self, task: str) -> str:
        t = task.lower()
        if any(kw in t for kw in ("到货", "进度", "arrival")):
            return "arrival_progress"
        if any(kw in t for kw in ("供应商", "supplier")):
            return "supplier_query"
        if any(kw in t for kw in ("采退", "purchase_return")):
            return "purchase_return"
        return "purchase_list"

    async def _dispatch(self, action, params, context):
        doc_type = params.get("doc_type", "purchase")
        if action == "purchase_return":
            doc_type = "purchase_return"
        return await self._query_local_data(
            doc_type=doc_type,
            mode=params.get("mode", "summary"),
            filters=params.get("filters", []),
            group_by=params.get("group_by"),
            include_invalid=params.get("include_invalid", False),
        )

    # ── 采购域查询方法 ──

    async def query_purchase(self, **kwargs: Any) -> ToolOutput:
        """采购单查询（走统一查询引擎）。"""
        return await self._query_local_data(
            doc_type="purchase", **kwargs,
        )

    async def query_purchase_return(self, **kwargs: Any) -> ToolOutput:
        """采退单查询（走统一查询引擎）。"""
        return await self._query_local_data(
            doc_type="purchase_return", **kwargs,
        )
