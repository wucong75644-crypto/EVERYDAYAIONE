"""
ERP 本地编码识别工具

纯本地查询，编码识别的唯一实现。
支持三种模式：编码精确匹配、商品名模糊搜索、规格名模糊搜索。

设计文档: docs/document/TECH_ERP数据本地索引系统.md §6 工具7
"""

from __future__ import annotations

from loguru import logger


from services.kuaimai.erp_local_helpers import check_sync_health

_TYPE_MAP = {0: "普通", 1: "SKU套件", 2: "纯套件", 3: "包材"}


async def local_product_identify(
    db,
    code: str | None = None,
    name: str | None = None,
    spec: str | None = None,
) -> str:
    """本地编码识别（code/name/spec 至少传一个）"""
    if not code and not name and not spec:
        return "请提供 code、name 或 spec 至少一个参数"

    if code:
        return await _identify_by_code(db, code.strip())
    if name:
        return await _search_by_name(db, name.strip())
    return await _search_by_spec(db, spec.strip())


async def _identify_by_code(db, code: str) -> str:
    """编码模式：主编码 → SKU编码 → 条码 → 未识别"""
    # 1. 主编码匹配
    try:
        result = (
            db.table("erp_products")
            .select("*")
            .eq("outer_id", code)
            .limit(1)
            .execute()
        )
        if result.data:
            return _format_product(db, code, result.data[0])
    except Exception as e:
        logger.debug(f"Local identify product | code={code} | {e}")

    # 2. SKU编码匹配
    try:
        result = (
            db.table("erp_product_skus")
            .select("*")
            .eq("sku_outer_id", code)
            .limit(1)
            .execute()
        )
        if result.data:
            return _format_sku(db, code, result.data[0])
    except Exception as e:
        logger.debug(f"Local identify sku | code={code} | {e}")

    # 3. 条码匹配
    try:
        result = (
            db.table("erp_products")
            .select("*")
            .eq("barcode", code)
            .limit(1)
            .execute()
        )
        if result.data:
            p = result.data[0]
            return (
                f"编码识别: {code}\n"
                f"✓ 条码匹配 | 编码类型: 条码(barcode)\n"
                f"对应商品: outer_id={p['outer_id']} | 名称: {p.get('title', '')}"
            )
        # SKU 条码
        result = (
            db.table("erp_product_skus")
            .select("*")
            .eq("barcode", code)
            .limit(1)
            .execute()
        )
        if result.data:
            s = result.data[0]
            return (
                f"编码识别: {code}\n"
                f"✓ 条码匹配 | 编码类型: SKU条码(barcode)\n"
                f"对应商品: outer_id={s['outer_id']}"
                f" | sku_outer_id={s['sku_outer_id']}"
                f" | 规格: {s.get('properties_name', '')}"
            )
    except Exception as e:
        logger.debug(f"Local identify barcode | code={code} | {e}")

    # 4. API 兜底：单条查询确认是否存在
    api_result = await _api_fallback_identify(db, code)
    if api_result:
        return api_result

    # 5. 确认不存在
    return (
        f"编码识别: {code}\n"
        f"✗ 该编码在ERP中不存在（本地+API均未找到）"
    )


async def _search_by_name(db, name: str) -> str:
    """名称搜索模式：pg_trgm ILIKE"""
    try:
        result = (
            db.table("erp_products")
            .select("outer_id,title,shipper,pic_url,active_status")
            .ilike("title", f"%{name}%")
            .limit(20)
            .execute()
        )
        rows = result.data or []
    except Exception as e:
        logger.error(f"Name search failed | name={name} | error={e}")
        return f"商品名搜索失败: {e}"

    if not rows:
        health = check_sync_health(db, ["product"])
        return f"搜索\"{name}\"未匹配到商品\n{health}".strip()

    lines = [f"搜索\"{name}\"匹配到{len(rows)}个商品：\n"]
    for i, r in enumerate(rows, 1):
        status = "" if r.get("active_status", 1) != -1 else " [已停用]"
        shipper = f" | 货主: {r['shipper']}" if r.get("shipper") else ""
        pic = f"\n   图片: {r['pic_url']}" if r.get("pic_url") else ""
        # 获取 SKU 数量
        sku_count = _get_sku_count(db, r["outer_id"])
        lines.append(
            f"{i}. {r['outer_id']} — {r.get('title', '')}{status}{shipper}"
            f"\n   SKU: {sku_count}个{pic}"
        )
    return "\n".join(lines)


async def _search_by_spec(db, spec: str) -> str:
    """规格搜索模式：pg_trgm ILIKE on properties_name"""
    try:
        result = (
            db.table("erp_product_skus")
            .select("sku_outer_id,outer_id,properties_name,pic_url")
            .ilike("properties_name", f"%{spec}%")
            .limit(20)
            .execute()
        )
        rows = result.data or []
    except Exception as e:
        logger.error(f"Spec search failed | spec={spec} | error={e}")
        return f"规格搜索失败: {e}"

    if not rows:
        health = check_sync_health(db, ["product"])
        return f"搜索规格\"{spec}\"未匹配到SKU\n{health}".strip()

    # 关联商品名称
    outer_ids = list({r["outer_id"] for r in rows})
    title_map: dict[str, str] = {}
    try:
        pr = (
            db.table("erp_products")
            .select("outer_id,title")
            .in_("outer_id", outer_ids)
            .execute()
        )
        title_map = {r["outer_id"]: r.get("title", "") for r in (pr.data or [])}
    except Exception:
        pass

    lines = [f"搜索规格\"{spec}\"匹配到{len(rows)}个SKU：\n"]
    for i, r in enumerate(rows, 1):
        title = title_map.get(r["outer_id"], "")
        lines.append(
            f"{i}. {r['sku_outer_id']} — {title}"
            f" | 规格: {r.get('properties_name', '')}"
        )
    return "\n".join(lines)


# ── API 兜底 ──────────────────────────────────────────


async def _api_fallback_identify(db, code: str) -> str | None:
    """本地未找到时，调 item.single.get API 兜底

    有结果→写入本地→返回格式化文本；无结果→返回 None。
    """
    try:
        from services.kuaimai.client import KuaiMaiClient
        client = KuaiMaiClient()
        if not client.is_configured:
            await client.close()
            return None

        await client.load_cached_token()
        try:
            data = await client.request_with_retry(
                "item.single.get", {"outerId": code},
            )
        finally:
            await client.close()

        if not data or not data.get("outerId"):
            return None

        # 写入本地 erp_products（复用 sync handler 字段映射）
        _upsert_product_from_api(db, data)
        # 重新走本地查询
        result = (
            db.table("erp_products")
            .select("*")
            .eq("outer_id", data["outerId"])
            .limit(1)
            .execute()
        )
        if result.data:
            return _format_product(db, code, result.data[0])
        return None
    except Exception as e:
        logger.debug(f"API fallback identify failed | code={code} | {e}")
        return None


def _upsert_product_from_api(db, p: dict) -> None:
    """将 API 单条商品数据 upsert 到本地 erp_products + erp_product_skus"""
    import re
    outer_id = p.get("outerId")
    if not outer_id:
        return

    html_re = re.compile(r"<[^>]+>")
    remark = p.get("remark")
    if remark:
        remark = html_re.sub("", remark).strip()

    spu_row = {
        "outer_id": outer_id,
        "title": p.get("title"),
        "item_type": p.get("type", 0),
        "is_virtual": bool(p.get("isVirtual")),
        "active_status": p.get("activeStatus", 1),
        "barcode": p.get("barcode"),
        "purchase_price": p.get("purchasePrice"),
        "selling_price": p.get("priceOutput"),
        "market_price": p.get("marketPrice"),
        "weight": p.get("weight"),
        "unit": p.get("unit"),
        "is_gift": bool(p.get("makeGift")),
        "sys_item_id": p.get("sysItemId"),
        "brand": p.get("brand"),
        "shipper": p.get("shipper"),
        "remark": remark,
        "created_at": p.get("created"),
        "modified_at": p.get("modified"),
        "pic_url": p.get("picPath"),
        "suit_singles": p.get("singleList"),
    }
    try:
        db.table("erp_products").upsert(
            spu_row, on_conflict="outer_id",
        ).execute()
    except Exception as e:
        logger.warning(f"Upsert product failed | outer_id={outer_id} | {e}")

    # SKU 行
    for sku in p.get("skus") or []:
        sku_outer_id = sku.get("skuOuterId")
        if not sku_outer_id:
            continue
        sku_row = {
            "outer_id": outer_id,
            "sku_outer_id": sku_outer_id,
            "properties_name": sku.get("propertiesName"),
            "barcode": sku.get("barcode"),
            "purchase_price": sku.get("purchasePrice"),
            "selling_price": sku.get("priceOutput"),
            "market_price": sku.get("marketPrice"),
            "weight": sku.get("weight"),
            "unit": sku.get("unit"),
            "shipper": sku.get("shipper"),
            "pic_url": sku.get("skuPicPath"),
            "sys_sku_id": sku.get("sysSkuId"),
            "active_status": sku.get("activeStatus", 1),
        }
        try:
            db.table("erp_product_skus").upsert(
                sku_row, on_conflict="sku_outer_id",
            ).execute()
        except Exception as e:
            logger.warning(
                f"Upsert sku failed | sku={sku_outer_id} | {e}",
            )


# ── 格式化 ────────────────────────────────────────────


def _format_product(db, code: str, p: dict) -> str:
    """格式化主编码识别结果"""
    item_type = p.get("item_type", 0)
    type_name = _TYPE_MAP.get(item_type, str(item_type))
    status = "停用" if p.get("active_status", 1) == 0 else ""

    lines = [
        f"编码识别: {code}",
        f"✓ 商品存在 | 编码类型: 主编码(outer_id)",
        f"商品类型: {type_name}(type={item_type})"
        + (f" | 状态: {status}" if status else "")
        + (f" | 货主: {p['shipper']}" if p.get("shipper") else ""),
        f"名称: {p.get('title', '')}"
        + (f" | 条码: {p['barcode']}" if p.get("barcode") else "")
        + (f" | 采购价: ¥{p['purchase_price']}" if p.get("purchase_price") else ""),
    ]

    if p.get("pic_url"):
        lines.append(f"图片: {p['pic_url']}")
    if p.get("remark"):
        lines.append(f"备注: {p['remark']}")

    # SKU 列表
    try:
        result = (
            db.table("erp_product_skus")
            .select("sku_outer_id,properties_name")
            .eq("outer_id", code)
            .limit(10)
            .execute()
        )
        skus = result.data or []
        if skus:
            parts = [
                f"{s['sku_outer_id']}({s.get('properties_name', '')})"
                for s in skus
            ]
            lines.append(f"SKU({len(skus)}个): {', '.join(parts)}")
    except Exception:
        pass

    # 套件子单品
    if item_type in (1, 2) and p.get("suit_singles"):
        singles = p["suit_singles"]
        if isinstance(singles, list):
            parts = [
                f"{s.get('outerId', '')}(x{s.get('ratio', 1)})"
                for s in singles[:10]
            ]
            lines.append(f"套件子单品({len(singles)}个): {', '.join(parts)}")
            lines.append("⚠ 查库存: 对每个子单品用 local_stock_query 查询")

    # 关联单据统计
    doc_summary = _get_doc_summary(db, code)
    if doc_summary:
        lines.append(f"关联单据: {doc_summary}")

    return "\n".join(lines)


def _format_sku(db, code: str, s: dict) -> str:
    """格式化SKU编码识别结果"""
    lines = [
        f"编码识别: {code}",
        f"✓ 商品存在 | 编码类型: SKU编码(sku_outer_id)",
        f"对应主编码: {s.get('outer_id', '')} | 规格: {s.get('properties_name', '')}",
    ]
    if s.get("barcode"):
        lines.append(f"条码: {s['barcode']}")
    if s.get("pic_url"):
        lines.append(f"图片: {s['pic_url']}")
    return "\n".join(lines)


def _get_sku_count(db, outer_id: str) -> int:
    """获取 SKU 数量"""
    try:
        result = (
            db.table("erp_product_skus")
            .select("sku_outer_id", count="exact")
            .eq("outer_id", outer_id)
            .execute()
        )
        return result.count or 0
    except Exception:
        return 0


def _get_doc_summary(db, code: str) -> str:
    """获取关联单据统计摘要"""
    try:
        result = (
            db.table("erp_document_items")
            .select("doc_type,doc_id")
            .or_(f"outer_id.eq.{code},sku_outer_id.eq.{code}")
            .limit(1000)
            .execute()
        )
        rows = result.data or []
        if not rows:
            return ""
        # 按类型统计去重 doc_id 数
        counts: dict[str, set] = {}
        for r in rows:
            dt = r.get("doc_type", "")
            counts.setdefault(dt, set()).add(r["doc_id"])
        type_names = {
            "purchase": "采购单", "receipt": "收货单", "shelf": "上架单",
            "order": "订单", "aftersale": "售后单", "purchase_return": "采退单",
        }
        parts = [
            f"{type_names.get(dt, dt)}{len(ids)}笔"
            for dt, ids in counts.items()
        ]
        return ", ".join(parts)
    except Exception:
        return ""
