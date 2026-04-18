"""
订单部门Agent。

负责：订单查询、物流查询、发货管理。
不负责：库存、采购、售后、财务。

设计文档: docs/document/TECH_多Agent单一职责重构.md §8.2
"""
from __future__ import annotations

from typing import Any

from services.agent.department_agent import DepartmentAgent
from services.agent.department_types import ValidationResult
from services.agent.tool_output import OutputStatus, ToolOutput


class TradeAgent(DepartmentAgent):
    """订单Agent — 订单/物流/发货。"""

    FIELD_MAP = {
        "outer_id": "product_code",
        "sku_outer_id": "sku_code",
    }

    allowed_doc_types = ["order"]

    @property
    def domain(self) -> str:
        return "trade"

    @property
    def tools(self) -> list[str]:
        return [
            "local_data",
            "erp_trade_query",
            "erp_taobao_query",
        ]

    @property
    def system_prompt(self) -> str:
        return (
            "你是订单专家Agent。你负责：\n"
            "- 订单查询（订单号/平台/时间范围/状态）\n"
            "- 物流查询（发货状态/快递单号）\n"
            "- 订单统计（按平台/店铺/时间分组）\n"
            "\n"
            "你不负责：库存、采购、售后、财务\n"
            "\n"
            "参数规则：\n"
            "- 订单查询必须指定：订单号 或 (时间范围 + 可选过滤条件)\n"
            "- 时间范围不能超过90天\n"
            "- 平台订单号格式：tb=18位, fxg=19位, jd/kuaishou=16位, "
            "xhs=P+18位, pdd=日期-数字串"
        )

    def validate_params(self, action: str, params: dict) -> ValidationResult:
        """订单域参数校验。"""
        if action == "order_list":
            if (not params.get("order_no")
                    and not params.get("time_range")
                    and not params.get("platform_order_no")):
                return ValidationResult.missing(
                    ["订单号 或 平台订单号 或 时间范围"],
                    prompt="您想查哪个订单？请提供订单号，或者告诉我时间范围。",
                )
            tr = params.get("time_range", "")
            if tr:
                result = self._validate_time_range(tr)
                if result is not None:
                    return result

        elif action == "logistics_query":
            if not params.get("order_no") and not params.get("logistics_no"):
                return ValidationResult.missing(
                    ["订单号 或 物流单号"],
                    prompt="查物流需要订单号或快递单号，请提供其中一个。",
                )

        return ValidationResult.ok()

    # ── DAG 分发 ──

    def _classify_action(self, task: str) -> str:
        t = task.lower()
        if any(kw in t for kw in ("物流", "快递", "签收", "logistics")):
            return "logistics_query"
        return "order_list"

    async def _dispatch(self, action, params, context):
        return await self.query_orders(
            mode=params.get("mode", "summary"),
            filters=params.get("filters", []),
            group_by=params.get("group_by"),
        )

    # ── 订单域查询方法 ──

    async def query_orders(self, **kwargs: Any) -> ToolOutput:
        """订单查询（走统一查询引擎）。"""
        return await self._query_local_data(
            doc_type="order", **kwargs,
        )
