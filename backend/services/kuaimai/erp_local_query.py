"""
ERP 本地查询工具（4个专用工具）

纯本地 PostgreSQL 查询，零 API 调用。
仅保留查不同表的工具（stock/platform_map/shop/warehouse），
erp_document_items 查询已迁移至 erp_unified_query.py。

所有函数返回 ToolOutput（Phase 0 改造）。

设计文档: docs/document/TECH_ERP数据本地索引系统.md §6
重构文档: docs/document/TECH_多Agent单一职责重构.md §4.3
"""

from __future__ import annotations

from typing import Optional

from loguru import logger

from services.agent.tool_output import (
    ColumnMeta,
    OutputFormat,
    OutputStatus,
    ToolOutput,
)
from services.kuaimai.erp_local_helpers import check_sync_health
from services.kuaimai.erp_unified_schema import PLATFORM_CN


# ── 库存查询（erp_stock_status 表）───────────────────────

_STOCK_STATUS_MAP = {
    0: "未知", 1: "正常", 2: "警戒", 3: "无货", 4: "超卖", 6: "有货",
}


_STOCK_COLUMNS = [
    ColumnMeta("outer_id", "text", "商品编码"),
    ColumnMeta("sku_outer_id", "text", "SKU编码"),
    ColumnMeta("properties_name", "text", "规格"),
    ColumnMeta("warehouse_id", "text", "仓库ID"),
    ColumnMeta("sellable_num", "integer", "可售库存"),
    ColumnMeta("total_stock", "integer", "总库存"),
    ColumnMeta("lock_stock", "integer", "锁定库存"),
    ColumnMeta("purchase_num", "integer", "采购在途"),
    ColumnMeta("stock_status", "integer", "库存状态"),
]


async def local_stock_query(
    db, product_code: str,
    stock_status: str | None = None,
    low_stock: bool = False,
    org_id: str | None = None,
) -> ToolOutput:
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
        return ToolOutput(
            summary=f"库存查询失败: {e}",
            source="warehouse",
            status=OutputStatus.ERROR,
            error_message=str(e),
        )

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
        return ToolOutput(
            summary=f"商品 {product_code} 无库存记录\n{health}".strip(),
            source="warehouse",
            status=OutputStatus.EMPTY,
            metadata={"product_code": product_code},
        )

    if low_stock:
        rows = [r for r in rows if (r.get("sellable_num") or 0) < 10]
        if not rows:
            return ToolOutput(
                summary=f"商品 {product_code} 无库存预警SKU",
                source="warehouse",
                status=OutputStatus.EMPTY,
                metadata={"product_code": product_code},
            )

    # ── 构建 summary 文本（给 LLM 阅读）──
    kit_label = "（套件，按子单品计算）" if is_kit else ""
    lines = [f"商品 {product_code} 库存状态{kit_label}：\n"]

    warehouses = {r.get("warehouse_id", "") for r in rows}
    multi_warehouse = len(warehouses) > 1

    wh_name_map: dict[str, str] = {}
    wh_ids = [w for w in warehouses if w]
    if wh_ids:
        try:
            wh_q = (
                db.table("erp_warehouses")
                .select("warehouse_id,name")
                .in_("warehouse_id", wh_ids)
            )
            if org_id:
                wh_q = wh_q.eq("org_id", org_id)
            wh_res = wh_q.execute()
            for w in wh_res.data or []:
                wh_name_map[w["warehouse_id"]] = w.get("name") or w["warehouse_id"]
        except Exception as e:
            logger.debug(f"Warehouse name lookup failed: {e}")

    total_sellable, total_stock, total_onway = 0, 0, 0

    if multi_warehouse:
        for wh_id in sorted(warehouses):
            wh_rows = [r for r in rows if r.get("warehouse_id", "") == wh_id]
            wh_sellable, wh_stock, wh_onway = 0, 0, 0
            wh_label = wh_name_map.get(wh_id, wh_id) if wh_id else "默认仓"
            lines.append(f"仓库: {wh_label}")
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

    return ToolOutput(
        summary="\n".join(lines),
        format=OutputFormat.TABLE,
        source="warehouse",
        columns=_STOCK_COLUMNS,
        data=rows,
        metadata={"product_code": product_code, "is_kit": is_kit},
    )


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


# ── 平台映射查询（erp_product_platform_map 表）────────────


_PLATFORM_MAP_COLUMNS = [
    ColumnMeta("outer_id", "text", "商品编码"),
    ColumnMeta("num_iid", "text", "平台商品ID"),
    ColumnMeta("user_id", "text", "店铺ID"),
    ColumnMeta("sku_mappings", "text", "SKU映射"),
]


async def local_platform_map_query(
    db,
    product_code: str | None = None,
    num_iid: str | None = None,
    user_id: str | None = None,
    org_id: str | None = None,
) -> ToolOutput:
    """下架检查：ERP编码↔平台商品映射"""
    if not product_code and not num_iid:
        return ToolOutput(
            summary="请提供 product_code 或 num_iid",
            source="warehouse",
            status=OutputStatus.ERROR,
            error_message="缺少必填参数",
        )

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
        return ToolOutput(
            summary=f"平台映射查询失败: {e}",
            source="warehouse",
            status=OutputStatus.ERROR,
            error_message=str(e),
        )

    if not rows:
        health = check_sync_health(db, ["platform_map"], org_id=org_id)
        code = product_code or num_iid
        return ToolOutput(
            summary=f"编码 {code} 无平台映射记录\n{health}".strip(),
            source="warehouse",
            status=OutputStatus.EMPTY,
            metadata={"product_code": code},
        )

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

    shop_ids = list({r.get("user_id", "") for r in rows if r.get("user_id")})
    shop_name_map: dict[str, str] = {}
    if shop_ids:
        try:
            shop_q = (
                db.table("erp_shops")
                .select("shop_id,name,platform")
                .in_("shop_id", shop_ids)
            )
            if org_id:
                shop_q = shop_q.eq("org_id", org_id)
            shop_res = shop_q.execute()
            for s in shop_res.data or []:
                label = s.get("name") or s.get("shop_id", "")
                if s.get("platform"):
                    label += f"({PLATFORM_CN.get(s['platform'], s['platform'])})"
                shop_name_map[s["shop_id"]] = label
        except Exception as e:
            logger.debug(f"Shop name lookup failed: {e}")

    # ── 构建 summary ──
    lines = [f"商品 {code} 平台上架情况：\n"]
    if product_info:
        lines.append(product_info)
        lines.append("")

    lines.append(f"平台映射（共{len(rows)}条）：")
    for i, r in enumerate(rows, 1):
        sku_count = len(r.get("sku_mappings") or [])
        uid = r.get("user_id", "")
        shop_label = shop_name_map.get(uid, uid)
        lines.append(
            f"  {i}. {shop_label} — "
            f"平台ID: {r.get('num_iid', '')} — SKU映射: {sku_count}个"
        )

    if len(rows) > 1:
        lines.append(f"\n⚠ 下架此商品将影响 {len(rows)} 个店铺的商品链接！")

    health = check_sync_health(db, ["platform_map"], org_id=org_id)
    if health:
        lines.append(health)

    return ToolOutput(
        summary="\n".join(lines),
        format=OutputFormat.TABLE,
        source="warehouse",
        columns=_PLATFORM_MAP_COLUMNS,
        data=rows,
        metadata={"product_code": code},
    )


# ── 店铺列表（erp_shops 表）─────────────────────────────


async def local_shop_list(
    db,
    platform: str | None = None,
    org_id: str | None = None,
) -> ToolOutput:
    """查询本地店铺列表（优先 erp_shops 同步表，降级到订单提取）

    返回 ToolOutput(TEXT) — 纯列表，不需要 DATA_REF。
    """
    rows: list[dict] = []

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
        pass

    if rows:
        state_map = {1: "停用", 2: "未初始化", 3: "启用", 4: "会话失效"}
        by_platform: dict[str, list[dict]] = {}
        for r in rows:
            plat = r.get("platform") or "未知"
            by_platform.setdefault(plat, []).append(r)

        total = len(rows)
        lines = [f"共 {total} 个店铺：\n"]
        for plat, shops in sorted(by_platform.items()):
            plat_label = PLATFORM_CN.get(plat, plat)
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
        return ToolOutput(summary="\n".join(lines), source="warehouse")

    # 降级：从订单数据提取
    try:
        params = {"p_org_id": org_id, "p_platform": platform or None}
        result = db.rpc("erp_distinct_shops", params).execute()
    except Exception as e:
        logger.error(f"local_shop_list RPC failed | error={e}")
        return ToolOutput(
            summary=f"店铺列表查询失败: {e}",
            source="warehouse",
            status=OutputStatus.ERROR,
            error_message=str(e),
        )

    if not result.data:
        platform_label = f"（平台: {PLATFORM_CN.get(platform, platform)}）" if platform else ""
        health = check_sync_health(db, ["order"], org_id=org_id)
        return ToolOutput(
            summary=f"暂无店铺数据{platform_label}\n{health}".strip(),
            source="warehouse",
            status=OutputStatus.EMPTY,
        )

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
    return ToolOutput(summary="\n".join(lines), source="warehouse")


# ── 仓库列表（erp_warehouses 表）────────────────────────


async def local_warehouse_list(
    db,
    is_virtual: bool | None = None,
    org_id: str | None = None,
) -> ToolOutput:
    """查询本地仓库列表（erp_warehouses 同步表）

    返回 ToolOutput(TEXT) — 纯列表，不需要 DATA_REF。
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
        return ToolOutput(
            summary=f"仓库列表查询失败: {e}",
            source="warehouse",
            status=OutputStatus.ERROR,
            error_message=str(e),
        )

    rows = result.data or []
    if not rows:
        health = check_sync_health(db, ["warehouse"], org_id=org_id)
        return ToolOutput(
            summary=f"暂无仓库数据\n{health}".strip(),
            source="warehouse",
            status=OutputStatus.EMPTY,
        )

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
    return ToolOutput(summary="\n".join(lines), source="warehouse")


# ── 供应商列表 ──────────────────────────────────────────


async def local_supplier_list(
    db,
    category: str | None = None,
    status: int | None = None,
    org_id: str | None = None,
) -> ToolOutput:
    """查询本地供应商列表（erp_suppliers 同步表）

    返回 ToolOutput(TEXT) — 按分类分组展示，不需要 DATA_REF。
    """
    try:
        q = db.table("erp_suppliers").select(
            "code, name, status, contact_name, mobile, "
            "category_name, remark"
        )
        if org_id:
            q = q.eq("org_id", org_id)
        else:
            q = q.is_("org_id", "null")
        if status is not None:
            q = q.eq("status", status)
        if category:
            q = q.ilike("category_name", f"%{category}%")
        result = q.order("name").execute()
    except Exception as e:
        logger.error(f"local_supplier_list failed | error={e}")
        return ToolOutput(
            summary=f"供应商列表查询失败: {e}",
            source="purchase",
            status=OutputStatus.ERROR,
            error_message=str(e),
        )

    rows = result.data or []
    if not rows:
        health = check_sync_health(db, ["supplier"], org_id=org_id)
        return ToolOutput(
            summary=f"暂无供应商数据\n{health}".strip(),
            source="purchase",
            status=OutputStatus.EMPTY,
        )

    status_map = {0: "停用", 1: "启用"}

    # 按 category_name 分组
    by_category: dict[str, list[dict]] = {}
    for r in rows:
        cat = r.get("category_name") or "未分类"
        by_category.setdefault(cat, []).append(r)

    lines = [f"共 {len(rows)} 个供应商：\n"]
    for cat, suppliers in sorted(by_category.items()):
        lines.append(f"【{cat}】({len(suppliers)}个)")
        for i, s in enumerate(suppliers, 1):
            name = s.get("name") or "未命名"
            code = s.get("code") or ""
            st = status_map.get(s.get("status"), "")
            contact = s.get("contact_name") or ""
            mobile = s.get("mobile") or ""
            status_str = f" [{st}]" if st and st != "启用" else ""
            contact_str = f" 联系人:{contact}" if contact else ""
            mobile_str = f" {mobile}" if mobile else ""
            lines.append(
                f"  {i}. {name} (编码:{code}){status_str}"
                f"{contact_str}{mobile_str}"
            )
        lines.append("")

    health = check_sync_health(db, ["supplier"], org_id=org_id)
    if health:
        lines.append(health)
    return ToolOutput(summary="\n".join(lines), source="purchase")
