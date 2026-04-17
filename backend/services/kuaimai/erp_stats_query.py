"""
ERP 统计报表查询工具

查询 erp_product_daily_stats 聚合表，支持月度/周度/日度统计。

所有函数返回 ToolOutput（Phase 0 改造）。

设计文档: docs/document/TECH_ERP数据本地索引系统.md §6 工具4
时间事实层: docs/document/TECH_ERP时间准确性架构.md §6.2.2 (B5f)
重构文档: docs/document/TECH_多Agent单一职责重构.md §4.3
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from loguru import logger

from services.agent.tool_output import (
    ColumnMeta,
    OutputFormat,
    OutputStatus,
    ToolOutput,
)
from services.kuaimai.erp_local_helpers import CN_TZ, check_sync_health
from utils.time_context import (
    DateRange,
    RequestContext,
    format_time_header,
    now_cn,
)


_STATS_COLUMNS = [
    ColumnMeta("stat_date", "text", "统计日期"),
    ColumnMeta("order_count", "integer", "订单数"),
    ColumnMeta("order_qty", "integer", "销量"),
    ColumnMeta("order_amount", "numeric", "销售金额"),
    ColumnMeta("purchase_count", "integer", "采购单数"),
    ColumnMeta("purchase_qty", "integer", "采购数量"),
    ColumnMeta("aftersale_count", "integer", "售后单数"),
    ColumnMeta("aftersale_qty", "integer", "售后数量"),
]


async def local_product_stats(
    db, product_code: str,
    period: str = "month",
    start_date: str | None = None,
    end_date: str | None = None,
    org_id: str | None = None,
    request_ctx: Optional[RequestContext] = None,
) -> ToolOutput:
    """按商品编码查统计数据（聚合表）"""
    now = request_ctx.now if request_ctx else now_cn()

    if not start_date:
        start_date = now.strftime("%Y-%m-01")
    if not end_date:
        end_date = now.strftime("%Y-%m-%d")

    try:
        q = (
            db.table("erp_product_daily_stats")
            .select("*")
            .eq("outer_id", product_code)
            .gte("stat_date", start_date)
            .lte("stat_date", end_date)
        )
        result = q.order("stat_date", desc=True).execute()
        rows = result.data or []
    except Exception as e:
        logger.error(f"Stats query failed | code={product_code} | error={e}")
        return ToolOutput(
            summary=f"统计查询失败: {e}",
            source="warehouse",
            status=OutputStatus.ERROR,
            error_message=str(e),
        )

    # 时间事实头
    try:
        s_dt = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=CN_TZ)
        e_dt = datetime.strptime(end_date, "%Y-%m-%d").replace(
            tzinfo=CN_TZ, hour=23, minute=59, second=59,
        )
        date_range = DateRange.custom(s_dt, e_dt, reference=now)
        time_header = format_time_header(
            ctx=request_ctx, range_=date_range, kind="统计区间",
        )
    except Exception:
        time_header = ""

    if not rows:
        health = check_sync_health(db, ["order", "purchase", "aftersale"])
        body = f"商品 {product_code} 在 {start_date}~{end_date} 无统计数据\n{health}".strip()
        summary = f"{time_header}\n\n{body}" if time_header else body
        return ToolOutput(
            summary=summary,
            source="warehouse",
            status=OutputStatus.EMPTY,
            metadata={"product_code": product_code, "time_range": f"{start_date} ~ {end_date}"},
        )

    # 汇总各维度
    order_count = sum(r.get("order_count", 0) for r in rows)
    order_qty = sum(r.get("order_qty", 0) for r in rows)
    order_amount = sum(float(r.get("order_amount", 0)) for r in rows)
    purchase_count = sum(r.get("purchase_count", 0) for r in rows)
    purchase_qty = sum(r.get("purchase_qty", 0) for r in rows)
    purchase_amount = sum(float(r.get("purchase_amount", 0)) for r in rows)
    receipt_count = sum(r.get("receipt_count", 0) for r in rows)
    receipt_qty = sum(r.get("receipt_qty", 0) for r in rows)
    shelf_count = sum(r.get("shelf_count", 0) for r in rows)
    shelf_qty = sum(r.get("shelf_qty", 0) for r in rows)
    aftersale_count = sum(r.get("aftersale_count", 0) for r in rows)
    aftersale_qty = sum(r.get("aftersale_qty", 0) for r in rows)
    aftersale_amount = sum(float(r.get("aftersale_amount", 0)) for r in rows)
    return_count = sum(r.get("return_count", 0) for r in rows)
    return_qty = sum(r.get("return_qty", 0) for r in rows)

    lines = []
    if time_header:
        lines.append(time_header)
        lines.append("")
    lines.append(f"商品 {product_code} 统计（{start_date} ~ {end_date}）：\n")

    if order_count:
        lines.append(
            f"销售：{order_count}笔，销量{order_qty}件，"
            f"金额¥{order_amount:,.2f}"
        )
    if purchase_count:
        recv_rate = (
            f"{receipt_qty / purchase_qty * 100:.1f}%"
            if purchase_qty else "N/A"
        )
        lines.append(
            f"采购：{purchase_count}笔，采购{purchase_qty}件，"
            f"到货{receipt_qty}件（到货率{recv_rate}），"
            f"金额¥{purchase_amount:,.2f}"
        )
    if receipt_count:
        lines.append(f"收货：{receipt_count}笔，收货{receipt_qty}件")
    if shelf_count:
        lines.append(f"上架：{shelf_count}笔，上架{shelf_qty}件")
    if return_count:
        lines.append(f"采退：{return_count}笔，退{return_qty}件")
    if aftersale_count:
        lines.append(
            f"售后：{aftersale_count}笔，售后{aftersale_qty}件，"
            f"售后金额¥{aftersale_amount:,.2f}"
        )

    # 关键指标
    if order_count and aftersale_count:
        rate = aftersale_count / order_count * 100
        lines.append(f"\n售后率：{aftersale_count}/{order_count} = {rate:.1f}%")
    if order_qty and len(rows) > 0:
        days_count = len(rows)
        daily_avg = order_qty / days_count
        lines.append(f"日均销量：{daily_avg:.1f}件/天（{days_count}天）")

    health = check_sync_health(db, ["order", "purchase", "aftersale"])
    if health:
        lines.append(f"\n{health}")

    return ToolOutput(
        summary="\n".join(lines),
        format=OutputFormat.TABLE,
        source="warehouse",
        columns=_STATS_COLUMNS,
        data=rows,
        metadata={
            "product_code": product_code,
            "time_range": f"{start_date} ~ {end_date}",
        },
    )
