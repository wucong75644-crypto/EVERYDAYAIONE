"""
ERP 本地全局统计/排名查询

无需 product_code，支持按时间/类型/店铺/供应商/平台统计，
支持分组和排名。通过 DB 端 RPC 聚合，无 LIMIT 截断。

设计文档: docs/document/TECH_ERP本地优先统一查询架构.md §6 工具2
"""

from __future__ import annotations

from datetime import datetime, timedelta

from loguru import logger

from services.kuaimai.erp_local_helpers import check_sync_health, CN_TZ

_DOC_TYPE_NAMES = {
    "purchase": "采购",
    "receipt": "收货",
    "shelf": "上架",
    "order": "订单",
    "aftersale": "售后",
    "purchase_return": "采退",
}


_VALID_TIME_TYPES = {"doc_created_at", "pay_time", "consign_time"}

_TIME_TYPE_LABELS = {
    "doc_created_at": "下单",
    "pay_time": "付款",
    "consign_time": "发货",
}


async def local_global_stats(
    db,
    doc_type: str,
    date: str | None = None,
    period: str = "day",
    time_type: str | None = None,
    shop_name: str | None = None,
    platform: str | None = None,
    supplier_name: str | None = None,
    warehouse_name: str | None = None,
    rank_by: str | None = None,
    group_by: str | None = None,
    org_id: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
) -> str:
    """全局统计/排名（DB 端 RPC 聚合，无 LIMIT 截断）"""
    if start_time or end_time:
        if not (start_time and end_time):
            return "参数错误: start_time 和 end_time 必须同时提供"
        try:
            start_iso, end_iso, period_label = _parse_custom_range(
                start_time, end_time,
            )
        except ValueError as e:
            return f"参数错误: {e}"
    else:
        start_iso, end_iso, period_label = _calc_period(date, period)
    type_name = _DOC_TYPE_NAMES.get(doc_type, doc_type)

    # 校验 time_type（防注入）
    time_col = time_type if time_type in _VALID_TIME_TYPES else "doc_created_at"

    # 确定 RPC 的 group_by 参数
    rpc_group = group_by or _rank_by_to_group(rank_by)

    params = {
        "p_doc_type": doc_type,
        "p_start": start_iso,
        "p_end": end_iso,
        "p_time_col": time_col,
        "p_shop": shop_name or None,
        "p_platform": platform or None,
        "p_supplier": supplier_name or None,
        "p_warehouse": warehouse_name or None,
        "p_group_by": rpc_group,
        "p_limit": 20,
        "p_org_id": org_id,
    }

    try:
        result = db.rpc("erp_global_stats_query", params).execute()
        data = result.data
    except Exception as e:
        logger.error(f"local_global_stats RPC failed | error={e}", exc_info=True)
        return f"统计查询失败: {e}"

    # RPC 返回校验
    if not data or data == {} or data == []:
        health = check_sync_health(db, [doc_type], org_id=org_id)
        return f"{type_name}{period_label}内无记录\n{health}".strip()

    if isinstance(data, dict) and "error" in data:
        return f"查询参数错误: {data['error']}"

    # 时间类型标注（非默认时附加说明）
    time_label = ""
    if time_col != "doc_created_at":
        time_label = f"（按{_TIME_TYPE_LABELS.get(time_col, time_col)}时间）"

    # 根据返回类型格式化
    if rpc_group is None:
        return _format_summary(
            data, type_name, period_label + time_label, db, doc_type, org_id=org_id,
        )

    if rank_by:
        return _format_ranking(data, rank_by, type_name, period_label)

    return _format_grouped(data, group_by or "", type_name, period_label)


def _rank_by_to_group(rank_by: str | None) -> str | None:
    """rank_by 映射到 RPC 的 p_group_by（排名需要按 product 分组）"""
    if rank_by:
        return "product"
    return None


def _try_parse_time(raw: str, is_end: bool = False) -> datetime | None:
    """解析时间字符串，支持 ISO 格式及空格分隔，失败返回 None

    is_end=True 时，纯日期（YYYY-MM-DD）自动补全为 23:59:59。
    """
    normalized = raw.strip().replace(" ", "T")
    try:
        dt = datetime.fromisoformat(normalized)
        # 纯日期输入（无时分秒）：start 补 00:00:00，end 补 23:59:59
        if is_end and "T" not in normalized and dt.hour == 0 and dt.minute == 0:
            dt = dt.replace(hour=23, minute=59, second=59)
        return dt.replace(tzinfo=CN_TZ) if dt.tzinfo is None else dt.astimezone(CN_TZ)
    except ValueError:
        return None


def _parse_custom_range(
    start_time: str, end_time: str,
) -> tuple[str, str, str]:
    """解析自定义时间范围（支持精确到秒），失败抛 ValueError"""
    start_dt = _try_parse_time(start_time)
    if start_dt is None:
        raise ValueError(
            f"无法解析 start_time='{start_time}'，"
            "格式应为 YYYY-MM-DD HH:MM 或 YYYY-MM-DD HH:MM:SS"
        )
    end_dt = _try_parse_time(end_time, is_end=True)
    if end_dt is None:
        raise ValueError(
            f"无法解析 end_time='{end_time}'，"
            "格式应为 YYYY-MM-DD HH:MM 或 YYYY-MM-DD HH:MM:SS"
        )
    if start_dt >= end_dt:
        raise ValueError(
            f"start_time({start_time}) >= end_time({end_time})"
        )

    s_str = start_dt.strftime("%m-%d %H:%M")
    e_str = end_dt.strftime("%m-%d %H:%M")
    label = f"{s_str} ~ {e_str}"
    return start_dt.isoformat(), end_dt.isoformat(), label


def _calc_period(
    date: str | None, period: str,
) -> tuple[str, str, str]:
    """计算统计时间范围，返回 (start_iso, end_iso, label)"""
    now = datetime.now(CN_TZ)
    if date:
        try:
            base = datetime.strptime(date, "%Y-%m-%d").replace(
                tzinfo=CN_TZ,
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
    db, doc_type: str, org_id: str | None = None,
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

    health = check_sync_health(db, [doc_type], org_id=org_id)
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
