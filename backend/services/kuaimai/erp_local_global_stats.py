"""
ERP 本地全局统计/排名查询

无需 product_code，支持按时间/类型/店铺/供应商/平台统计，
支持分组和排名。通过 DB 端 RPC 聚合，无 LIMIT 截断。

设计文档: docs/document/TECH_ERP本地优先统一查询架构.md §6 工具2
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from loguru import logger


from services.kuaimai.erp_local_helpers import check_sync_health

_DOC_TYPE_NAMES = {
    "purchase": "采购",
    "receipt": "收货",
    "shelf": "上架",
    "order": "订单",
    "aftersale": "售后",
    "purchase_return": "采退",
}


async def local_global_stats(
    db,
    doc_type: str,
    date: str | None = None,
    period: str = "day",
    shop_name: str | None = None,
    platform: str | None = None,
    supplier_name: str | None = None,
    warehouse_name: str | None = None,
    rank_by: str | None = None,
    group_by: str | None = None,
) -> str:
    """全局统计/排名（DB 端 RPC 聚合，无 LIMIT 截断）"""
    start_iso, end_iso, period_label = _calc_period(date, period)
    type_name = _DOC_TYPE_NAMES.get(doc_type, doc_type)

    # 确定 RPC 的 group_by 参数
    rpc_group = group_by or _rank_by_to_group(rank_by)

    params = {
        "p_doc_type": doc_type,
        "p_start": start_iso,
        "p_end": end_iso,
        "p_shop": shop_name or None,
        "p_platform": platform or None,
        "p_supplier": supplier_name or None,
        "p_warehouse": warehouse_name or None,
        "p_group_by": rpc_group,
        "p_limit": 20,
    }

    try:
        result = db.rpc("erp_global_stats_query", params).execute()
        data = result.data
    except Exception as e:
        logger.error(f"local_global_stats RPC failed | error={e}", exc_info=True)
        return f"统计查询失败: {e}"

    # RPC 返回校验
    if not data or data == {} or data == []:
        health = check_sync_health(db, [doc_type])
        return f"{type_name}{period_label}内无记录\n{health}".strip()

    if isinstance(data, dict) and "error" in data:
        return f"查询参数错误: {data['error']}"

    # 根据返回类型格式化
    if rpc_group is None:
        return _format_summary(data, type_name, period_label, db, doc_type)

    if rank_by:
        return _format_ranking(data, rank_by, type_name, period_label)

    return _format_grouped(data, group_by or "", type_name, period_label)


def _rank_by_to_group(rank_by: str | None) -> str | None:
    """rank_by 映射到 RPC 的 p_group_by（排名需要按 product 分组）"""
    if rank_by:
        return "product"
    return None


def _calc_period(
    date: str | None, period: str,
) -> tuple[str, str, str]:
    """计算统计时间范围，返回 (start_iso, end_iso, label)"""
    now = datetime.now(timezone.utc)
    if date:
        try:
            base = datetime.strptime(date, "%Y-%m-%d").replace(
                tzinfo=timezone.utc,
            )
        except ValueError:
            base = now
    else:
        base = now

    if period == "week":
        start = base - timedelta(days=base.weekday())
        start = start.replace(hour=0, minute=0, second=0)
        end = start + timedelta(days=7)
        label = f"本周（{start.strftime('%m-%d')}~{end.strftime('%m-%d')}）"
    elif period == "month":
        start = base.replace(day=1, hour=0, minute=0, second=0)
        next_month = (start + timedelta(days=32)).replace(day=1)
        end = next_month
        label = f"{start.strftime('%Y年%m月')}"
    else:
        start = base.replace(hour=0, minute=0, second=0)
        end = start + timedelta(days=1)
        label = f"{start.strftime('%Y-%m-%d')}"

    return start.isoformat(), end.isoformat(), label


def _format_summary(
    data: dict, type_name: str, period_label: str,
    db, doc_type: str,
) -> str:
    """格式化总计统计（RPC 总计模式返回）"""
    doc_count = data.get("doc_count", 0)
    total_qty = data.get("total_qty", 0)
    total_amount = float(data.get("total_amount", 0))

    lines = [f"{period_label} {type_name}统计：\n"]
    lines.append(
        f"总计: {doc_count}笔 | 数量 {total_qty}件"
        f" | 金额 ¥{total_amount:,.2f}"
    )

    health = check_sync_health(db, [doc_type])
    if health:
        lines.append(f"\n{health}")
    return "\n".join(lines)


def _format_ranking(
    data: list, rank_by: str, type_name: str, period_label: str,
) -> str:
    """格式化排名（RPC product 分组模式返回）"""
    sort_key = {"count": "doc_count", "quantity": "total_qty", "amount": "total_amount"}
    key = sort_key.get(rank_by, "doc_count")

    ranked = sorted(data, key=lambda x: -(x.get(key) or 0))[:10]

    rank_name = {"count": "笔数", "quantity": "数量", "amount": "金额"}
    lines = [f"{period_label} {type_name}TOP10（按{rank_name.get(rank_by, rank_by)}）：\n"]
    for i, item in enumerate(ranked, 1):
        code = item.get("group_key", "未知")
        name = item.get("item_name", "")
        doc_count = item.get("doc_count", 0)
        qty = item.get("total_qty", 0)
        amt = float(item.get("total_amount", 0))
        lines.append(
            f"{i}. {code}({name}) — "
            f"{doc_count}笔 | {qty}件 | ¥{amt:,.2f}"
        )

    return "\n".join(lines)


def _format_grouped(
    data: list, group_by: str, type_name: str, period_label: str,
) -> str:
    """格式化分组统计（RPC 分组模式返回）"""
    lines = [f"{period_label} {type_name}按{group_by}分组：\n"]

    total_docs = 0
    for item in data:
        key = item.get("group_key", "未知")
        doc_count = item.get("doc_count", 0)
        qty = item.get("total_qty", 0)
        amt = float(item.get("total_amount", 0))
        total_docs += doc_count
        lines.append(
            f"  {key}: {doc_count}笔 | {qty}件"
            f" | ¥{amt:,.2f}"
        )

    lines.append(f"\n📊 总计：{total_docs}笔")
    return "\n".join(lines)
