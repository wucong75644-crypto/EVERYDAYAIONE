"""Narrow Agent Runtime RPC adapters for local ERP reads."""

from __future__ import annotations

from typing import Mapping

from services.agent.runtime.executors.contracts import ActionSnapshot
from services.agent.runtime.executors.real_base import (
    RealReadCapability, bounded_limit, optional_text, read_rpc, required_text,
)


class ErpLocalReadCapability(RealReadCapability):
    def __init__(self, resources, tool_name: str) -> None:
        super().__init__(resources)
        self._tool_name = tool_name

    async def _read_bound(self, snapshot: ActionSnapshot, request: Mapping[str, object]):
        dispatch = {
            "local_product_identify": self._product,
            "local_stock_query": self._stock,
            "local_product_stats": self._stats,
            "local_platform_map_query": self._platform,
            "local_compare_stats": self._compare,
            "local_shop_list": self._shops,
            "local_warehouse_list": self._warehouses,
            "local_supplier_list": self._suppliers,
        }
        handler = dispatch.get(self._tool_name)
        if handler is None:
            raise ValueError("READ_ERP_TOOL_INVALID")
        return await handler(snapshot, request)

    async def _product(self, snapshot, request):
        return await read_rpc(
            self.resources.database, "read_agent_runtime_erp", snapshot,
            request, p_operation="product", p_code=optional_text(request, "code", max_len=120),
            p_name=optional_text(request, "name", max_len=120),
            p_spec=optional_text(request, "spec", max_len=120),
        )

    async def _stock(self, snapshot, request):
        return await read_rpc(
            self.resources.database, "read_agent_runtime_erp", snapshot,
            request, p_operation="stock", p_code=None, p_product_code=required_text(request, "product_code", max_len=120),
        )

    async def _stats(self, snapshot, request):
        return await read_rpc(
            self.resources.database, "read_agent_runtime_erp", snapshot,
            request, p_operation="stats", p_code=None, p_product_code=required_text(request, "product_code", max_len=120),
            p_start_date=optional_text(request, "start_date", max_len=20),
            p_end_date=optional_text(request, "end_date", max_len=20),
        )

    async def _platform(self, snapshot, request):
        return await read_rpc(
            self.resources.database, "read_agent_runtime_erp", snapshot,
            request, p_operation="platform", p_code=optional_text(request, "product_code", max_len=120), p_product_code=None,
            p_num_iid=optional_text(request, "num_iid", max_len=120),
        )

    async def _compare(self, snapshot, request):
        return await read_rpc(
            self.resources.database, "read_agent_runtime_erp", snapshot,
            request, p_operation="compare", p_code=None, p_product_code=None, p_doc_type=required_text(request, "doc_type", max_len=30),
            p_compare_kind=required_text(request, "compare_kind", max_len=30),
            p_current_period=required_text(request, "current_period", max_len=30),
            p_shop_name=optional_text(request, "shop_name", max_len=120),
            p_platform=optional_text(request, "platform", max_len=40),
            p_supplier_name=optional_text(request, "supplier_name", max_len=120),
            p_warehouse_name=optional_text(request, "warehouse_name", max_len=120),
        )

    async def _shops(self, snapshot, request):
        return await read_rpc(
            self.resources.database, "read_agent_runtime_erp", snapshot,
            request, p_operation="shops", p_code=None, p_product_code=None, p_doc_type=None, p_platform=optional_text(request, "platform", max_len=40),
        )

    async def _warehouses(self, snapshot, request):
        value = request.get("is_virtual")
        if value is not None and not isinstance(value, bool):
            raise ValueError("READ_IS_VIRTUAL_INVALID")
        return await read_rpc(
            self.resources.database, "read_agent_runtime_erp", snapshot,
            request, p_operation="warehouses", p_code=None, p_product_code=None, p_doc_type=None, p_platform=None, p_is_virtual=value,
        )

    async def _suppliers(self, snapshot, request):
        status = request.get("status")
        if status is not None and (isinstance(status, bool) or not isinstance(status, int)):
            raise ValueError("READ_STATUS_INVALID")
        return await read_rpc(
            self.resources.database, "read_agent_runtime_erp", snapshot,
            request, p_operation="suppliers", p_code=None, p_product_code=None, p_doc_type=None, p_platform=None, p_category=optional_text(request, "category", max_len=120),
            p_status=status,
        )
