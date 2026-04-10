"""
ERP 本地查询工具（6个）

纯本地 PostgreSQL 查询，零 API 调用。
数据来源：erp_sync_service 后台同步写入的本地表。

设计文档: docs/document/TECH_ERP数据本地索引系统.md §6
时间事实层: docs/document/TECH_ERP时间准确性架构.md §6.2.2 (B5b-h)
"""

from __future__ import annotations

from typing import Optional

from loguru import logger


from services.kuaimai.erp_local_helpers import (
    check_sync_health,
    query_doc_items,
)
from utils.time_context import RequestContext, make_n_days_header


# ── 工具1：采购查询（含采退）────────────────────────────


async def local_purchase_query(
    db, product_code: str,
    status: str | None = None,
    include_return: bool = True,
    days: int = 30,
    org_id: str | None = None,
    request_ctx: Optional[RequestContext] = None,
) -> str:
    """按商品编码查采购到货进度（含采退）"""
    extra = {"doc_status": status} if status else None
    rows = query_doc_items(db, "purchase", product_code, days, extra, org_id=org_id)

    return_rows: list[dict] = []
    if include_return:
        return_rows = query_doc_items(db, "purchase_return", product_code, days, org_id=org_id)

    time_header = make_n_days_header(ctx=request_ctx, days=days, kind="查询窗口")

    if not rows and not return_rows:
        health = check_sync_health(db, ["purchase", "purchase_return"], org_id=org_id)
        return f"{time_header}\n\n商品 {product_code} 近{days}天无采购/采退记录\n{health}".strip()

    # 按 doc_id 聚合采购单
    lines = [time_header, "", f"商品 {product_code} 采购情况（近{days}天）：\n"]
    docs: dict[str, list[dict]] = {}
    for r in rows:
        docs.setdefault(r["doc_id"], []).append(r)

    total_qty, total_received, doc_count = 0, 0, 0
    for doc_id, items in docs.items():
        first = items[0]
        doc_count += 1
        qty = sum(r.get("quantity") or 0 for r in items)
        recv = sum(r.get("quantity_received") or 0 for r in items)
        total_qty += qty
        total_received += recv
        lines.append(
            f"📦 采购单 {first.get('doc_code', doc_id)}"
            f"（{first.get('doc_status', '')}）"
        )
        lines.append(f"  采购数: {qty}，已到货: {recv}")
        if first.get("supplier_name"):
            lines.append(f"  供应商: {first['supplier_name']}")
        lines.append(f"  创建时间: {str(first.get('doc_created_at', ''))[:10]}")
        lines.append("")

    # 采退单
    ret_docs: dict[str, list[dict]] = {}
    for r in return_rows:
        ret_docs.setdefault(r["doc_id"], []).append(r)
    total_ret_qty, ret_count = 0, 0
    for doc_id, items in ret_docs.items():
        first = items[0]
        ret_count += 1
        qty = sum(r.get("quantity") or 0 for r in items)
        total_ret_qty += qty
        lines.append(
            f"↩️ 采退单 {first.get('doc_code', doc_id)}"
            f"（{first.get('doc_status', '')}）"
        )
        lines.append(f"  退货数: {qty}")
        if first.get("supplier_name"):
            lines.append(f"  供应商: {first['supplier_name']}")
        lines.append(f"  创建时间: {str(first.get('doc_created_at', ''))[:10]}")
        lines.append("")

    # 汇总
    rate = f"{total_received / total_qty * 100:.1f}%" if total_qty else "N/A"
    summary = (
        f"📊 汇总：{doc_count}笔采购，总采购{total_qty}件，"
        f"已到货{total_received}件（{rate}）"
    )
    if ret_count:
        summary += f"；{ret_count}笔采退，退货{total_ret_qty}件"
    lines.append(summary)

    health = check_sync_health(db, ["purchase", "purchase_return"], org_id=org_id)
    if health:
        lines.append(health)
    return "\n".join(lines)


# ── 工具2：售后查询 ────────────────────────────────────

_AFTERSALE_TYPE_MAP = {
    "0": "其他", "1": "已发货仅退款", "2": "退货", "3": "补发",
    "4": "换货", "5": "未发货仅退款", "7": "拒收退货",
    "8": "档口退货", "9": "维修",
}


async def local_aftersale_query(
    db, product_code: str,
    aftersale_type: str | None = None,
    days: int = 30,
    org_id: str | None = None,
    request_ctx: Optional[RequestContext] = None,
) -> str:
    """按商品编码查售后情况"""
    extra = {"aftersale_type": aftersale_type} if aftersale_type else None
    rows = query_doc_items(db, "aftersale", product_code, days, extra, org_id=org_id)

    time_header = make_n_days_header(ctx=request_ctx, days=days, kind="查询窗口")

    if not rows:
        health = check_sync_health(db, ["aftersale"], org_id=org_id)
        return f"{time_header}\n\n商品 {product_code} 近{days}天无售后记录\n{health}".strip()

    # 按类型统计
    type_counts: dict[str, int] = {}
    for r in rows:
        t = str(r.get("aftersale_type", "0"))
        name = _AFTERSALE_TYPE_MAP.get(t, t)
        type_counts[name] = type_counts.get(name, 0) + 1

    lines = [time_header, "", f"商品 {product_code} 售后情况（近{days}天）：\n"]
    lines.append("📊 售后汇总：")
    for name, count in type_counts.items():
        lines.append(f"  {name}: {count}笔")
    unique_docs = {r["doc_id"] for r in rows}
    lines.append(f"  合计: {len(unique_docs)}笔工单\n")

    # 近期工单（最新5笔，按 doc_id 去重）
    seen_docs: set[str] = set()
    recent: list[dict] = []
    for r in rows:
        if r["doc_id"] not in seen_docs:
            seen_docs.add(r["doc_id"])
            recent.append(r)
        if len(recent) >= 5:
            break

    lines.append("近期售后工单（最新5笔）：")
    for i, r in enumerate(recent, 1):
        t = _AFTERSALE_TYPE_MAP.get(str(r.get("aftersale_type", "0")), "")
        lines.append(
            f"  {i}. 工单{r.get('doc_id', '')} — {t}"
            f" — {r.get('doc_status', '')}"
            f" — {str(r.get('doc_created_at', ''))[:10]}"
        )

    health = check_sync_health(db, ["aftersale"], org_id=org_id)
    if health:
        lines.append(f"\n{health}")
    return "\n".join(lines)


# ── 工具3：订单查询 ────────────────────────────────────


async def local_order_query(
    db, product_code: str,
    shop_name: str | None = None,
    platform: str | None = None,
    status: str | None = None,
    days: int = 30,
    org_id: str | None = None,
    request_ctx: Optional[RequestContext] = None,
) -> str:
    """按商品编码查销售订单"""
    rows = query_doc_items(db, "order", product_code, days, org_id=org_id)

    if shop_name:
        rows = [r for r in rows if shop_name in (r.get("shop_name") or "")]
    if platform:
        rows = [r for r in rows if platform in (r.get("platform") or "")]
    if status:
        rows = [r for r in rows if r.get("order_status") == status]

    time_header = make_n_days_header(ctx=request_ctx, days=days, kind="查询窗口")

    if not rows:
        health = check_sync_health(db, ["order"], org_id=org_id)
        return f"{time_header}\n\n商品 {product_code} 近{days}天无订单记录\n{health}".strip()

    unique_docs = {r["doc_id"] for r in rows}
    total_qty = sum(r.get("quantity") or 0 for r in rows)
    total_amount = sum(float(r.get("amount") or 0) for r in rows)

    # 按平台分组
    platform_stats: dict[str, dict] = {}
    for r in rows:
        p = r.get("platform") or "未知"
        if p not in platform_stats:
            platform_stats[p] = {"docs": set(), "amount": 0.0}
        platform_stats[p]["docs"].add(r["doc_id"])
        platform_stats[p]["amount"] += float(r.get("amount") or 0)

    lines = [time_header, "", f"商品 {product_code} 销售情况（近{days}天）：\n"]
    lines.append(f"销售汇总：总订单{len(unique_docs)}笔，销量{total_qty}件，¥{total_amount:,.2f}")

    if platform_stats:
        lines.append("\n按平台：")
        for p, s in platform_stats.items():
            lines.append(f"  {p}: {len(s['docs'])}笔 ¥{s['amount']:,.2f}")

    # 近期订单（最新5笔）
    seen: set[str] = set()
    recent: list[dict] = []
    for r in rows:
        if r["doc_id"] not in seen:
            seen.add(r["doc_id"])
            recent.append(r)
        if len(recent) >= 5:
            break

    lines.append("\n近期订单（最新5笔）：")
    for i, r in enumerate(recent, 1):
        lines.append(
            f"  {i}. {r.get('order_no', '')} — {r.get('platform', '')}"
            f" — {r.get('order_status', '')}"
            f" — {r.get('quantity', '')}件 ¥{r.get('amount', '')}"
            f" — {str(r.get('doc_created_at', ''))[:10]}"
        )

    health = check_sync_health(db, ["order"], org_id=org_id)
    if health:
        lines.append(f"\n{health}")
    return "\n".join(lines)


# ── 工具5：全链路流转 ──────────────────────────────────


async def local_product_flow(
    db, product_code: str, days: int = 30,
    org_id: str | None = None,
    request_ctx: Optional[RequestContext] = None,
) -> str:
    """按商品编码查完整流转（采购→收货→上架→销售→售后→采退）"""
    doc_types = [
        "purchase", "receipt", "shelf", "order", "aftersale", "purchase_return",
    ]
    stats: dict[str, dict] = {}
    for dt in doc_types:
        rows = query_doc_items(db, dt, product_code, days, org_id=org_id)
        unique_docs = {r["doc_id"] for r in rows}
        total_qty = sum(r.get("quantity") or 0 for r in rows)
        total_amount = sum(float(r.get("amount") or 0) for r in rows)
        stats[dt] = {"count": len(unique_docs), "qty": total_qty, "amount": total_amount}

    time_header = make_n_days_header(ctx=request_ctx, days=days, kind="查询窗口")

    if all(s["count"] == 0 for s in stats.values()):
        health = check_sync_health(db, doc_types, org_id=org_id)
        return f"{time_header}\n\n商品 {product_code} 近{days}天无流转记录\n{health}".strip()

    lines = [time_header, "", f"商品 {product_code} 全链路流转（近{days}天）：\n"]
    s = stats
    lines.append(f"采购：{s['purchase']['count']}笔，共{s['purchase']['qty']}件")
    lines.append(f"收货：{s['receipt']['count']}笔，收货{s['receipt']['qty']}件")
    lines.append(f"上架：{s['shelf']['count']}笔，上架{s['shelf']['qty']}件")
    lines.append(
        f"销售：{s['order']['count']}笔，销量{s['order']['qty']}件，"
        f"金额¥{s['order']['amount']:,.2f}"
    )
    lines.append(f"售后：{s['aftersale']['count']}笔")
    lines.append(f"采退：{s['purchase_return']['count']}笔，退{s['purchase_return']['qty']}件")

    if s["order"]["count"] > 0 and s["aftersale"]["count"] > 0:
        rate = s["aftersale"]["count"] / s["order"]["count"] * 100
        lines.append(
            f"\n售后率：{s['aftersale']['count']}/{s['order']['count']} = {rate:.1f}%"
        )

    health = check_sync_health(db, doc_types, org_id=org_id)
    if health:
        lines.append(f"\n{health}")
    return "\n".join(lines)


# ── 工具6：库存查询 ────────────────────────────────────

_STOCK_STATUS_MAP = {
    0: "未知", 1: "正常", 2: "警戒", 3: "无货", 4: "超卖", 6: "有货",
}


async def local_stock_query(
    db, product_code: str,
    stock_status: str | None = None,
    low_stock: bool = False,
    org_id: str | None = None,
) -> str:
    """按商品编码查库存状态（支持多仓分组展示）"""
    try:
        q = (
            db.table("erp_stock_status")
            .select("*")
            .or_(f"outer_id.eq.{product_code},sku_outer_id.eq.{product_code}")
        )
        if stock_status:
            q = q.eq("stock_status", stock_status)
        result = q.limit(100).execute()
        rows = result.data or []
    except Exception as e:
        logger.error(f"Stock query failed | code={product_code} | error={e}")
        return f"库存查询失败: {e}"

    # 普通库存无结果时，查套件库存物化视图
    is_kit = False
    if not rows:
        try:
            kit_q = (
                db.table("mv_kit_stock")
                .select("*")
                .or_(
                    f"outer_id.eq.{product_code},"
                    f"sku_outer_id.eq.{product_code}"
                )
            )
            if stock_status:
                kit_q = kit_q.eq("stock_status", stock_status)
            kit_result = kit_q.limit(100).execute()
            rows = kit_result.data or []
            is_kit = bool(rows)
        except Exception as e:
            logger.debug(f"Kit stock query failed | code={product_code} | error={e}")

    if not rows:
        health = check_sync_health(db, ["stock"], org_id=org_id)
        return f"商品 {product_code} 无库存记录\n{health}".strip()

    if low_stock:
        rows = [r for r in rows if (r.get("sellable_num") or 0) < 10]
        if not rows:
            return f"商品 {product_code} 无库存预警SKU"

    kit_label = "（套件，按子单品计算）" if is_kit else ""
    lines = [f"商品 {product_code} 库存状态{kit_label}：\n"]

    # 检测是否多仓
    warehouses = {r.get("warehouse_id", "") for r in rows}
    multi_warehouse = len(warehouses) > 1

    total_sellable, total_stock, total_onway = 0, 0, 0

    if multi_warehouse:
        # 多仓分组展示
        for wh_id in sorted(warehouses):
            wh_rows = [r for r in rows if r.get("warehouse_id", "") == wh_id]
            wh_sellable, wh_stock, wh_onway = 0, 0, 0
            lines.append(f"仓库: {wh_id or '默认仓'}")
            for r in wh_rows:
                s, t, o = _format_stock_row(r, lines)
                wh_sellable += s
                wh_stock += t
                wh_onway += o
            lines.append(
                f"  小计：可售{wh_sellable} | 总库存{wh_stock} | 在途{wh_onway}"
            )
            lines.append("")
            total_sellable += wh_sellable
            total_stock += wh_stock
            total_onway += wh_onway
    else:
        # 单仓或无仓库数据（保持原有逻辑）
        for r in rows:
            s, t, o = _format_stock_row(r, lines)
            total_sellable += s
            total_stock += t
            total_onway += o

    lines.append(
        f"\n📊 汇总：总可售{total_sellable}件，"
        f"总库存{total_stock}件，总在途{total_onway}件"
    )

    health = check_sync_health(db, ["stock"], org_id=org_id)
    if health:
        lines.append(health)
    return "\n".join(lines)


def _format_stock_row(
    r: dict, lines: list[str],
) -> tuple[int, int, int]:
    """格式化单行库存数据，追加到 lines，返回 (sellable, total, onway)"""
    sku = r.get("sku_outer_id") or "(SPU级)"
    spec = r.get("properties_name") or ""
    sellable = r.get("sellable_num", 0)
    total = r.get("total_stock", 0)
    lock = r.get("lock_stock", 0)
    onway = r.get("purchase_num", 0)
    st = _STOCK_STATUS_MAP.get(r.get("stock_status", 0), "未知")

    label = f"SKU {sku}"
    if spec:
        label += f"（{spec}）"
    lines.append(f"  {label}：")
    lines.append(
        f"    可售: {sellable} | 总库存: {total} | 锁定: {lock}"
        f" | 采购在途: {onway} | 状态: {st}"
    )
    return sellable, total, onway


# ── 工具8：平台映射查询 ────────────────────────────────


async def local_platform_map_query(
    db,
    product_code: str | None = None,
    num_iid: str | None = None,
    user_id: str | None = None,
    org_id: str | None = None,
) -> str:
    """下架检查：ERP编码↔平台商品映射"""
    if not product_code and not num_iid:
        return "请提供 product_code 或 num_iid"

    try:
        q = db.table("erp_product_platform_map").select("*")
        if product_code:
            q = q.eq("outer_id", product_code)
        if num_iid:
            q = q.eq("num_iid", num_iid)
        if user_id:
            q = q.eq("user_id", user_id)
        result = q.limit(100).execute()
        rows = result.data or []
    except Exception as e:
        logger.error(f"Platform map query failed | error={e}")
        return f"平台映射查询失败: {e}"

    if not rows:
        health = check_sync_health(db, ["platform_map"], org_id=org_id)
        code = product_code or num_iid
        return f"编码 {code} 无平台映射记录\n{health}".strip()

    code = product_code or rows[0].get("outer_id", "")
    product_info = ""
    try:
        pr_q = (
            db.table("erp_products")
            .select("title,shipper,active_status")
            .eq("outer_id", code)
        )
        pr = pr_q.limit(1).execute()
        if pr.data:
            p = pr.data[0]
            st = "启用" if p.get("active_status", 1) != -1 else "已删除"
            product_info = (
                f"商品名称: {p.get('title', '')} | "
                f"货主: {p.get('shipper', '')} | 状态: {st}"
            )
    except Exception:
        pass

    lines = [f"商品 {code} 平台上架情况：\n"]
    if product_info:
        lines.append(product_info)
        lines.append("")

    lines.append(f"平台映射（共{len(rows)}条）：")
    for i, r in enumerate(rows, 1):
        sku_count = len(r.get("sku_mappings") or [])
        lines.append(
            f"  {i}. 店铺{r.get('user_id', '')} — "
            f"平台ID: {r.get('num_iid', '')} — SKU映射: {sku_count}个"
        )

    if len(rows) > 1:
        lines.append(f"\n⚠ 下架此商品将影响 {len(rows)} 个店铺的商品链接！")

    health = check_sync_health(db, ["platform_map"], org_id=org_id)
    if health:
        lines.append(health)
    return "\n".join(lines)


# ── 工具7：店铺列表查询 ────────────────────────────────


async def local_shop_list(
    db,
    platform: str | None = None,
    org_id: str | None = None,
) -> str:
    """查询本地店铺列表（优先 erp_shops 同步表，降级到订单提取）

    数据来源：erp_shops 表（API 全量同步），包含未出单的新店。
    降级：erp_shops 无数据时回退到 erp_distinct_shops RPC（仅出过单的店铺）。
    """
    rows: list[dict] = []

    # 优先查 erp_shops 同步表
    try:
        q = db.table("erp_shops").select("name, platform, state, shop_id, short_name")
        if org_id:
            q = q.eq("org_id", org_id)
        else:
            q = q.is_("org_id", "null")
        if platform:
            q = q.eq("platform", platform)
        result = q.order("platform").execute()
        rows = result.data or []
    except Exception:
        pass  # 表可能尚未创建，降级

    if rows:
        # 状态枚举：1=停用, 2=未初始化, 3=启用, 4=会话失效
        state_map = {1: "停用", 2: "未初始化", 3: "启用", 4: "会话失效"}
        by_platform: dict[str, list[dict]] = {}
        for r in rows:
            plat = r.get("platform") or "未知"
            by_platform.setdefault(plat, []).append(r)

        _plat_cn = {
            "tb": "淘宝", "jd": "京东", "pdd": "拼多多",
            "fxg": "抖音", "kuaishou": "快手", "xhs": "小红书",
            "1688": "1688", "sys": "系统", "wd": "微店",
        }
        total = len(rows)
        lines = [f"共 {total} 个店铺：\n"]
        for plat, shops in sorted(by_platform.items()):
            plat_label = _plat_cn.get(plat, plat)
            lines.append(f"【{plat_label}】({len(shops)}个)")
            for i, s in enumerate(shops, 1):
                name = s.get("name") or s.get("short_name") or "未命名"
                state = state_map.get(s.get("state"), "")
                sid = s.get("shop_id", "")
                suffix = f" [{state}]" if state and state != "启用" else ""
                lines.append(f"  {i}. {name}{suffix} (ID:{sid})")
            lines.append("")

        health = check_sync_health(db, ["shop"], org_id=org_id)
        if health:
            lines.append(health)
        return "\n".join(lines)

    # 降级：从订单数据提取
    try:
        params = {"p_org_id": org_id, "p_platform": platform or None}
        result = db.rpc("erp_distinct_shops", params).execute()
    except Exception as e:
        logger.error(f"local_shop_list RPC failed | error={e}")
        return f"店铺列表查询失败: {e}"

    if not result.data:
        platform_label = f"（平台: {platform}）" if platform else ""
        health = check_sync_health(db, ["order"], org_id=org_id)
        return f"暂无店铺数据{platform_label}\n{health}".strip()

    seen: dict[str, str] = {}
    for row in result.data:
        name = (row.get("shop_name") or "").strip()
        plat = row.get("platform") or "未知"
        if name and name not in seen:
            seen[name] = plat

    by_platform: dict[str, list[str]] = {}
    for name, plat in sorted(seen.items(), key=lambda x: (x[1], x[0])):
        by_platform.setdefault(plat, []).append(name)

    lines = [f"共 {len(seen)} 个店铺（来源: 订单数据，可能不含新店）：\n"]
    for plat, shops in sorted(by_platform.items()):
        lines.append(f"【{plat}】({len(shops)}个)")
        for i, name in enumerate(shops, 1):
            lines.append(f"  {i}. {name}")
        lines.append("")

    health = check_sync_health(db, ["order"], org_id=org_id)
    if health:
        lines.append(health)
    return "\n".join(lines)


async def local_warehouse_list(
    db,
    is_virtual: bool | None = None,
    org_id: str | None = None,
) -> str:
    """查询本地仓库列表（erp_warehouses 同步表）

    包含实体仓库和虚拟仓库，返回名称/编码/类型/状态/地址。
    """
    try:
        q = db.table("erp_warehouses").select(
            "warehouse_id, name, code, warehouse_type, status, "
            "is_virtual, contact, contact_phone, province, city, district, address"
        )
        if org_id:
            q = q.eq("org_id", org_id)
        else:
            q = q.is_("org_id", "null")
        if is_virtual is not None:
            q = q.eq("is_virtual", is_virtual)
        result = q.order("is_virtual").order("name").execute()
    except Exception as e:
        logger.error(f"local_warehouse_list failed | error={e}")
        return f"仓库列表查询失败: {e}"

    rows = result.data or []
    if not rows:
        health = check_sync_health(db, ["warehouse"], org_id=org_id)
        return f"暂无仓库数据\n{health}".strip()

    type_map = {0: "自有", 1: "第三方", 2: "门店"}
    status_map = {0: "停用", 1: "正常", 2: "禁止发货"}

    real_wh = [r for r in rows if not r.get("is_virtual")]
    virtual_wh = [r for r in rows if r.get("is_virtual")]

    lines = [f"共 {len(rows)} 个仓库（实体 {len(real_wh)}，虚拟 {len(virtual_wh)}）：\n"]

    if real_wh:
        lines.append("【实体仓库】")
        for i, w in enumerate(real_wh, 1):
            name = w.get("name") or "未命名"
            code = w.get("code") or ""
            wtype = type_map.get(w.get("warehouse_type"), "")
            status = status_map.get(w.get("status"), "")
            addr_parts = [w.get(k) or "" for k in ("province", "city", "district")]
            addr = "".join(addr_parts)
            if w.get("address"):
                addr += w["address"]
            code_str = f" 编码:{code}" if code else ""
            type_str = f" {wtype}" if wtype else ""
            status_str = f" [{status}]" if status and status != "正常" else ""
            addr_str = f" 地址:{addr}" if addr.strip() else ""
            lines.append(f"  {i}. {name}{code_str}{type_str}{status_str}{addr_str}")
        lines.append("")

    if virtual_wh:
        lines.append("【虚拟仓库】")
        for i, w in enumerate(virtual_wh, 1):
            lines.append(f"  {i}. {w.get('name') or '未命名'}")
        lines.append("")

    health = check_sync_health(db, ["warehouse"], org_id=org_id)
    if health:
        lines.append(health)
    return "\n".join(lines)
