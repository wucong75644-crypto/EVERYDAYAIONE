"""
仓储部门Agent。

负责：库存查询、缺货分析、仓库信息、出入库记录。
不负责：采购、订单、售后、财务。

设计文档: docs/document/TECH_多Agent单一职责重构.md §6.2
"""
from __future__ import annotations

from typing import Any

from services.agent.department_agent import DepartmentAgent
from services.agent.department_types import ValidationResult
from services.agent.tool_output import (
    ColumnMeta,
    OutputStatus,
    ToolOutput,
)


class WarehouseAgent(DepartmentAgent):
    """仓储Agent — 库存/仓库/出入库。"""

    # ── 底层字段 → 标准字段映射 ──
    FIELD_MAP = {
        "outer_id": "product_code",
        "sku_outer_id": "sku_code",
    }

    # ── doc_type 白名单（仓储域只能查收货/上架）──
    allowed_doc_types = ["receipt", "shelf"]

    @property
    def domain(self) -> str:
        return "warehouse"

    @property
    def tools(self) -> list[str]:
        return [
            "local_stock_query",
            "local_warehouse_list",
            "local_data",
        ]

    @property
    def system_prompt(self) -> str:
        return (
            "你是仓储专家Agent。你负责：\n"
            "- 库存查询（可售/锁定/在途）\n"
            "- 缺货分析（哪些SKU缺货、缺多少）\n"
            "- 仓库信息查询\n"
            "- 出入库记录查询（入库单/上架单）\n"
            "\n"
            "你不负责：采购、订单、售后、财务\n"
            "\n"
            "参数规则：\n"
            "- 缺货查询必须指定：平台 + 时间范围\n"
            "- 库存查询必须指定：商品编码 或 关键词\n"
            "- 时间范围不能超过90天"
        )

    def validate_params(self, action: str, params: dict) -> ValidationResult:
        """仓储域参数校验。"""
        if action == "stock_query":
            if not params.get("product_code") and not params.get("keyword"):
                return ValidationResult.missing(
                    ["商品编码或关键词"],
                    prompt="您想查哪个商品的库存？请告诉我商品编码或名称。",
                )

        elif action == "shortage_query":
            missing = []
            if not params.get("platform"):
                missing.append("平台")
            if not params.get("time_range"):
                missing.append("时间范围")
            if missing:
                return ValidationResult.missing(
                    missing,
                    prompt=f"查缺货需要知道{'和'.join(missing)}，请补充。",
                )
            # 时间范围格式校验
            tr = params.get("time_range", "")
            if tr:
                result = self._validate_time_range(tr)
                if result is not None:
                    return result

        elif action == "warehouse_list":
            pass  # 无必填参数

        elif action == "receipt_query" or action == "shelf_query":
            if not params.get("time_range") and not params.get("product_code"):
                return ValidationResult.missing(
                    ["时间范围 或 商品编码"],
                    prompt="请告诉我要查哪个时间段的收货/上架记录，或指定商品编码。",
                )

        return ValidationResult.ok()

    # ── DAG 分发 ──

    def _classify_action(self, task: str) -> str:
        t = task.lower()
        if any(kw in t for kw in ("库存", "可售", "缺货", "stock")):
            return "stock_query"
        if any(kw in t for kw in ("仓库", "warehouse")):
            return "warehouse_list"
        if any(kw in t for kw in ("收货", "入库", "receipt")):
            return "receipt_query"
        if any(kw in t for kw in ("上架", "shelf")):
            return "shelf_query"
        return "stock_query"

    async def _dispatch(self, action, params, context):
        if action == "stock_query":
            return await self.query_stock(
                product_code=params.get("product_code", ""),
                context=context,
            )
        if action == "warehouse_list":
            return await self.query_warehouse_list()
        if action in ("receipt_query", "shelf_query"):
            doc_type = "receipt" if action == "receipt_query" else "shelf"
            return await self._query_local_data(
                doc_type=doc_type,
                mode=params.get("mode", "summary"),
                filters=params.get("filters", []),
            )
        return await self.query_stock(
            product_code=params.get("product_code", ""),
            context=context,
        )

    # ── 仓储域专用查询方法 ──

    async def query_stock(
        self,
        product_code: str,
        stock_status: str | None = None,
        low_stock: bool = False,
        context: list[ToolOutput] | None = None,
    ) -> ToolOutput:
        """库存查询。

        如果有上游 context（如售后Agent的退货商品列表），
        自动提取 product_code 列表做批量查询。
        """
        from services.kuaimai.erp_local_query import local_stock_query

        # 从上游 context 提取 product_code（如有）
        if context and not product_code:
            codes = self._extract_field_from_context(context, "product_code")
            if codes:
                product_code = codes[0]

        if not product_code:
            return ToolOutput(
                summary="请提供商品编码",
                source=self.domain,
                status=OutputStatus.ERROR,
                error_message="缺少 product_code",
            )

        return await local_stock_query(
            self.db,
            product_code=product_code,
            stock_status=stock_status,
            low_stock=low_stock,
            org_id=self.org_id,
        )

    async def query_warehouse_list(
        self, is_virtual: bool | None = None,
    ) -> ToolOutput:
        """仓库列表查询。"""
        from services.kuaimai.erp_local_query import local_warehouse_list

        return await local_warehouse_list(
            self.db,
            is_virtual=is_virtual,
            org_id=self.org_id,
        )

    async def query_receipt(self, **kwargs: Any) -> ToolOutput:
        """收货单查询（走统一查询引擎）。"""
        return await self._query_local_data(
            doc_type="receipt", **kwargs,
        )

    async def query_shelf(self, **kwargs: Any) -> ToolOutput:
        """上架单查询（走统一查询引擎）。"""
        return await self._query_local_data(
            doc_type="shelf", **kwargs,
        )
