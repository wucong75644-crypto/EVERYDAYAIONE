"""Narrow, direct PostgreSQL adapters for the eight local ERP reads."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Mapping

from services.agent.runtime.executors.contracts import ActionSnapshot
from services.agent.runtime.executors.real_base import (
    RealReadCapability, bounded_limit, execute_query, optional_text,
    public_rows, required_text,
)


class ErpLocalReadCapability(RealReadCapability):
    def __init__(self, resources, tool_name: str) -> None:
        super().__init__(resources)
        self._tool_name = tool_name

    async def _read_bound(self, snapshot: ActionSnapshot, request: Mapping[str, object]):
        handlers = {
            "local_product_identify": self._product_identify,
            "local_stock_query": self._stock_query,
            "local_product_stats": self._product_stats,
            "local_platform_map_query": self._platform_map,
            "local_compare_stats": self._compare_stats,
            "local_shop_list": self._shop_list,
            "local_warehouse_list": self._warehouse_list,
            "local_supplier_list": self._supplier_list,
        }
        handler = handlers.get(self._tool_name)
        if handler is None:
            raise ValueError("READ_ERP_TOOL_INVALID")
        return await handler(snapshot, request)

    async def _product_identify(self, snapshot, request):
        code = optional_text(request, "code", max_len=120)
        name = optional_text(request, "name", max_len=120)
        spec = optional_text(request, "spec", max_len=120)
        if not code and not name and not spec:
            raise ValueError("READ_PRODUCT_IDENTIFIER_REQUIRED")
        rows = []
        if code:
            rows += await self._query_product(snapshot, "outer_id", code)
            rows += await self._query_sku(snapshot, "sku_outer_id", code)
            rows += await self._query_product(snapshot, "barcode", code)
            rows += await self._query_sku(snapshot, "barcode", code)
        elif name:
            rows = await self._query_product(snapshot, "title", name, fuzzy=True)
        else:
            rows = await self._query_sku(snapshot, "properties_name", spec or "", fuzzy=True)
        rows = rows[:20]
        items = public_rows(rows, ("outer_id", "sku_outer_id", "title", "properties_name", "barcode", "active_status", "shipper"))
        return {"summary": "未匹配到商品或 SKU" if not items else "商品编码识别结果", "count": len(items), "items": items}

    async def _query_product(self, snapshot, field: str, value: str, fuzzy: bool = False):
        query = self.resources.database.table("erp_products").select("outer_id,title,shipper,active_status,barcode").eq("org_id", snapshot.scope.org_id)
        query = query.ilike(field, f"%{value}%") if fuzzy else query.eq(field, value)
        return await _data(await execute_query(query.limit(20)))

    async def _query_sku(self, snapshot, field: str, value: str, fuzzy: bool = False):
        query = self.resources.database.table("erp_product_skus").select("outer_id,sku_outer_id,properties_name,barcode").eq("org_id", snapshot.scope.org_id)
        query = query.ilike(field, f"%{value}%") if fuzzy else query.eq(field, value)
        return await _data(await execute_query(query.limit(20)))

    async def _stock_query(self, snapshot, request):
        code = required_text(request, "product_code", max_len=120)
        rows = []
        for field in ("outer_id", "sku_outer_id"):
            query = self.resources.database.table("erp_stock_status").select("outer_id,sku_outer_id,properties_name,warehouse_id,sellable_num,total_stock,lock_stock,purchase_num,stock_status").eq("org_id", snapshot.scope.org_id).eq(field, code).limit(100)
            rows.extend(await _data(await execute_query(query)))
        unique = {str((row.get("outer_id"), row.get("sku_outer_id"), row.get("warehouse_id"))): row for row in rows}
        items = list(unique.values())[:100]
        return {"summary": "无库存记录" if not items else "库存查询结果", "count": len(items), "items": items}

    async def _product_stats(self, snapshot, request):
        code = required_text(request, "product_code", max_len=120)
        start = optional_text(request, "start_date", max_len=20) or datetime.now(timezone.utc).strftime("%Y-%m-01")
        end = optional_text(request, "end_date", max_len=20) or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        query = self.resources.database.table("erp_product_daily_stats").select("stat_date,order_count,order_qty,order_amount,purchase_count,purchase_qty,receipt_count,receipt_qty,aftersale_count,aftersale_qty").eq("org_id", snapshot.scope.org_id).eq("outer_id", code).gte("stat_date", start).lte("stat_date", end).order("stat_date", desc=True).limit(100)
        rows = await _data(await execute_query(query))
        return {"summary": "无统计数据" if not rows else "商品统计结果", "count": len(rows), "items": rows, "time_range": f"{start} ~ {end}"}

    async def _platform_map(self, snapshot, request):
        code = optional_text(request, "product_code", max_len=120)
        num_iid = optional_text(request, "num_iid", max_len=120)
        if not code and not num_iid:
            raise ValueError("READ_PLATFORM_IDENTIFIER_REQUIRED")
        query = self.resources.database.table("erp_product_platform_map").select("outer_id,num_iid,user_id,sku_mappings").eq("org_id", snapshot.scope.org_id)
        if code:
            query = query.eq("outer_id", code)
        if num_iid:
            query = query.eq("num_iid", num_iid)
        rows = await _data(await execute_query(query.limit(100)))
        return {"summary": "无平台映射记录" if not rows else "平台映射结果", "count": len(rows), "items": rows}

    async def _compare_stats(self, snapshot, request):
        doc_type = required_text(request, "doc_type", max_len=30)
        compare_kind = required_text(request, "compare_kind", max_len=30)
        current_period = required_text(request, "current_period", max_len=30)
        current_start, current_end = _period(current_period)
        baseline_start, baseline_end = _baseline(current_start, current_end, compare_kind)
        params = {
            "p_doc_type": doc_type, "p_time_col": "doc_created_at",
            "p_shop": optional_text(request, "shop_name", max_len=120),
            "p_platform": optional_text(request, "platform", max_len=40),
            "p_supplier": optional_text(request, "supplier_name", max_len=120),
            "p_warehouse": optional_text(request, "warehouse_name", max_len=120),
            "p_group_by": None, "p_limit": 20, "p_org_id": snapshot.scope.org_id,
            "p_filters": None,
        }
        current = await _rpc(self.resources.database, "erp_global_stats_query", {**params, "p_start": current_start, "p_end": current_end})
        baseline = await _rpc(self.resources.database, "erp_global_stats_query", {**params, "p_start": baseline_start, "p_end": baseline_end})
        return {"summary": "对比统计结果", "current": _bounded_rpc(current), "baseline": _bounded_rpc(baseline), "compare_kind": compare_kind}

    async def _shop_list(self, snapshot, request):
        query = self.resources.database.table("erp_shops").select("name,platform,state,shop_id,short_name").eq("org_id", snapshot.scope.org_id)
        platform = optional_text(request, "platform", max_len=40)
        if platform:
            query = query.eq("platform", platform)
        rows = await _data(await execute_query(query.order("platform").limit(100)))
        return {"summary": "暂无店铺数据" if not rows else "店铺列表", "count": len(rows), "items": rows}

    async def _warehouse_list(self, snapshot, request):
        query = self.resources.database.table("erp_warehouses").select("warehouse_id,name,code,warehouse_type,status,is_virtual,province,city,district,address").eq("org_id", snapshot.scope.org_id)
        virtual = request.get("is_virtual")
        if virtual is not None:
            if not isinstance(virtual, bool):
                raise ValueError("READ_IS_VIRTUAL_INVALID")
            query = query.eq("is_virtual", virtual)
        rows = await _data(await execute_query(query.order("is_virtual").order("name").limit(100)))
        return {"summary": "暂无仓库数据" if not rows else "仓库列表", "count": len(rows), "items": rows}

    async def _supplier_list(self, snapshot, request):
        query = self.resources.database.table("erp_suppliers").select("code,name,status,contact_name,category_name,remark").eq("org_id", snapshot.scope.org_id)
        category = optional_text(request, "category", max_len=120)
        status = request.get("status")
        if category:
            query = query.ilike("category_name", f"%{category}%")
        if status is not None:
            if isinstance(status, bool) or not isinstance(status, int):
                raise ValueError("READ_STATUS_INVALID")
            query = query.eq("status", status)
        rows = await _data(await execute_query(query.order("name").limit(100)))
        return {"summary": "暂无供应商数据" if not rows else "供应商列表", "count": len(rows), "items": rows}


async def _data(result):
    data = getattr(result, "data", None)
    return data if isinstance(data, list) else []


async def _rpc(database, name: str, params: dict[str, object]):
    result = await execute_query(database.rpc(name, params))
    return getattr(result, "data", None)


def _bounded_rpc(value: object) -> object:
    if isinstance(value, list):
        return value[:20]
    if isinstance(value, Mapping):
        return {key: value.get(key) for key in ("doc_count", "total_qty", "total_amount", "rows") if key in value}
    return value


def _period(period: str) -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    if period == "today":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "yesterday":
        start = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "this_week":
        start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "this_month":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        raise ValueError("READ_CURRENT_PERIOD_INVALID")
    return start.isoformat(), now.isoformat()


def _baseline(start: str, end: str, compare_kind: str) -> tuple[str, str]:
    if compare_kind not in {"wow", "yoy"}:
        raise ValueError("READ_COMPARE_KIND_INVALID")
    delta = timedelta(days=7 if compare_kind == "wow" else 365)
    return (datetime.fromisoformat(start) - delta).isoformat(), (datetime.fromisoformat(end) - delta).isoformat()
