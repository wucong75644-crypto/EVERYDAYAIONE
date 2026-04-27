"""
仓储部门Agent。

负责：库存查询、缺货分析、仓库信息、出入库记录、商品/SKU主数据、
      日统计报表、平台映射、批次效期库存。
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

    # ── doc_type 白名单（仓储域全量：单据 + 新增8表中归属仓储的6个）──
    allowed_doc_types = [
        "receipt", "shelf",
        "stock", "batch_stock",
        "product", "sku", "daily_stats", "platform_map",
    ]

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
            "- 库存查询（可售/锁定/在途/缺货/库存负数）\n"
            "- 商品主数据查询（停售/虚拟/品牌/分类）\n"
            "- SKU 明细查询（规格/条码/价格）\n"
            "- 商品日统计报表（销量/采购量/售后量）\n"
            "- 平台映射查询（商品在哪些平台售卖）\n"
            "- 批次效期库存查询（快过期/批次号）\n"
            "- 仓库信息查询\n"
            "- 出入库记录查询（入库单/上架单）\n"
            "\n"
            "你不负责：采购、订单、售后、财务\n"
            "\n"
            "参数规则：\n"
            "- 缺货查询必须指定：平台 + 时间范围\n"
            "- 库存查询必须指定：商品编码 或 关键词\n"
            "- 日统计查询必须指定：时间范围\n"
            "- 时间范围不能超过90天"
        )

    def validate_params(self, action: str, params: dict) -> ValidationResult:
        """仓储域参数校验。"""
        # v2.2：分析类查询走引擎内部路由，不需要传统参数校验
        if params.get("query_type") in self._ANALYTICS_QUERY_TYPES:
            return ValidationResult.ok()

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
            has_identifier = (
                params.get("time_range") or params.get("product_code")
                or params.get("item_name") or params.get("warehouse_name")
                or params.get("supplier_name") or params.get("doc_code")
            )
            if not has_identifier:
                return ValidationResult.missing(
                    ["时间范围 或 商品编码 或 仓库名"],
                    prompt="请告诉我要查哪个时间段的收货/上架记录，或指定商品编码、仓库。",
                )

        return ValidationResult.ok()

    # ── DAG 分发 ──

    # action → doc_type 统一映射（_dispatch 读取，不再 if/elif 硬编码）
    _DOC_TYPE_ACTION_MAP = {
        "shelf": "shelf_query",
        "receipt": "receipt_query",
        "stock": "stock_data_query",
        "batch_stock": "batch_stock_query",
        "product": "product_query",
        "sku": "sku_query",
        "daily_stats": "daily_stats_query",
        "platform_map": "platform_map_query",
    }

    # action → 实际 doc_type（_dispatch 统一查表）
    _ACTION_TO_DOC_TYPE: dict[str, str] = {
        "receipt_query": "receipt",
        "shelf_query": "shelf",
        "stock_data_query": "stock",
        "batch_stock_query": "batch_stock",
        "product_query": "product",
        "sku_query": "sku",
        "daily_stats_query": "daily_stats",
        "platform_map_query": "platform_map",
    }

    def _classify_action(self, task: str) -> str:
        t = task.lower()
        # 先匹配具体场景，再匹配宽泛关键词（避免"库存"吃掉"批次库存"）
        if any(kw in t for kw in ("批次", "效期", "过期", "保质期", "batch")):
            return "batch_stock_query"
        if any(kw in t for kw in ("平台映射", "哪些平台", "在售平台", "平台商品",
                                    "platform_map")):
            return "platform_map_query"
        if any(kw in t for kw in ("日统计", "销量排名", "销量top", "退货率",
                                    "daily_stats", "商品销量")):
            return "daily_stats_query"
        if any(kw in t for kw in ("sku", "规格", "变体")):
            return "sku_query"
        if any(kw in t for kw in ("商品主数据", "停售商品", "虚拟商品", "商品列表",
                                    "品牌", "商品分类", "商品信息")):
            return "product_query"
        if any(kw in t for kw in ("库存负数", "库存统计", "可用库存", "总库存",
                                    "库存预警", "缺货商品")):
            return "stock_data_query"
        if any(kw in t for kw in ("库存", "可售", "缺货", "stock")):
            return "stock_query"
        if any(kw in t for kw in ("仓库", "warehouse")):
            return "warehouse_list"
        if any(kw in t for kw in ("收货", "入库", "receipt")):
            return "receipt_query"
        if any(kw in t for kw in ("上架", "shelf")):
            return "shelf_query"
        return "stock_query"

    # v2.2 分析类查询类型——直接走引擎路由，不需要 action 映射
    _ANALYTICS_QUERY_TYPES = frozenset({
        "trend", "compare", "ratio", "cross", "alert", "distribution",
    })

    async def _dispatch(self, action: str, params: dict, context: Any) -> ToolOutput:
        # ── v2.2：分析类查询直接走 _query_local_data（引擎内部路由） ──
        # 显式 query_type 或隐式分析参数（alert_type/time_granularity/compare_range）
        query_type = params.get("query_type")
        has_analytics_hint = (
            params.get("alert_type")
            or params.get("time_granularity")
            or params.get("compare_range")
        )
        if query_type in self._ANALYTICS_QUERY_TYPES or has_analytics_hint:
            doc_type = params.get("doc_type") or "daily_stats"
            return await self._query_local_data(
                doc_type=doc_type, **self._query_kwargs(params),
            )

        # 专用方法：stock_query（按编码查库存）和 warehouse_list
        if action == "stock_query":
            return await self.query_stock(
                product_code=params.get("product_code", ""),
                context=context,
            )
        if action == "warehouse_list":
            return await self.query_warehouse_list()

        # 统一查询引擎路由：从 _ACTION_TO_DOC_TYPE 查表，一处维护
        doc_type = self._ACTION_TO_DOC_TYPE.get(action)
        if doc_type:
            return await self._query_local_data(
                doc_type=doc_type, **self._query_kwargs(params),
            )

        # 兜底
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
