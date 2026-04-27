"""
ERP 跨域指标分析 — 复合指标（Python 计算）。

库存周转天数 / 动销率 / 进销存 / 供应商评估。
这些指标需要跨多张表查询后 Python 端计算，不适合放入单个 RPC。

设计文档: docs/document/TECH_ERP查询架构重构.md §5.6
"""
from __future__ import annotations

from typing import Any

from loguru import logger

from services.agent.tool_output import (
    ColumnMeta,
    OutputFormat,
    OutputStatus,
    ToolOutput,
)
from services.kuaimai.erp_unified_schema import TimeRange, ValidatedFilter
from services.kuaimai.erp_analytics_cross import to_date_str, date_str_offset


# ── 分发入口 ───────────────────────────────────────────────

async def query_composite_metric(
    db: Any, org_id: str, tr: TimeRange,
    metric: str, info: dict[str, str],
    filters: list[ValidatedFilter],
    group_col: str | None, outer_id: str | None,
    limit: int,
) -> ToolOutput:
    """复合指标分发器。"""
    if metric == "inventory_turnover":
        return await _query_inventory_turnover(db, org_id, tr, limit)
    if metric == "sell_through_rate":
        return await _query_sell_through_rate(db, org_id, tr, limit)
    if metric == "inventory_flow":
        return await _query_inventory_flow(db, org_id, tr, outer_id, limit)
    if metric == "supplier_evaluation":
        return await _query_supplier_evaluation(db, org_id, tr, limit)
    return ToolOutput(
        summary=f"复合指标 '{metric}' 未实现",
        status=OutputStatus.ERROR,
        source="erp",
        error_message=f"composite metric not implemented: {metric}",
    )


# ── 库存周转天数（stock + daily_stats） ───────────────────

async def _query_inventory_turnover(
    db: Any, org_id: str, tr: TimeRange, limit: int,
) -> ToolOutput:
    """库存周转天数 = 当前可用库存 / 近 30 天日均销量。"""
    stock_data = await _fetch_stock_by_product(db, org_id, limit=1000)
    thirty_days_ago = date_str_offset(tr.end_iso, days=-30)
    daily_rows = await _fetch_daily_sales_avg(
        db, org_id, thirty_days_ago, to_date_str(tr.end_iso),
    )

    if not stock_data:
        return ToolOutput(
            summary="无库存数据",
            status=OutputStatus.EMPTY,
            source="erp",
            metadata={"query_type": "cross", "metric": "inventory_turnover"},
        )

    sales_map: dict[str, float] = {}
    for r in daily_rows:
        oid = r.get("outer_id", "")
        qty = float(r.get("total_qty", 0))
        days = float(r.get("day_count", 1))
        sales_map[oid] = qty / days if days > 0 else 0

    result = []
    for item in stock_data:
        oid = item["outer_id"]
        avail = float(item.get("available_stock", 0))
        daily_avg = sales_map.get(oid, 0)
        turnover = round(avail / daily_avg, 1) if daily_avg > 0 else -1
        if turnover < 0:
            risk = "无销量"
        elif turnover < 7:
            risk = "危险"
        elif turnover < 14:
            risk = "警告"
        else:
            risk = "正常"
        result.append({
            "outer_id": oid,
            "item_name": item.get("item_name", ""),
            "available_stock": avail,
            "daily_avg_sales": round(daily_avg, 2),
            "turnover_days": turnover if turnover >= 0 else None,
            "risk_level": risk,
        })

    result.sort(key=lambda x: (x["turnover_days"] is None, x["turnover_days"] or 0))
    result = result[:limit]

    lines = ["库存周转天数（近 30 天日均销量计算）："]
    danger = [r for r in result if r["risk_level"] == "危险"]
    warning = [r for r in result if r["risk_level"] == "警告"]
    if danger:
        lines.append(f"  危险（<7天）：{len(danger)} 个 SKU")
    if warning:
        lines.append(f"  警告（<14天）：{len(warning)} 个 SKU")
    lines.append(f"  共分析 {len(result)} 个商品")

    return ToolOutput(
        summary="\n".join(lines),
        format=OutputFormat.TABLE,
        source="erp",
        data=result,
        columns=[
            ColumnMeta("outer_id", "text", "商品编码"),
            ColumnMeta("item_name", "text", "商品名称"),
            ColumnMeta("available_stock", "numeric", "可用库存"),
            ColumnMeta("daily_avg_sales", "numeric", "日均销量"),
            ColumnMeta("turnover_days", "numeric", "周转天数"),
            ColumnMeta("risk_level", "text", "风险等级"),
        ],
        metadata={"query_type": "cross", "metric": "inventory_turnover"},
    )


# ── 动销率（daily_stats + product） ───────────────────────

async def _query_sell_through_rate(
    db: Any, org_id: str, tr: TimeRange, limit: int,
) -> ToolOutput:
    """动销率 = 有销量的 SKU 数 / 总 SKU 数 × 100。"""
    start_str = to_date_str(tr.start_iso)
    end_str = to_date_str(tr.end_iso)

    # 有销量的 outer_id
    try:
        ds_rows = (
            db.table("erp_product_daily_stats")
            .select("outer_id")
            .eq("org_id", org_id)
            .gte("stat_date", start_str)
            .lt("stat_date", end_str)
            .gt("order_count", 0)
        ).execute().data or []
        active_ids = {r["outer_id"] for r in ds_rows}
    except Exception as e:
        logger.warning(f"sell_through: daily_stats query failed: {e}")
        active_ids = set()

    # 总商品数
    try:
        prod_result = (
            db.table("erp_products")
            .select("outer_id", count="exact")
            .eq("org_id", org_id)
        ).execute()
        total_skus = prod_result.count or len(prod_result.data or [])
    except Exception as e:
        logger.warning(f"sell_through: product query failed: {e}")
        total_skus = 0

    active_count = len(active_ids)
    rate = round(active_count / total_skus * 100, 2) if total_skus > 0 else 0

    return ToolOutput(
        summary=(
            f"{tr.label} 动销率：{rate}%\n"
            f"有销量商品 {active_count} 个 / 总商品 {total_skus} 个"
        ),
        format=OutputFormat.TABLE,
        source="erp",
        data=[{"metric_value": rate, "active_count": active_count, "total_count": total_skus}],
        columns=[
            ColumnMeta("metric_value", "numeric", "动销率(%)"),
            ColumnMeta("active_count", "integer", "有销量商品数"),
            ColumnMeta("total_count", "integer", "总商品数"),
        ],
        metadata={"query_type": "cross", "metric": "sell_through_rate", "time_range": tr.label},
    )


# ── 进销存（daily_stats + stock） ─────────────────────────

async def _query_inventory_flow(
    db: Any, org_id: str, tr: TimeRange,
    outer_id: str | None, limit: int,
) -> ToolOutput:
    """商品进销存——进了多少/卖了多少/还剩多少。"""
    start_str = to_date_str(tr.start_iso)
    end_str = to_date_str(tr.end_iso)

    # 进+销
    try:
        ds_q = (
            db.table("erp_product_daily_stats")
            .select(
                "outer_id,item_name,"
                "purchase_qty,purchase_received_qty,order_qty,"
                "aftersale_return_count,purchase_return_qty"
            )
            .eq("org_id", org_id)
            .gte("stat_date", start_str)
            .lt("stat_date", end_str)
        )
        if outer_id:
            ds_q = ds_q.eq("outer_id", outer_id)
        ds_rows = ds_q.execute().data or []
    except Exception as e:
        logger.error(f"inventory_flow: daily_stats query failed: {e}")
        ds_rows = []

    # Python 端按 outer_id 聚合
    agg: dict[str, dict[str, Any]] = {}
    for r in ds_rows:
        oid = r["outer_id"]
        if oid not in agg:
            agg[oid] = {
                "outer_id": oid, "item_name": r.get("item_name", ""),
                "purchased": 0, "received": 0,
                "sold": 0, "returned": 0, "purchase_returned": 0,
            }
        a = agg[oid]
        a["purchased"] += int(r.get("purchase_qty", 0) or 0)
        a["received"] += int(r.get("purchase_received_qty", 0) or 0)
        a["sold"] += int(r.get("order_qty", 0) or 0)
        a["returned"] += int(r.get("aftersale_return_count", 0) or 0)
        a["purchase_returned"] += int(r.get("purchase_return_qty", 0) or 0)

    # 存
    stock_data = await _fetch_stock_by_product(db, org_id, limit=1000)
    stock_map = {s["outer_id"]: s for s in stock_data}

    result = []
    for oid, a in agg.items():
        stock = stock_map.get(oid, {})
        a["current_stock"] = float(stock.get("available_stock", 0))
        a["net_flow"] = a["received"] - a["sold"] + a["returned"] - a["purchase_returned"]
        result.append(a)

    result.sort(key=lambda x: x["sold"], reverse=True)
    result = result[:limit]

    if not result:
        return ToolOutput(
            summary=f"{tr.label} 无进销存数据",
            status=OutputStatus.EMPTY,
            source="erp",
            metadata={"query_type": "cross", "metric": "inventory_flow"},
        )

    total_sold = sum(r["sold"] for r in result)
    total_purchased = sum(r["purchased"] for r in result)

    return ToolOutput(
        summary=(
            f"{tr.label} 进销存概览（{len(result)} 个商品）：\n"
            f"总采购 {total_purchased} 件，总销售 {total_sold} 件"
        ),
        format=OutputFormat.TABLE,
        source="erp",
        data=result,
        columns=[
            ColumnMeta("outer_id", "text", "商品编码"),
            ColumnMeta("item_name", "text", "商品名称"),
            ColumnMeta("purchased", "integer", "采购量"),
            ColumnMeta("received", "integer", "收货量"),
            ColumnMeta("sold", "integer", "销售量"),
            ColumnMeta("returned", "integer", "退货量"),
            ColumnMeta("current_stock", "numeric", "当前库存"),
            ColumnMeta("net_flow", "integer", "净流入"),
        ],
        metadata={"query_type": "cross", "metric": "inventory_flow", "time_range": tr.label},
    )


# ── 供应商评估（现有 RPC group_by=supplier） ──────────────

async def _query_supplier_evaluation(
    db: Any, org_id: str, tr: TimeRange, limit: int,
) -> ToolOutput:
    """供应商评估——采购量/退货率（复用 erp_global_stats_query）。"""
    common = {
        "p_start": tr.start_iso, "p_end": tr.end_iso,
        "p_time_col": tr.time_col,
        "p_group_by": "supplier", "p_limit": limit,
        "p_org_id": org_id,
    }

    purchase_result = db.rpc("erp_global_stats_query", {
        **common, "p_doc_type": "purchase",
    }).execute()
    return_result = db.rpc("erp_global_stats_query", {
        **common, "p_doc_type": "purchase_return",
    }).execute()

    purchase_rows = purchase_result.data if isinstance(purchase_result.data, list) else []
    return_rows = return_result.data if isinstance(return_result.data, list) else []

    if not purchase_rows:
        return ToolOutput(
            summary=f"{tr.label} 无采购数据，无法评估供应商",
            status=OutputStatus.EMPTY,
            source="erp",
            metadata={"query_type": "cross", "metric": "supplier_evaluation"},
        )

    return_map = {r.get("group_key", ""): r for r in return_rows}
    result = []
    for row in purchase_rows:
        supplier = row.get("group_key", "未知")
        purchase_count = int(row.get("doc_count", 0))
        purchase_amount = float(row.get("total_amount", 0))
        ret = return_map.get(supplier, {})
        return_count = int(ret.get("doc_count", 0))
        return_rate = round(return_count / purchase_count * 100, 2) if purchase_count else 0
        result.append({
            "supplier_name": supplier,
            "purchase_count": purchase_count,
            "purchase_amount": purchase_amount,
            "return_count": return_count,
            "return_rate": return_rate,
        })

    result.sort(key=lambda x: x["return_rate"], reverse=True)

    lines = [f"{tr.label} 供应商评估（{len(result)} 家）："]
    for r in result[:5]:
        lines.append(
            f"  {r['supplier_name']}: "
            f"采购 {r['purchase_count']} 笔 ¥{r['purchase_amount']:,.0f}，"
            f"退货率 {r['return_rate']}%"
        )
    if len(result) > 5:
        lines.append(f"  ...共 {len(result)} 家供应商")

    return ToolOutput(
        summary="\n".join(lines),
        format=OutputFormat.TABLE,
        source="erp",
        data=result,
        columns=[
            ColumnMeta("supplier_name", "text", "供应商"),
            ColumnMeta("purchase_count", "integer", "采购笔数"),
            ColumnMeta("purchase_amount", "numeric", "采购金额"),
            ColumnMeta("return_count", "integer", "退货笔数"),
            ColumnMeta("return_rate", "numeric", "退货率(%)"),
        ],
        metadata={"query_type": "cross", "metric": "supplier_evaluation", "time_range": tr.label},
    )


# ── 数据获取辅助 ───────────────────────────────────────────

async def _fetch_stock_by_product(
    db: Any, org_id: str, limit: int = 1000,
) -> list[dict]:
    """查询库存表，按 outer_id 聚合（多仓库/多SKU 合并）。"""
    try:
        rows = (
            db.table("erp_stock_status")
            .select("outer_id,item_name,available_stock")
            .eq("org_id", org_id)
            .gt("available_stock", 0)
            .limit(limit)
        ).execute().data or []
    except Exception as e:
        logger.warning(f"stock query failed: {e}")
        return []

    agg: dict[str, dict] = {}
    for r in rows:
        oid = r["outer_id"]
        if oid not in agg:
            agg[oid] = {"outer_id": oid, "item_name": r.get("item_name", ""), "available_stock": 0}
        agg[oid]["available_stock"] += float(r.get("available_stock", 0))
    return list(agg.values())


async def _fetch_daily_sales_avg(
    db: Any, org_id: str, start_str: str, end_str: str,
) -> list[dict]:
    """查询 daily_stats 日均销量（按 outer_id 聚合）。"""
    try:
        rows = (
            db.table("erp_product_daily_stats")
            .select("outer_id,order_qty,stat_date")
            .eq("org_id", org_id)
            .gte("stat_date", start_str)
            .lt("stat_date", end_str)
            .gt("order_qty", 0)
        ).execute().data or []
    except Exception as e:
        logger.warning(f"daily sales avg query failed: {e}")
        return []

    agg: dict[str, dict] = {}
    for r in rows:
        oid = r["outer_id"]
        if oid not in agg:
            agg[oid] = {"outer_id": oid, "total_qty": 0, "dates": set()}
        agg[oid]["total_qty"] += int(r.get("order_qty", 0) or 0)
        agg[oid]["dates"].add(r.get("stat_date", ""))

    return [
        {"outer_id": oid, "total_qty": a["total_qty"], "day_count": len(a["dates"])}
        for oid, a in agg.items()
    ]
