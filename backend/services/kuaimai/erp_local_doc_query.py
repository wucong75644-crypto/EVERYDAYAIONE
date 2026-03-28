"""
ERP 本地多维度单据查询

支持按 订单号/快递号/采购单号/供应商/店铺/商品编码 查询单据，
返回完整信息含所有中转钥匙（sid/order_no/express_no/outer_id）。

设计文档: docs/document/TECH_ERP本地优先统一查询架构.md §6 工具1
"""

from __future__ import annotations

from loguru import logger


from services.kuaimai.erp_local_helpers import check_sync_health, cutoff_iso

_DOC_TYPE_NAMES = {
    "purchase": "采购单",
    "receipt": "收货单",
    "shelf": "上架单",
    "order": "订单",
    "aftersale": "售后单",
    "purchase_return": "采退单",
}


async def local_doc_query(
    db,
    product_code: str | None = None,
    order_no: str | None = None,
    doc_code: str | None = None,
    express_no: str | None = None,
    supplier_name: str | None = None,
    shop_name: str | None = None,
    doc_type: str | None = None,
    status: str | None = None,
    days: int = 30,
    org_id: str | None = None,
) -> str:
    """多维度单据查询，返回完整信息含所有中转钥匙"""
    if not any([product_code, order_no, doc_code, express_no,
                supplier_name, shop_name]):
        return "请至少提供一个查询条件（product_code/order_no/doc_code/express_no/supplier_name/shop_name）"

    try:
        rows = _execute_query(
            db, product_code, order_no, doc_code, express_no,
            supplier_name, shop_name, doc_type, status, days,
            org_id=org_id,
        )
    except Exception as e:
        logger.error(f"local_doc_query failed | error={e}", exc_info=True)
        return f"单据查询失败: {e}"

    if not rows:
        types = [doc_type] if doc_type else ["order", "purchase", "aftersale"]
        health = check_sync_health(db, types, org_id=org_id)
        return f"未查到匹配记录（近{days}天）\n{health}".strip()

    return _format_doc_results(db, rows)


def _execute_query(
    db,
    product_code: str | None,
    order_no: str | None,
    doc_code: str | None,
    express_no: str | None,
    supplier_name: str | None,
    shop_name: str | None,
    doc_type: str | None,
    status: str | None,
    days: int,
    org_id: str | None = None,
) -> list[dict]:
    """构建并执行查询（热表 + 冷表 UNION）"""
    from services.kuaimai.erp_local_helpers import _apply_org
    cutoff = cutoff_iso(days)

    def _query_table(table: str) -> list[dict]:
        q = _apply_org(db.table(table).select("*"), org_id)
        if product_code:
            q = q.or_(
                f"outer_id.eq.{product_code},"
                f"sku_outer_id.eq.{product_code}"
            )
        if order_no:
            q = q.eq("order_no", order_no)
        if doc_code:
            q = q.eq("doc_code", doc_code)
        if express_no:
            q = q.eq("express_no", express_no)
        if supplier_name:
            q = q.ilike("supplier_name", f"%{supplier_name}%")
        if shop_name:
            q = q.ilike("shop_name", f"%{shop_name}%")
        if doc_type:
            q = q.eq("doc_type", doc_type)
        if status:
            q = q.or_(f"doc_status.eq.{status},order_status.eq.{status}")
        q = q.gte("doc_created_at", cutoff)
        q = q.order("doc_created_at", desc=True)
        q = q.limit(50)
        return q.execute().data or []

    rows = _query_table("erp_document_items")

    # days > 90 自动查冷表
    if days > 90:
        archive_rows = _query_table("erp_document_items_archive")
        seen = {(r["doc_id"], r["item_index"]) for r in rows}
        for r in archive_rows:
            if (r["doc_id"], r["item_index"]) not in seen:
                rows.append(r)
        rows.sort(key=lambda r: r.get("doc_created_at", ""), reverse=True)
        rows = rows[:50]

    return rows


def _format_doc_results(db, rows: list[dict]) -> str:
    """格式化结果，按 doc_id 聚合，暴露所有中转钥匙"""
    # 按 doc_id 聚合
    docs: dict[str, list[dict]] = {}
    for r in rows:
        docs.setdefault(r["doc_id"], []).append(r)

    lines = [f"查询结果（共{len(docs)}笔单据）：\n"]
    for i, (doc_id, items) in enumerate(list(docs.items())[:20], 1):
        first = items[0]
        dt = first.get("doc_type", "")
        type_name = _DOC_TYPE_NAMES.get(dt, dt)

        # 始终暴露所有中转钥匙
        keys = [f"sid={doc_id}"]
        if first.get("order_no"):
            keys.append(f"order_no={first['order_no']}")
        if first.get("doc_code"):
            keys.append(f"doc_code={first['doc_code']}")
        if first.get("express_no"):
            express_info = first["express_no"]
            if first.get("express_company"):
                express_info += f"({first['express_company']})"
            keys.append(f"express={express_info}")

        lines.append(f"{i}. {type_name} {' | '.join(keys)}")

        for item in items:
            sku_info = ""
            if item.get("sku_outer_id"):
                sku_info = f" | SKU: {item['sku_outer_id']}"
            lines.append(
                f"  商品: {item.get('outer_id', '')}({item.get('item_name', '')})"
                f" x {item.get('quantity', '')}件 ¥{item.get('amount', '')}"
                f"{sku_info}"
            )

        if first.get("supplier_name"):
            lines.append(f"  供应商: {first['supplier_name']}")
        if first.get("shop_name"):
            lines.append(f"  店铺: {first['shop_name']}")
        if first.get("platform"):
            lines.append(f"  平台: {first['platform']}")

        status_parts = []
        if first.get("doc_status"):
            status_parts.append(first["doc_status"])
        if first.get("order_status"):
            status_parts.append(first["order_status"])
        date_str = str(first.get("doc_created_at", ""))[:10]
        lines.append(
            f"  状态: {'/'.join(status_parts) or '未知'} | 时间: {date_str}"
        )
        lines.append("")

    # 汇总
    total_qty = sum(r.get("quantity") or 0 for r in rows)
    total_amt = sum(float(r.get("amount") or 0) for r in rows)
    lines.append(f"📊 汇总：{len(docs)}笔 | {total_qty}件 | ¥{total_amt:,.2f}")

    # 同步健康
    types = list({r.get("doc_type", "") for r in rows})
    health = check_sync_health(db, types)
    if health:
        lines.append(health)

    return "\n".join(lines)
