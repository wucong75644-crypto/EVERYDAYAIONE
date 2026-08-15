"""
ERP 跨域指标分析模块 — 主入口 + RPC 指标。

20 个跨域指标覆盖销售/利润/采购/库存/履约 5 大类。
本文件：主分发器 + daily_stats RPC 指标 + 复购率 + 发货时效。
复合指标（周转/动销/进销存/供应商评估）见 erp_analytics_cross_composite.py。

设计文档: docs/document/TECH_ERP查询架构重构.md §5.6
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from loguru import logger

from services.agent.tool_output import (
    ColumnMeta,
    OutputFormat,
    OutputStatus,
    ToolOutput,
)
from services.kuaimai.erp_unified_schema import (
    PLATFORM_CN,
    TimeRange,
    ValidatedFilter,
)


# ── 指标元信息 ─────────────────────────────────────────────

METRIC_INFO: dict[str, dict[str, str]] = {
    # daily_stats 比率/均值指标（RPC: erp_cross_metric_query）
    "return_rate":           {"label": "退货率",       "unit": "%"},
    "refund_rate":           {"label": "退款率",       "unit": "%"},
    "exchange_rate":         {"label": "换货率",       "unit": "%"},
    "aftersale_rate":        {"label": "售后率",       "unit": "%"},
    "avg_order_value":       {"label": "客单价",       "unit": "元"},
    "gross_margin":          {"label": "毛利率",       "unit": "%"},
    "gross_profit":          {"label": "毛利额",       "unit": "元"},
    "purchase_fulfillment":  {"label": "采购达成率",   "unit": "%"},
    "shelf_rate":            {"label": "上架率",       "unit": "%"},
    "supplier_return_rate":  {"label": "供应商退货率", "unit": "%"},
    # 专用 RPC 指标
    "repurchase_rate":       {"label": "复购率",       "unit": "%"},
    "avg_ship_time":         {"label": "平均发货时长", "unit": "小时"},
    "same_day_rate":         {"label": "当日发货率",   "unit": "%"},
    # 复合指标（Python 计算，实现在 erp_analytics_cross_composite.py）
    "inventory_turnover":    {"label": "库存周转天数", "unit": "天"},
    "sell_through_rate":     {"label": "动销率",       "unit": "%"},
    "inventory_flow":        {"label": "进销存",       "unit": ""},
    "supplier_evaluation":   {"label": "供应商评估",   "unit": ""},
}

# daily_stats RPC 支持的指标名（传给 p_metric）
_DS_METRICS = frozenset({
    "return_rate", "refund_rate", "exchange_rate", "aftersale_rate",
    "avg_order_value", "gross_margin", "gross_profit",
    "purchase_fulfillment", "shelf_rate", "supplier_return_rate",
})

# 复合指标名（分发到 composite 模块）
_COMPOSITE_METRICS = frozenset({
    "inventory_turnover", "sell_through_rate",
    "inventory_flow", "supplier_evaluation",
})


# ── 主入口 ─────────────────────────────────────────────────

async def query_cross(
    db: Any,
    org_id: str,
    filters: list[ValidatedFilter],
    tr: TimeRange,
    metrics: list[str] | None = None,
    group_by: list[str] | None = None,
    time_granularity: str | None = None,
    sort_by: str | None = None,
    sort_dir: str = "desc",
    limit: int = 50,
    **_kwargs: Any,
) -> ToolOutput:
    """跨域指标查询——按 metric 名称分发到对应处理器。"""
    metric = (metrics[0] if metrics else "").strip()
    if not metric or metric not in METRIC_INFO:
        known = ", ".join(sorted(METRIC_INFO.keys()))
        return ToolOutput(
            summary=f"未知跨域指标 '{metric}'，支持: {known}",
            status=OutputStatus.ERROR,
            source="erp",
            error_message=f"unknown metric: {metric}",
        )

    info = METRIC_INFO[metric]
    group_col = group_by[0] if group_by else None
    outer_id = _extract_filter(filters, "outer_id")
    platform = _extract_filter(filters, "platform")
    shop_name = _extract_filter(filters, "shop_name", ops=("eq", "like"))

    try:
        if metric in _DS_METRICS:
            return await _query_ds_metric(
                db, org_id, tr, metric, info,
                group_col, time_granularity,
                outer_id, platform, shop_name, limit,
            )
        if metric == "repurchase_rate":
            return await _query_repurchase_rate(
                db, org_id, tr, info,
                group_col, platform, shop_name, limit,
            )
        if metric in ("avg_ship_time", "same_day_rate"):
            return await _query_ship_time(
                db, org_id, tr, info,
                group_col, platform, shop_name, limit,
            )
        if metric in _COMPOSITE_METRICS:
            from services.kuaimai.erp_analytics_cross_composite import (
                query_composite_metric,
            )
            return await query_composite_metric(
                db, org_id, tr, metric, info,
                filters, group_col, outer_id, limit,
            )
    except Exception as e:
        logger.error(f"cross metric failed | metric={metric} | {e}", exc_info=True)
        return ToolOutput(
            summary=f"{info['label']}查询失败: {e}",
            status=OutputStatus.ERROR,
            source="erp",
            error_message=str(e),
            metadata={"query_type": "cross", "metric": metric},
        )

    return ToolOutput(
        summary=f"指标 '{metric}' 暂不支持",
        status=OutputStatus.ERROR,
        source="erp",
        error_message=f"metric not implemented: {metric}",
    )


# ── daily_stats 比率/均值指标（RPC） ───────────────────────

async def _query_ds_metric(
    db: Any, org_id: str, tr: TimeRange,
    metric: str, info: dict[str, str],
    group_col: str | None, granularity: str | None,
    outer_id: str | None, platform: str | None,
    shop_name: str | None, limit: int,
) -> ToolOutput:
    """调用 erp_cross_metric_query RPC 查询 daily_stats 指标。"""
    params = {
        "p_org_id": org_id,
        "p_start": to_date_str(tr.start_iso),
        "p_end": to_date_str(tr.end_iso),
        "p_metric": metric,
        "p_group_by": group_col,
        "p_granularity": granularity,
        "p_outer_id": outer_id,
        "p_platform": platform,
        "p_shop_name": shop_name,
        "p_limit": min(limit, 500),
    }
    result = db.rpc("erp_cross_metric_query", params).execute()
    data = result.data

    if isinstance(data, dict) and "error" in data:
        return ToolOutput(
            summary=f"{info['label']}查询参数错误: {data['error']}",
            status=OutputStatus.ERROR,
            source="erp",
            error_message=str(data["error"]),
        )

    rows = data if isinstance(data, list) else []
    if not rows:
        return ToolOutput(
            summary=f"{tr.label} 内无 {info['label']} 数据",
            status=OutputStatus.EMPTY,
            source="erp",
            metadata={"query_type": "cross", "metric": metric},
        )

    translate_platform_keys(rows)
    summary = format_metric_summary(rows, info, tr, group_col, granularity)
    columns = build_metric_columns(group_col, granularity, info)

    return ToolOutput(
        summary=summary,
        format=OutputFormat.TABLE,
        source="erp",
        data=rows,
        columns=columns,
        metadata={
            "query_type": "cross",
            "metric": metric,
            "time_range": tr.label,
            "granularity": granularity,
        },
    )


# ── 复购率（专用 RPC） ────────────────────────────────────

async def _query_repurchase_rate(
    db: Any, org_id: str, tr: TimeRange, info: dict[str, str],
    group_col: str | None, platform: str | None,
    shop_name: str | None, limit: int,
) -> ToolOutput:
    """复购率——buyer_nick 子查询。"""
    params = {
        "p_org_id": org_id,
        "p_start": tr.start_iso,
        "p_end": tr.end_iso,
        "p_group_by": group_col,
        "p_platform": platform,
        "p_shop_name": shop_name,
        "p_limit": min(limit, 200),
    }
    result = db.rpc("erp_repurchase_rate_query", params).execute()
    rows = result.data if isinstance(result.data, list) else []

    if not rows:
        return ToolOutput(
            summary=f"{tr.label} 内无复购数据（可能无有效买家记录）",
            status=OutputStatus.EMPTY,
            source="erp",
            metadata={"query_type": "cross", "metric": "repurchase_rate"},
        )

    translate_platform_keys(rows)
    total_buyers = sum(r.get("total_buyers", 0) for r in rows)
    repeat_buyers = sum(r.get("repeat_buyers", 0) for r in rows)

    if group_col:
        lines = [f"{tr.label} 复购率（按{group_label(group_col)}）："]
        for r in rows:
            lines.append(
                f"  {r.get('group_key', '未知')}: {r['metric_value']}%"
                f"（复购{r['repeat_buyers']}人/总{r['total_buyers']}人）"
            )
    else:
        rate = rows[0]["metric_value"] if rows else 0
        lines = [
            f"{tr.label} 复购率：{rate}%",
            f"总客户 {total_buyers} 人，复购客户 {repeat_buyers} 人",
        ]

    columns = [
        ColumnMeta("metric_value", "numeric", "复购率(%)"),
        ColumnMeta("total_buyers", "integer", "总客户数"),
        ColumnMeta("repeat_buyers", "integer", "复购客户数"),
    ]
    if group_col:
        columns.insert(0, ColumnMeta("group_key", "text", group_label(group_col)))

    return ToolOutput(
        summary="\n".join(lines),
        format=OutputFormat.TABLE,
        source="erp",
        data=rows,
        columns=columns,
        metadata={"query_type": "cross", "metric": "repurchase_rate", "time_range": tr.label},
    )


# ── 发货时效（专用 RPC） ──────────────────────────────────

async def _query_ship_time(
    db: Any, org_id: str, tr: TimeRange, info: dict[str, str],
    group_col: str | None, platform: str | None,
    shop_name: str | None, limit: int,
) -> ToolOutput:
    """发货时效——AVG(consign_time - pay_time)。"""
    params = {
        "p_org_id": org_id,
        "p_start": tr.start_iso,
        "p_end": tr.end_iso,
        "p_group_by": group_col,
        "p_platform": platform,
        "p_shop_name": shop_name,
        "p_limit": min(limit, 200),
    }
    result = db.rpc("erp_ship_time_query", params).execute()
    rows = result.data if isinstance(result.data, list) else []

    if not rows:
        return ToolOutput(
            summary=f"{tr.label} 内无发货时效数据（可能无已发货订单）",
            status=OutputStatus.EMPTY,
            source="erp",
            metadata={"query_type": "cross", "metric": "avg_ship_time"},
        )

    translate_platform_keys(rows)

    if group_col:
        lines = [f"{tr.label} 发货时效（按{group_label(group_col)}）："]
        for r in rows:
            lines.append(
                f"  {r.get('group_key', '未知')}: "
                f"平均 {r['avg_ship_hours']} 小时，"
                f"当日发货率 {r['same_day_rate']}%"
                f"（共 {r['total_shipped']} 单）"
            )
    else:
        r = rows[0]
        lines = [
            f"{tr.label} 发货时效：",
            f"平均发货时长 {r['avg_ship_hours']} 小时",
            f"当日发货率 {r['same_day_rate']}%（{r['total_shipped']} 单中）",
        ]

    columns = [
        ColumnMeta("avg_ship_hours", "numeric", "平均发货时长(小时)"),
        ColumnMeta("same_day_rate", "numeric", "当日发货率(%)"),
        ColumnMeta("total_shipped", "integer", "已发货单数"),
    ]
    if group_col:
        columns.insert(0, ColumnMeta("group_key", "text", group_label(group_col)))

    return ToolOutput(
        summary="\n".join(lines),
        format=OutputFormat.TABLE,
        source="erp",
        data=rows,
        columns=columns,
        metadata={"query_type": "cross", "metric": "avg_ship_time", "time_range": tr.label},
    )


# ── 格式化与辅助（供本模块和 composite 模块共用） ─────────

def format_metric_summary(
    rows: list[dict], info: dict[str, str],
    tr: TimeRange, group_col: str | None,
    granularity: str | None,
) -> str:
    """格式化 daily_stats 指标结果为人类可读文本。"""
    label = info["label"]
    unit = info["unit"]

    if granularity and not group_col:
        lines = [f"{tr.label} {label}趋势："]
        for r in rows[:10]:
            val = r.get("metric_value")
            val_str = f"{val}{unit}" if val is not None else "N/A"
            lines.append(f"  {r.get('period', '')}: {val_str}")
        if len(rows) > 10:
            lines.append(f"  ...共 {len(rows)} 个数据点")
        return "\n".join(lines)

    if group_col:
        lines = [f"{tr.label} {label}（按{group_label(group_col)}）："]
        for r in rows[:10]:
            key = r.get("group_key") or "未知"
            val = r.get("metric_value")
            val_str = f"{val}{unit}" if val is not None else "N/A"
            extra = ""
            if r.get("numerator") is not None and r.get("denominator") is not None:
                extra = f"（{r['numerator']}/{r['denominator']}）"
            lines.append(f"  {key}: {val_str} {extra}".rstrip())
        if len(rows) > 10:
            lines.append(f"  ...共 {len(rows)} 组")
        return "\n".join(lines)

    r = rows[0] if rows else {}
    val = r.get("metric_value")
    val_str = f"{val}{unit}" if val is not None else "N/A"
    extra = ""
    if r.get("numerator") is not None and r.get("denominator") is not None:
        extra = f"（{r['numerator']}/{r['denominator']}）"
    return f"{tr.label} {label}：{val_str} {extra}".rstrip()


def build_metric_columns(
    group_col: str | None, granularity: str | None,
    info: dict[str, str],
) -> list[ColumnMeta]:
    """构建 daily_stats 指标的列定义。"""
    cols: list[ColumnMeta] = []
    if granularity:
        cols.append(ColumnMeta("period", "text", "时间"))
    if group_col:
        cols.append(ColumnMeta("group_key", "text", group_label(group_col)))
        if group_col == "outer_id":
            cols.append(ColumnMeta("item_name", "text", "商品名称"))
    cols.append(ColumnMeta("metric_value", "numeric", f"{info['label']}({info['unit']})"))
    cols.append(ColumnMeta("numerator", "numeric", "分子"))
    cols.append(ColumnMeta("denominator", "numeric", "分母"))
    return cols


def _extract_filter(
    filters: list[ValidatedFilter], field: str, ops: tuple[str, ...] = ("eq",),
) -> str | None:
    for f in filters:
        if f.field == field and f.op in ops:
            return str(f.value)
    return None


def translate_platform_keys(rows: list[dict]) -> None:
    """将 group_key 中的平台编码翻译为中文。"""
    for r in rows:
        gk = r.get("group_key")
        if gk and gk in PLATFORM_CN:
            r["group_key"] = PLATFORM_CN[gk]


def group_label(group_col: str | None) -> str:
    return {
        "outer_id": "商品", "platform": "平台", "shop_name": "店铺",
    }.get(group_col or "", "分组")


def to_date_str(iso_str: str) -> str:
    """ISO 时间字符串截取为日期字符串（YYYY-MM-DD）。"""
    return iso_str[:10]


def date_str_offset(iso_str: str, days: int) -> str:
    """日期偏移。"""
    from datetime import datetime
    dt = datetime.fromisoformat(iso_str[:10])
    return (dt + timedelta(days=days)).strftime("%Y-%m-%d")
