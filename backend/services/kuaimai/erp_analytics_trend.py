"""
ERP 趋势分析 + 对比分析引擎

Phase 3: query_trend()   — 按天/周/月的指标趋势（daily_stats RPC）
Phase 4: query_compare() — 环比/同比/周环比增长率（双 RPC + Python 计算）

设计文档: docs/document/TECH_ERP查询架构重构.md §5.3, §5.4
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from typing import Any, Union

from loguru import logger

from services.agent.tool_output import (
    ColumnMeta, OutputFormat, OutputStatus, ToolOutput,
)
from services.kuaimai.erp_unified_schema import PLATFORM_CN

TREND_METRICS_WHITELIST: frozenset[str] = frozenset({
    "order_count", "order_qty", "order_amount", "order_cost",
    "order_shipped_count", "order_finished_count",
    "order_refund_count", "order_cancelled_count",
    "aftersale_count", "aftersale_refund_count",
    "aftersale_return_count", "aftersale_exchange_count",
    "aftersale_reissue_count", "aftersale_reject_count",
    "aftersale_repair_count", "aftersale_other_count",
    "aftersale_qty", "aftersale_amount",
    "purchase_count", "purchase_qty",
    "purchase_received_qty", "purchase_amount",
    "receipt_count", "receipt_qty",
    "shelf_count", "shelf_qty",
    "purchase_return_count", "purchase_return_qty", "purchase_return_amount",
})
_DEFAULT_METRICS = ["order_count", "order_amount"]
_GRANULARITY_CN = {"day": "日", "week": "周", "month": "月"}


def _get_metric_label_cn() -> dict[str, str]:
    """延迟导入避免循环引用。"""
    from services.kuaimai.erp_multi_table_schema import FIELD_LABEL_CN
    return {k: v for k, v in FIELD_LABEL_CN.items() if k in TREND_METRICS_WHITELIST}


# ── 趋势分析 ─────────────────────────────────────────────


async def query_trend(
    db: Any, org_id: str, start_date: str, end_date: str,
    time_granularity: str = "day", metrics: list[str] | None = None,
    group_by: str | None = None, outer_id: str | None = None,
    platform: str | None = None, shop_name: str | None = None,
    limit: int = 366,
) -> ToolOutput:
    """趋势分析——按天/周/月聚合 daily_stats。"""
    clean_metrics = _sanitize_metrics(metrics)
    granularity = _auto_adjust_granularity(time_granularity, start_date, end_date)

    try:
        rpc_result = db.rpc("erp_trend_query", {
            "p_org_id": org_id, "p_start": start_date, "p_end": end_date,
            "p_granularity": granularity, "p_metrics": clean_metrics,
            "p_group_by": group_by, "p_outer_id": outer_id,
            "p_platform": platform, "p_shop_name": shop_name,
            "p_limit": min(limit, 366),
        }).execute()
    except Exception as e:
        logger.error(f"erp_trend_query RPC failed | error={e}", exc_info=True)
        return ToolOutput(
            summary=f"趋势查询失败: {e}", status=OutputStatus.ERROR,
            error_message=str(e), metadata={"query_type": "trend"},
        )

    raw_data = rpc_result.data
    if isinstance(raw_data, dict) and "error" in raw_data:
        return ToolOutput(
            summary=f"趋势查询参数错误: {raw_data['error']}",
            status=OutputStatus.ERROR, error_message=str(raw_data["error"]),
            metadata={"query_type": "trend"},
        )

    rows: list[dict] = raw_data if isinstance(raw_data, list) else []
    if not group_by and rows:
        rows = _fill_zero_periods(rows, start_date, end_date, granularity, clean_metrics)

    if not rows:
        return ToolOutput(
            summary="该时间范围内无趋势数据", status=OutputStatus.EMPTY,
            metadata={"query_type": "trend", "granularity": granularity},
        )

    _translate_platform_in_rows(rows)

    return ToolOutput(
        summary=format_trend_summary(rows, granularity, clean_metrics, group_by),
        format=OutputFormat.TABLE, data=rows,
        columns=_build_trend_columns(clean_metrics, group_by),
        metadata={"query_type": "trend", "granularity": granularity,
                   "metrics": clean_metrics, "row_count": len(rows)},
    )


# ── 对比分析 ─────────────────────────────────────────────


async def query_compare(
    db: Any, org_id: str, doc_type: str,
    start_date: str, end_date: str,
    compare_range: str = "mom", metrics: list[str] | None = None,
    group_by: str | None = None, platform: str | None = None,
    shop_name: str | None = None, limit: int = 100,
) -> ToolOutput:
    """对比分析——并行查两个时间段，Python 计算差值和增长率。"""
    try:
        cur_start, cur_end = _parse_datetime(start_date), _parse_datetime(end_date)
        prev_start, prev_end = shift_time_range(cur_start, cur_end, compare_range)
    except ValueError as e:
        return ToolOutput(
            summary=f"对比参数错误: {e}", status=OutputStatus.ERROR,
            error_message=str(e), metadata={"query_type": "compare"},
        )

    rpc_base = {
        "p_doc_type": doc_type, "p_time_col": "doc_created_at",
        "p_shop": shop_name, "p_platform": platform,
        "p_supplier": None, "p_warehouse": None,
        "p_group_by": group_by, "p_limit": limit,
        "p_org_id": org_id, "p_filters": None,
    }

    try:
        cur_data, prev_data = await asyncio.gather(
            _fetch_stats(db, rpc_base, cur_start, cur_end),
            _fetch_stats(db, rpc_base, prev_start, prev_end),
        )
    except Exception as e:
        logger.error(f"compare RPC failed | error={e}", exc_info=True)
        return ToolOutput(
            summary=f"对比查询失败: {e}", status=OutputStatus.ERROR,
            error_message=str(e), metadata={"query_type": "compare"},
        )

    for data, label in [(cur_data, "当前期"), (prev_data, "基线期")]:
        if isinstance(data, dict) and "error" in data:
            return ToolOutput(
                summary=f"{label}查询错误: {data['error']}",
                status=OutputStatus.ERROR, error_message=str(data["error"]),
                metadata={"query_type": "compare"},
            )

    compared = compute_comparison(cur_data, prev_data, group_by)
    if not compared:
        return ToolOutput(
            summary="两个时间段均无数据，无法对比", status=OutputStatus.EMPTY,
            metadata={"query_type": "compare", "compare_range": compare_range},
        )

    _translate_platform_in_rows(compared)
    cur_label = f"{cur_start.isoformat()} ~ {cur_end.isoformat()}"
    prev_label = f"{prev_start.isoformat()} ~ {prev_end.isoformat()}"

    return ToolOutput(
        summary=format_compare_summary(compared, compare_range, cur_label, prev_label),
        format=OutputFormat.TABLE, data=compared,
        columns=_build_compare_columns(group_by),
        metadata={"query_type": "compare", "compare_range": compare_range,
                   "current_period": cur_label, "prev_period": prev_label,
                   "row_count": len(compared)},
    )


# ── 时间范围偏移 ─────────────────────────────────────────


def shift_time_range(
    start: Union[date, datetime], end: Union[date, datetime], compare_range: str,
) -> tuple[Union[date, datetime], Union[date, datetime]]:
    """根据 compare_range 计算前期起止: mom/yoy/wow。

    支持 date 和 datetime：传入 datetime 时保留时分秒，
    确保对比期与当前期的时间边界一致（如今天到 14:30，上周也到 14:30）。
    """
    if compare_range == "mom":
        return _shift_months(start, -1), _shift_months(end, -1)
    if compare_range == "yoy":
        return _shift_years(start, -1), _shift_years(end, -1)
    if compare_range == "wow":
        return start - timedelta(days=7), end - timedelta(days=7)
    raise ValueError(f"未知 compare_range: {compare_range}")


def _shift_months(d: Union[date, datetime], months: int) -> Union[date, datetime]:
    """安全月偏移（月末日不存在时降级到月末，datetime 保留时分秒）。"""
    year = d.year + (d.month - 1 + months) // 12
    month = (d.month - 1 + months) % 12 + 1
    day = min(d.day, _last_day_of_month(year, month))
    if isinstance(d, datetime):
        return d.replace(year=year, month=month, day=day)
    return date(year, month, day)


def _shift_years(d: Union[date, datetime], years: int) -> Union[date, datetime]:
    """安全年偏移（02-29 降级到 02-28，datetime 保留时分秒）。"""
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        return d.replace(year=d.year + years, day=28)


def _last_day_of_month(year: int, month: int) -> int:
    if month == 12:
        return 31
    return (date(year, month + 1, 1) - timedelta(days=1)).day


# ── 对比计算 ─────────────────────────────────────────────

_COMPARE_FIELDS = ["doc_count", "total_qty", "total_amount"]


def compute_comparison(
    cur_data: Any, prev_data: Any, group_by: str | None,
) -> list[dict]:
    """计算当前期 vs 基线期的差值和增长率。"""
    if group_by is None:
        cur = _normalize_summary(cur_data)
        prev = _normalize_summary(prev_data)
        row = _compare_one_row(cur, prev)
        return [row] if row else []

    cur_list = cur_data if isinstance(cur_data, list) else []
    prev_list = prev_data if isinstance(prev_data, list) else []
    cur_map = {r.get("group_key", ""): r for r in cur_list}
    prev_map = {r.get("group_key", ""): r for r in prev_list}
    all_keys = list(dict.fromkeys(
        [r.get("group_key", "") for r in cur_list]
        + [r.get("group_key", "") for r in prev_list]
    ))

    result = []
    for key in all_keys:
        row = _compare_one_row(cur_map.get(key, {}), prev_map.get(key, {}))
        if row:
            row["group_key"] = key
            result.append(row)

    result.sort(key=lambda r: r.get("current_total_amount", 0), reverse=True)
    return result


def _normalize_summary(data: Any) -> dict:
    if isinstance(data, dict):
        return data
    if isinstance(data, list):
        return data[0] if data else {}
    return {}


def _compare_one_row(cur: dict, prev: dict) -> dict | None:
    row: dict[str, Any] = {}
    has_data = False
    for field in _COMPARE_FIELDS:
        cur_val = _to_float(cur.get(field, 0))
        prev_val = _to_float(prev.get(field, 0))
        diff = cur_val - prev_val
        if cur_val != 0 or prev_val != 0:
            has_data = True
        if prev_val == 0:
            growth = "+∞%" if cur_val > 0 else ("0.0%" if cur_val == 0 else "-∞%")
        else:
            growth = f"{diff / prev_val * 100:+.1f}%"
        row[f"current_{field}"] = cur_val
        row[f"prev_{field}"] = prev_val
        row[f"{field}_change"] = round(diff, 2)
        row[f"{field}_growth"] = growth
    return row if has_data else None


def _to_float(v: Any) -> float:
    if v is None:
        return 0.0
    try:
        return float(v)
    except (ValueError, TypeError):
        return 0.0


# ── RPC 封装 ─────────────────────────────────────────────


async def _fetch_stats(
    db: Any, base_params: dict,
    start: Union[date, datetime], end: Union[date, datetime],
) -> Any:
    """调用 erp_global_stats_query RPC。"""
    result = db.rpc("erp_global_stats_query", {
        **base_params, "p_start": start.isoformat(), "p_end": end.isoformat(),
    }).execute()
    return result.data


# ── 补零 ─────────────────────────────────────────────────


def _fill_zero_periods(
    rows: list[dict], start_date: str, end_date: str,
    granularity: str, metrics: list[str],
) -> list[dict]:
    """为无数据的时间点填充零值（仅无分组时），确保趋势图 X 轴连续。"""
    existing = {str(r.get("period", ""))[:10] for r in rows}
    zero_tmpl = {m: 0 for m in metrics}
    filled = list(rows)
    for p in _generate_periods(start_date, end_date, granularity):
        if p.isoformat() not in existing:
            filled.append({"period": p.isoformat(), **zero_tmpl})
    filled.sort(key=lambda r: str(r.get("period", "")))
    return filled


def _generate_periods(start_str: str, end_str: str, granularity: str) -> list[date]:
    """生成时间段内所有分桶起始日期。"""
    start, end = _parse_date(start_str), _parse_date(end_str)
    periods: list[date] = []
    if granularity == "day":
        cur = start
        while cur < end:
            periods.append(cur)
            cur += timedelta(days=1)
    elif granularity == "week":
        cur = start - timedelta(days=start.weekday())
        while cur < end:
            if cur >= start or cur + timedelta(days=7) > start:
                periods.append(cur)
            cur += timedelta(days=7)
    elif granularity == "month":
        cur = date(start.year, start.month, 1)
        while cur < end:
            periods.append(cur)
            cur = date(cur.year + (1 if cur.month == 12 else 0),
                       1 if cur.month == 12 else cur.month + 1, 1)
    return periods


# ── 粒度自动调整 ──────────────────────────────────────────


def _auto_adjust_granularity(granularity: str, start_str: str, end_str: str) -> str:
    """day+跨度>1年→month; week+跨度<7天→day。"""
    if granularity not in ("day", "week", "month"):
        return "day"
    try:
        span = (_parse_date(end_str) - _parse_date(start_str)).days
    except (ValueError, TypeError):
        return granularity
    if granularity == "day" and span > 365:
        logger.info(f"Trend granularity auto-adjusted: day→month (span={span}d > 365d)")
        return "month"
    if granularity == "week" and span < 7:
        logger.info(f"Trend granularity auto-adjusted: week→day (span={span}d < 7d)")
        return "day"
    return granularity


def _sanitize_metrics(metrics: list[str] | None) -> list[str]:
    if not metrics:
        return list(_DEFAULT_METRICS)
    clean = [m for m in metrics if m in TREND_METRICS_WHITELIST]
    return clean if clean else list(_DEFAULT_METRICS)


def _parse_date(s: str) -> date:
    return date.fromisoformat(s.strip()[:10])


def _parse_datetime(s: str) -> datetime:
    """解析时间字符串，保留时分秒（对比查询需要精确时间边界）。

    输入格式：
    - "2026-05-05 14:30:00+0800" → datetime(2026,5,5,14,30, tzinfo=...)
    - "2026-05-05" → datetime(2026,5,5,0,0)（无时间部分则为当天 00:00）
    """
    stripped = s.strip()
    # 尝试完整 datetime 解析
    try:
        return datetime.fromisoformat(stripped.replace("Z", "+00:00"))
    except ValueError:
        pass
    # 降级为 date → datetime midnight
    return datetime.combine(date.fromisoformat(stripped[:10]), datetime.min.time())


def _translate_platform_in_rows(rows: list[dict]) -> None:
    for row in rows:
        gk = row.get("group_key")
        if gk and gk in PLATFORM_CN:
            row["group_key"] = PLATFORM_CN[gk]


# ── 列元信息 + 摘要格式化 ────────────────────────────────


def _build_trend_columns(metrics: list[str], group_by: str | None) -> list[ColumnMeta]:
    label_cn = _get_metric_label_cn()
    cols = [ColumnMeta("period", "text", "时间")]
    if group_by:
        cols.append(ColumnMeta("group_key", "text", "分组"))
    for m in metrics:
        cols.append(ColumnMeta(m, "integer" if m.endswith("_count") else "numeric",
                               label_cn.get(m, m)))
    return cols


def _build_compare_columns(group_by: str | None) -> list[ColumnMeta]:
    cols: list[ColumnMeta] = []
    if group_by:
        cols.append(ColumnMeta("group_key", "text", "分组"))
    for field, label in [("doc_count", "单数"), ("total_qty", "数量"), ("total_amount", "金额")]:
        cols.extend([
            ColumnMeta(f"current_{field}", "numeric", f"当前{label}"),
            ColumnMeta(f"prev_{field}", "numeric", f"上期{label}"),
            ColumnMeta(f"{field}_change", "numeric", f"{label}变化"),
            ColumnMeta(f"{field}_growth", "text", f"{label}增长率"),
        ])
    return cols


def format_trend_summary(
    rows: list[dict], granularity: str, metrics: list[str], group_by: str | None,
) -> str:
    label_cn = _get_metric_label_cn()
    gran_cn = _GRANULARITY_CN.get(granularity, granularity)
    metric_str = "、".join(label_cn.get(m, m) for m in metrics)
    n_periods = len({str(r.get("period", ""))[:10] for r in rows})
    parts = [f"按{gran_cn}趋势（{metric_str}），共 {n_periods} 个时间点"]
    if group_by:
        parts.append(f"按 {group_by} 分组，共 {len({r.get('group_key', '') for r in rows})} 组")
    primary = metrics[0]
    values = [_to_float(r.get(primary, 0)) for r in rows if not r.get("group_key")]
    if not values:
        values = [_to_float(r.get(primary, 0)) for r in rows]
    if values:
        total = sum(values)
        lbl = label_cn.get(primary, primary)
        parts.append(f"{lbl}合计: {int(total):,}" if primary.endswith("_count")
                     else f"{lbl}合计: ¥{total:,.2f}")
    return "；".join(parts)


def format_compare_summary(
    compared: list[dict], compare_range: str, cur_label: str, prev_label: str,
) -> str:
    range_cn = {"mom": "环比（月）", "yoy": "同比（年）", "wow": "环比（周）"}
    parts = [range_cn.get(compare_range, compare_range) + "对比",
             f"当前期: {cur_label}", f"基线期: {prev_label}"]
    if len(compared) == 1 and "group_key" not in compared[0]:
        row = compared[0]
        for field, unit, fmt in [("doc_count", "笔", ",.0f"), ("total_qty", "件", ",.0f"),
                                  ("total_amount", "¥", ",.2f")]:
            cv, pv = row.get(f"current_{field}", 0), row.get(f"prev_{field}", 0)
            g = row.get(f"{field}_growth", "N/A")
            name = "金额" if unit == "¥" else ("单数" if field == "doc_count" else "数量")
            if unit == "¥":
                parts.append(f"  {name}: {unit}{cv:{fmt}} vs {unit}{pv:{fmt}} → {g}")
            else:
                parts.append(f"  {name}: {cv:{fmt}}{unit} vs {pv:{fmt}}{unit} → {g}")
    else:
        parts.append(f"共 {len(compared)} 组")
        for row in compared[:5]:
            gk = row.get("group_key", "")
            parts.append(f"  {gk}: ¥{row.get('current_total_amount', 0):,.2f}"
                         f"（{row.get('total_amount_growth', 'N/A')}）")
        if len(compared) > 5:
            parts.append(f"  ...等共 {len(compared)} 组")
    return "\n".join(parts)
