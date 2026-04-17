"""
售后部门Agent。

负责：退货/退款查询、售后单处理。
不负责：库存、采购、订单、财务。

设计文档: docs/document/TECH_多Agent单一职责重构.md §8.3
"""
from __future__ import annotations

from typing import Any

from services.agent.department_agent import DepartmentAgent
from services.agent.department_types import ValidationResult
from services.agent.tool_output import OutputStatus, ToolOutput


class AftersaleAgent(DepartmentAgent):
    """售后Agent — 退货/退款/售后单。"""

    FIELD_MAP = {
        "outer_id": "product_code",
        "sku_outer_id": "sku_code",
    }

    allowed_doc_types = ["aftersale"]

    @property
    def domain(self) -> str:
        return "aftersale"

    @property
    def tools(self) -> list[str]:
        return [
            "local_data",
            "erp_aftersales_query",
        ]

    @property
    def system_prompt(self) -> str:
        return (
            "你是售后专家Agent。你负责：\n"
            "- 退货/退款查询（售后单号/时间范围/商品）\n"
            "- 售后统计（退货率/退款金额/按商品/平台分组）\n"
            "- 售后原因分析\n"
            "\n"
            "你不负责：库存、采购、订单、财务\n"
            "\n"
            "参数规则：\n"
            "- 售后查询必须指定：时间范围 或 商品编码 或 售后单号\n"
            "- 时间范围不能超过90天"
        )

    def validate_params(self, action: str, params: dict) -> ValidationResult:
        """售后域参数校验。"""
        if action == "aftersale_list":
            if (not params.get("time_range")
                    and not params.get("product_code")
                    and not params.get("aftersale_no")):
                return ValidationResult.missing([
                    "时间范围 或 商品编码 或 售后单号",
                ])
            tr = params.get("time_range", "")
            if tr:
                result = self._validate_time_range(tr)
                if result is not None:
                    return result

        elif action == "return_rate":
            if not params.get("time_range"):
                return ValidationResult.missing(["时间范围"])
            result = self._validate_time_range(params["time_range"])
            if result is not None:
                return result

        return ValidationResult.ok()

    # ── DAG 分发 ──

    def _classify_action(self, task: str) -> str:
        t = task.lower()
        if any(kw in t for kw in ("退货率", "退款率", "return_rate")):
            return "return_rate"
        return "aftersale_list"

    async def _dispatch(self, action, params, context):
        return await self._query_local_data(
            doc_type="aftersale",
            mode=params.get("mode", "summary"),
            filters=params.get("filters", []),
            group_by=params.get("group_by"),
        )

    # ── 售后域查询方法 ──

    async def query_aftersale(self, **kwargs: Any) -> ToolOutput:
        """售后单查询（走统一查询引擎）。"""
        return await self._query_local_data(
            doc_type="aftersale", **kwargs,
        )
