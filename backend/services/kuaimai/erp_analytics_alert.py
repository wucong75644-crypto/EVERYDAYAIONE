"""
预警查询引擎 — 5 种库存/采购预警。

low_stock:        可售天数 < 14 天（stock + daily_stats 日均销量）
slow_moving:      近30天零销量的 SKU（daily_stats + product）
overstock:        库存 > 日均销量 x 90 天（stock + daily_stats）
out_of_stock:     库存=0 但近30天有销量（stock + daily_stats）
purchase_overdue: 采购单 delivery_date < today 且未完成（erp_document_items）

所有函数返回 AgentResult，由 UnifiedQueryEngine.execute() 路由调用。

设计文档: docs/document/TECH_ERP查询架构重构.md §5.7
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from loguru import logger

from services.agent.tool_output import (
    ColumnMeta,
    OutputFormat,
    OutputStatus,
    ToolOutput,
)
from services.kuaimai.erp_unified_schema import TimeRange, ValidatedFilter

# 分布分析从独立模块导出（方便上层统一 import）
from services.kuaimai.erp_analytics_distribution import (  # noqa: F401
    query_distribution,
    format_distribution_summary,
)


# ============================================================
# 预警阈值常量（可调）
# ============================================================

ALERT_THRESHOLDS = {
    "low_stock_critical_days": 3,
    "low_stock_warning_days": 7,
    "low_stock_info_days": 14,
    "slow_moving_days": 30,
    "overstock_days": 90,
}


# ============================================================
# 预警列定义
# ============================================================

_COL = ColumnMeta  # 缩写

_ALERT_COLUMNS = {
    "low_stock": [
        _COL("outer_id", "text", "商品编码"),
        _COL("item_name", "text", "商品名称"),
        _COL("available_stock", "numeric", "可用库存"),
        _COL("daily_avg_sales", "numeric", "日均销量"),
        _COL("days_left", "numeric", "可售天数"),
        _COL("severity", "text", "紧急程度"),
        _COL("suggestion", "text", "建议"),
    ],
    "slow_moving": [
        _COL("outer_id", "text", "商品编码"),
        _COL("item_name", "text", "商品名称"),
        _COL("available_stock", "numeric", "可用库存"),
        _COL("severity", "text", "紧急程度"),
    ],
    "overstock": [
        _COL("outer_id", "text", "商品编码"),
        _COL("item_name", "text", "商品名称"),
        _COL("available_stock", "numeric", "可用库存"),
        _COL("daily_avg_sales", "numeric", "日均销量"),
        _COL("days_of_stock", "numeric", "库存可售天数"),
        _COL("excess_qty", "numeric", "超量"),
        _COL("severity", "text", "紧急程度"),
    ],
    "out_of_stock": [
        _COL("outer_id", "text", "商品编码"),
        _COL("item_name", "text", "商品名称"),
        _COL("recent_sales", "numeric", "近30天销量"),
        _COL("severity", "text", "紧急程度"),
    ],
    "purchase_overdue": [
        _COL("doc_code", "text", "采购单号"),
        _COL("supplier_name", "text", "供应商"),
        _COL("item_name", "text", "商品名称"),
        _COL("outer_id", "text", "商品编码"),
        _COL("quantity", "numeric", "采购数量"),
        _COL("delivery_date", "text", "预计到货日"),
        _COL("overdue_days", "integer", "超期天数"),
        _COL("severity", "text", "紧急程度"),
    ],
}


# ============================================================
# 内部辅助：查库存 / 查日均销量 / 查活跃 SKU
# ============================================================

async def _fetch_stock(db: Any, org_id: str | None) -> list[dict]:
    """查询所有 SKU 的当前库存（erp_stock_status 表）。"""
    q = db.table("erp_stock_status").select(
        "outer_id,item_name,available_stock,total_stock,sellable_num"
    )
    if org_id:
        q = q.eq("org_id", org_id)
    return (q.execute()).data or []


async def _fetch_daily_avg_sales(
    db: Any, org_id: str | None, days: int = 30,
) -> dict[str, float]:
    """从 daily_stats 计算近 N 天日均销量，返回 {outer_id: avg_daily_qty}。"""
    start = (date.today() - timedelta(days=days)).isoformat()
    q = db.table("erp_product_daily_stats").select("outer_id,order_qty")
    if org_id:
        q = q.eq("org_id", org_id)
    q = q.gte("stat_date", start).lt("stat_date", date.today().isoformat())
    rows = (q.execute()).data or []

    totals: dict[str, float] = {}
    for r in rows:
        oid = r["outer_id"]
        totals[oid] = totals.get(oid, 0) + float(r.get("order_qty") or 0)
    return {oid: total / days for oid, total in totals.items()}


async def _fetch_active_skus(
    db: Any, org_id: str | None, days: int = 30,
) -> dict[str, float]:
    """近 N 天有销量的 SKU + 总销量，返回 {outer_id: total_qty}。"""
    start = (date.today() - timedelta(days=days)).isoformat()
    q = db.table("erp_product_daily_stats").select(
        "outer_id,order_qty",
    ).gt("order_qty", 0)
    if org_id:
        q = q.eq("org_id", org_id)
    q = q.gte("stat_date", start).lt("stat_date", date.today().isoformat())
    rows = (q.execute()).data or []

    totals: dict[str, float] = {}
    for r in rows:
        oid = r["outer_id"]
        totals[oid] = totals.get(oid, 0) + float(r.get("order_qty") or 0)
    return totals


async def _fetch_all_products(db: Any, org_id: str | None) -> list[dict]:
    """查所有商品（erp_products 表）。"""
    q = db.table("erp_products").select("outer_id,title")
    if org_id:
        q = q.eq("org_id", org_id)
    return (q.execute()).data or []


# ============================================================
# 预警查询入口
# ============================================================

async def query_alert(
    db: Any,
    org_id: str | None,
    alert_type: str,
    filters: list[ValidatedFilter] | None = None,
    tr: TimeRange | None = None,
    limit: int = 100,
) -> ToolOutput:
    """预警查询——规则引擎入口。"""
    handler = _ALERT_HANDLERS.get(alert_type)
    if not handler:
        return ToolOutput(
            summary=f"不支持的预警类型: {alert_type}",
            status=OutputStatus.ERROR,
            error_message=f"alert_type={alert_type} not in "
                          f"{list(_ALERT_HANDLERS.keys())}",
            metadata={"query_type": "alert", "alert_type": alert_type},
        )
    try:
        return await handler(db, org_id, filters, tr, limit)
    except Exception as e:
        logger.error(f"Alert query failed | type={alert_type} | {e}")
        return ToolOutput(
            summary=f"预警查询失败: {e}",
            status=OutputStatus.ERROR,
            error_message=str(e),
            metadata={"query_type": "alert", "alert_type": alert_type},
        )


def _build_result(
    data: list[dict], alert_type: str, total: int, limit: int,
) -> ToolOutput:
    """统一构建预警 ToolOutput。"""
    sliced = data[:limit]
    return ToolOutput(
        summary=format_alert_summary(sliced, alert_type, total),
        status=OutputStatus.OK if sliced else OutputStatus.EMPTY,
        format=OutputFormat.TABLE,
        data=sliced,
        columns=_ALERT_COLUMNS.get(alert_type, []),
        metadata={
            "query_type": "alert",
            "alert_type": alert_type,
            "total_alerts": total,
        },
    )


# ============================================================
# 5 种预警处理器
# ============================================================

async def _alert_low_stock(
    db: Any, org_id: str | None,
    filters: list[ValidatedFilter] | None,
    tr: TimeRange | None, limit: int,
) -> ToolOutput:
    """缺货预警：可售天数 < 14 天。"""
    stock = await _fetch_stock(db, org_id)
    daily_sales = await _fetch_daily_avg_sales(db, org_id, days=30)
    th = ALERT_THRESHOLDS

    alerts: list[dict] = []
    for item in stock:
        oid = item["outer_id"]
        avail = float(item.get("available_stock") or 0)
        daily_avg = daily_sales.get(oid, 0)
        if daily_avg <= 0:
            continue
        days_left = avail / daily_avg
        if days_left >= th["low_stock_info_days"]:
            continue

        shortage = max(0, round(daily_avg * 30 - avail))
        if days_left < th["low_stock_critical_days"]:
            severity = "critical"
        elif days_left < th["low_stock_warning_days"]:
            severity = "warning"
        else:
            severity = "info"

        alerts.append({
            "outer_id": oid,
            "item_name": item.get("item_name", ""),
            "available_stock": avail,
            "daily_avg_sales": round(daily_avg, 2),
            "days_left": round(days_left, 1),
            "severity": severity,
            "suggestion": f"建议补货 {shortage} 件（30天用量）",
        })

    alerts.sort(key=lambda x: x["days_left"])
    return _build_result(alerts, "low_stock", len(alerts), limit)


async def _alert_slow_moving(
    db: Any, org_id: str | None,
    filters: list[ValidatedFilter] | None,
    tr: TimeRange | None, limit: int,
) -> ToolOutput:
    """滞销预警：近30天零销量的 SKU（有库存）。"""
    days = ALERT_THRESHOLDS["slow_moving_days"]
    active_skus = await _fetch_active_skus(db, org_id, days=days)
    all_products = await _fetch_all_products(db, org_id)
    stock = await _fetch_stock(db, org_id)
    stock_map = {s["outer_id"]: s for s in stock}

    slow: list[dict] = []
    for p in all_products:
        oid = p["outer_id"]
        if oid in active_skus:
            continue
        avail = float(stock_map.get(oid, {}).get("available_stock") or 0)
        if avail <= 0:
            continue
        severity = "critical" if avail > 100 else "warning"
        slow.append({
            "outer_id": oid,
            "item_name": p.get("title", ""),
            "available_stock": avail,
            "severity": severity,
        })

    slow.sort(key=lambda x: x["available_stock"], reverse=True)
    return _build_result(slow, "slow_moving", len(slow), limit)


async def _alert_overstock(
    db: Any, org_id: str | None,
    filters: list[ValidatedFilter] | None,
    tr: TimeRange | None, limit: int,
) -> ToolOutput:
    """积压预警：库存 > 日均销量 x 90 天。"""
    stock = await _fetch_stock(db, org_id)
    daily_sales = await _fetch_daily_avg_sales(db, org_id, days=30)
    threshold_days = ALERT_THRESHOLDS["overstock_days"]

    alerts: list[dict] = []
    for item in stock:
        oid = item["outer_id"]
        avail = float(item.get("available_stock") or 0)
        daily_avg = daily_sales.get(oid, 0)
        if daily_avg <= 0 or avail <= 0:
            continue
        days_of_stock = avail / daily_avg
        if days_of_stock <= threshold_days:
            continue
        alerts.append({
            "outer_id": oid,
            "item_name": item.get("item_name", ""),
            "available_stock": avail,
            "daily_avg_sales": round(daily_avg, 2),
            "days_of_stock": round(days_of_stock, 0),
            "excess_qty": round(avail - daily_avg * threshold_days),
            "severity": "warning",
        })

    alerts.sort(key=lambda x: x["days_of_stock"], reverse=True)
    return _build_result(alerts, "overstock", len(alerts), limit)


async def _alert_out_of_stock(
    db: Any, org_id: str | None,
    filters: list[ValidatedFilter] | None,
    tr: TimeRange | None, limit: int,
) -> ToolOutput:
    """热销断货：库存=0 但近30天有销量。"""
    stock = await _fetch_stock(db, org_id)
    active_skus = await _fetch_active_skus(db, org_id, days=30)

    alerts: list[dict] = []
    for item in stock:
        if float(item.get("available_stock") or 0) != 0:
            continue
        oid = item["outer_id"]
        total_qty = active_skus.get(oid)
        if total_qty is None:
            continue
        alerts.append({
            "outer_id": oid,
            "item_name": item.get("item_name", ""),
            "recent_sales": round(total_qty, 0),
            "severity": "critical",
        })

    alerts.sort(key=lambda x: x["recent_sales"], reverse=True)
    return _build_result(alerts, "out_of_stock", len(alerts), limit)


async def _alert_purchase_overdue(
    db: Any, org_id: str | None,
    filters: list[ValidatedFilter] | None,
    tr: TimeRange | None, limit: int,
) -> ToolOutput:
    """采购超期：delivery_date < today 且未完成。"""
    today = date.today().isoformat()
    q = (
        db.table("erp_document_items")
        .select(
            "doc_code,supplier_name,item_name,outer_id,"
            "quantity,delivery_date,doc_created_at",
            count="exact",
        )
        .eq("doc_type", "purchase")
        .lt("delivery_date", today)
        .not_.in_("doc_status", ["已完成", "已关闭", "已取消"])
    )
    if org_id:
        q = q.eq("org_id", org_id)
    q = q.order("delivery_date", desc=False).limit(limit or 100)

    result = q.execute()
    rows = result.data or []
    for r in rows:
        delivery = r.get("delivery_date")
        overdue = (
            (date.today() - date.fromisoformat(str(delivery)[:10])).days
            if delivery else 0
        )
        r["overdue_days"] = overdue
        r["severity"] = (
            "critical" if overdue > 14
            else "warning" if overdue > 7
            else "info"
        )

    return _build_result(rows, "purchase_overdue",
                         result.count or len(rows), limit)


_ALERT_HANDLERS = {
    "low_stock": _alert_low_stock,
    "slow_moving": _alert_slow_moving,
    "overstock": _alert_overstock,
    "out_of_stock": _alert_out_of_stock,
    "purchase_overdue": _alert_purchase_overdue,
}


# ============================================================
# 格式化
# ============================================================

_ALERT_TYPE_CN = {
    "low_stock": "缺货预警", "slow_moving": "滞销预警",
    "overstock": "积压预警", "out_of_stock": "热销断货",
    "purchase_overdue": "采购超期",
}
_SEVERITY_CN = {"critical": "紧急", "warning": "警告", "info": "提醒"}


def format_alert_summary(
    alerts: list[dict], alert_type: str, total: int,
) -> str:
    """预警结果人类可读摘要。"""
    type_cn = _ALERT_TYPE_CN.get(alert_type, alert_type)
    if not alerts:
        return f"{type_cn}：暂无预警项"

    sev_counts: dict[str, int] = {}
    for a in alerts:
        s = a.get("severity", "info")
        sev_counts[s] = sev_counts.get(s, 0) + 1

    parts = [f"{type_cn}：共 {total} 项"]
    for sev in ("critical", "warning", "info"):
        cnt = sev_counts.get(sev, 0)
        if cnt > 0:
            parts.append(f"{_SEVERITY_CN.get(sev, sev)} {cnt} 项")

    details = _format_top_details(alerts[:3], alert_type)
    if details:
        parts.append("\n" + "\n".join(details))
    if total > 3:
        parts.append(f"  ...等共 {total} 项")
    return "，".join(parts[:3]) + ("".join(parts[3:]) if len(parts) > 3 else "")


def _format_top_details(alerts: list[dict], alert_type: str) -> list[str]:
    """生成前 N 条预警的详情摘要行。"""
    details = []
    for a in alerts:
        name = a.get("item_name") or a.get("outer_id", "")
        if alert_type == "low_stock":
            details.append(f"  {name}：还能卖 {a.get('days_left', '?')} 天")
        elif alert_type == "slow_moving":
            details.append(
                f"  {name}：库存 {a.get('available_stock', 0)} 件，"
                f"近{ALERT_THRESHOLDS['slow_moving_days']}天零销量"
            )
        elif alert_type == "overstock":
            details.append(
                f"  {name}：库存够卖 {a.get('days_of_stock', '?')} 天，"
                f"超量 {a.get('excess_qty', 0)} 件"
            )
        elif alert_type == "out_of_stock":
            details.append(
                f"  {name}：已断货，近30天卖了 {a.get('recent_sales', 0)} 件"
            )
        elif alert_type == "purchase_overdue":
            details.append(
                f"  {a.get('doc_code', '')}({a.get('supplier_name', '')})："
                f"超期 {a.get('overdue_days', 0)} 天"
            )
    return details
