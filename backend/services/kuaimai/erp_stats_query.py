"""
ERP 统计报表查询工具

查询 erp_product_daily_stats 聚合表，支持月度/周度/日度统计。

设计文档: docs/document/TECH_ERP数据本地索引系统.md §6 工具4
"""

from __future__ import annotations

from datetime import datetime, timezone

from loguru import logger


from services.kuaimai.erp_local_helpers import check_sync_health


async def local_product_stats(
    db, product_code: str,
    period: str = "month",
    start_date: str | None = None,
    end_date: str | None = None,
    org_id: str | None = None,
) -> str:
    """按商品编码查统计数据（聚合表）"""
    now = datetime.now(timezone.utc)

    if not start_date:
        start_date = now.strftime("%Y-%m-01")
    if not end_date:
        end_date = now.strftime("%Y-%m-%d")

    try:
        from services.kuaimai.erp_local_helpers import _apply_org
        q = (
            db.table("erp_product_daily_stats")
            .select("*")
            .eq("outer_id", product_code)
            .gte("stat_date", start_date)
            .lte("stat_date", end_date)
        )
        result = _apply_org(q, org_id).order("stat_date", desc=True).execute()
        rows = result.data or []
    except Exception as e:
        logger.error(f"Stats query failed | code={product_code} | error={e}")
        return f"统计查询失败: {e}"

    if not rows:
        health = check_sync_health(db, ["order", "purchase", "aftersale"])
        return (
            f"商品 {product_code} 在 {start_date}~{end_date} 无统计数据\n{health}"
        ).strip()

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

    lines = [
        f"商品 {product_code} 统计（{start_date} ~ {end_date}）：\n",
    ]

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
    return "\n".join(lines)
