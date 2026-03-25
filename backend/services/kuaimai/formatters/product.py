"""
商品/库存 格式化器（Phase 5B 标签映射表模式）

从 service.py 迁移: _format_product, _format_product_detail, _format_inventory
新增: SKU列表、分类、品牌等格式化
"""

from typing import Any, Callable, Dict

from services.kuaimai.formatters.common import format_item_with_labels, format_timestamp
from services.kuaimai.registry.base import ApiEntry

# ---------------------------------------------------------------------------
# 库存状态 — stock.api.status.query
# ---------------------------------------------------------------------------
_INVENTORY_LABELS = {
    "title": "名称", "mainOuterId": "编码",
    "outerId": "SKU编码", "skuOuterId": "规格编码",
    "propertiesName": "规格",
    "totalAvailableStockSum": "总库存", "sellableNum": "可售",
    "totalAvailableStock": "实际可用",
    "totalLockStock": "锁定", "purchaseNum": "采购在途",
    "onTheWayNum": "销退在途",
    "allocateNum": "调拨", "totalDefectiveStock": "残次品",
    "refundStock": "退款库存",
    "purchaseStock": "入库暂存",
    "virtualStock": "虚拟库存",
    "purchasePrice": "采购价", "sellingPrice": "销售价", "marketPrice": "市场价",
    "stockStatus": "状态", "wareHouseId": "仓库ID",
    "brand": "品牌", "cidName": "分类",
    "unit": "单位", "place": "产地",
    "stockModifiedTime": "库存更新时间",
    "itemBarcode": "条码", "skuBarcode": "SKU条码",
    "supplierCodes": "供应商编码", "supplierNames": "供应商",
}
_INVENTORY_SKIP = {"shortTitle"}
_INVENTORY_TRANSFORMS: Dict[str, Callable] = {
    "stockStatus": lambda v: {0: "正常", 1: "正常", 2: "警戒", 3: "无货", 4: "超卖",
                              6: "有货"}.get(v, str(v)),
    "purchasePrice": lambda v: f"¥{v}",
    "sellingPrice": lambda v: f"¥{v}",
    "marketPrice": lambda v: f"¥{v}",
    "stockModifiedTime": format_timestamp,
}

# ---------------------------------------------------------------------------
# 仓库库存 — erp.item.warehouse.list.get
# 注意: 此API不返回 purchaseNum（仅 stock.api.status.query 有）
# ---------------------------------------------------------------------------
_WH_STOCK_LABELS = {
    "name": "仓库", "id": "仓库ID", "code": "仓库编码",
    "totalAvailableStockSum": "总库存", "sellableNum": "可售",
    "totalAvailableStock": "实际可用",
    "totalLockStock": "锁定", "totalDefectiveStock": "次品",
    "stockStatus": "库存状态", "status": "仓库状态",
}
_WH_STOCK_TRANSFORMS: Dict[str, Callable] = {
    "stockStatus": lambda v: {1: "正常", 2: "警戒", 3: "无货",
                              4: "超卖", 6: "有货"}.get(v, str(v)),
    "status": lambda v: {0: "停用", 1: "正常", 2: "禁止发货"}.get(v, str(v)),
}

# ---------------------------------------------------------------------------
# 出入库流水 — erp.item.stock.in.out.list
# 响应key: stockInOutRecordVos，字段已通过API实测验证(2026-03-18)
# ---------------------------------------------------------------------------
_STOCK_IO_LABELS = {
    "outerId": "编码", "title": "名称",
    "propertiesName": "规格",
    "orderType": "单据类型", "stockChange": "变动数量",
    "warehouseName": "仓库", "orderNumber": "单据号",
    "operateTime": "时间",
}
_STOCK_IO_TRANSFORMS: Dict[str, Callable] = {"operateTime": format_timestamp}

# ---------------------------------------------------------------------------
# 商品列表 — item.list.query
# 关键: 销售价字段名是 priceOutput（非 sellingPrice）
# ---------------------------------------------------------------------------
_PRODUCT_LABELS = {
    "title": "名称", "outerId": "编码", "barcode": "条码",
    "type": "商品类型",
    "weight": "重量", "unit": "单位",
    "purchasePrice": "采购价",
    "priceOutput": "销售价",
    "marketPrice": "市场价",
    "brand": "品牌",
    "isSkuItem": "多规格", "isVirtual": "虚拟商品", "makeGift": "赠品",
    "activeStatus": "状态",
    "remark": "备注",
}
_PRODUCT_SKIP = {"shortTitle"}
_PRODUCT_TRANSFORMS: Dict[str, Callable] = {
    "type": lambda v: {0: "普通", 1: "SKU套件", 2: "纯套件", 3: "包材"}.get(v, str(v)),
    "isSkuItem": lambda v: "是" if v else "否",
    "isVirtual": lambda v: "是" if v else "否",
    "makeGift": lambda v: "是" if v else "否",
    "activeStatus": lambda v: "启用" if v == 1 else "停用",
    "weight": lambda v: f"{v}g" if v else "",
    "purchasePrice": lambda v: f"¥{v}",
    "priceOutput": lambda v: f"¥{v}",
    "marketPrice": lambda v: f"¥{v}",
}

# ---------------------------------------------------------------------------
# SKU行
# ---------------------------------------------------------------------------
_SKU_LABELS = {
    "skuOuterId": "编码", "propertiesName": "规格",
    "barcode": "条码",
    "weight": "重量",
    "purchasePrice": "采购价", "priceOutput": "销售价", "marketPrice": "市场价",
    "unit": "单位",
    "activeStatus": "状态",
}
_SKU_TRANSFORMS: Dict[str, Callable] = {
    "activeStatus": lambda v: "启用" if v == 1 else "停用",
    "weight": lambda v: f"{v}g" if v else "",
    "purchasePrice": lambda v: f"¥{v}",
    "priceOutput": lambda v: f"¥{v}",
    "marketPrice": lambda v: f"¥{v}",
}


# ===== 公开 formatter 函数 =====

def format_product_list(data: Any, entry: ApiEntry) -> str:
    """商品列表"""
    items = data.get("items") or data.get("list") or []
    total = data.get("total", len(items))
    if not items:
        return "查询返回 0 条商品"
    lines = [f"共找到 {total} 个商品：\n"]
    for item in items[:20]:
        lines.append("- " + format_item_with_labels(
            item, _PRODUCT_LABELS, _PRODUCT_SKIP, _PRODUCT_TRANSFORMS))
    if int(total) > len(items):
        lines.append(f"\n（显示前{len(items)}个，共{total}个）")
    return "\n".join(lines)


def format_product_detail(data: Any, entry: ApiEntry) -> str:
    """商品详情"""
    title = data.get("title", "")
    lines = [f"商品详情：{title}"]
    lines.append(format_item_with_labels(
        data, _PRODUCT_LABELS, _PRODUCT_SKIP, _PRODUCT_TRANSFORMS))

    cats = data.get("sellerCats") or []
    if cats:
        cat_names = [c.get("name", "") for c in cats if c.get("name")]
        if cat_names:
            lines.append(f"分类: {' > '.join(cat_names)}")

    # 套件子单品
    suit_singles = data.get("suitSingleList") or []
    if suit_singles:
        lines.append(f"\n套件子单品({len(suit_singles)}个)：")
        for s in suit_singles[:20]:
            outer = s.get("outerId", "")
            title_s = s.get("title", "")
            ratio = s.get("ratio", 1)
            sku_outer = s.get("skuOuterId", "")
            spec = s.get("propertiesName", "")
            parts = [f"  - {outer} {title_s} x{ratio}"]
            if sku_outer:
                parts.append(f"sku={sku_outer}")
            if spec:
                parts.append(spec)
            lines.append(" | ".join(parts))

    skus = data.get("items") or []
    if skus:
        lines.append(f"\nSKU列表（共{len(skus)}个）：")
        for sku in skus[:10]:
            lines.append("  - " + format_item_with_labels(
                sku, _SKU_LABELS, transforms=_SKU_TRANSFORMS))
    return "\n".join(lines)


def format_inventory_list(data: Any, entry: ApiEntry) -> str:
    """库存状态列表"""
    items = data.get("stockStatusVoList") or data.get("list") or []
    total = data.get("total", len(items))
    if not items:
        return "查询返回 0 条库存记录"
    lines = [f"共找到 {total} 条库存记录：\n"]
    for item in items[:100]:
        lines.append("- " + format_item_with_labels(
            item, _INVENTORY_LABELS, _INVENTORY_SKIP, _INVENTORY_TRANSFORMS))
    if int(total) > len(items):
        lines.append(f"\n（显示前{len(items)}条，共{total}条）")
    return "\n".join(lines)


def format_sku_info(data: Any, entry: ApiEntry) -> str:
    """SKU信息"""
    items = data.get("items") or data.get("list") or []
    if isinstance(data, dict) and not items:
        # 可能是单个SKU详情
        return "SKU详情: " + format_item_with_labels(
            data, _SKU_LABELS, transforms=_SKU_TRANSFORMS)
    if not items:
        return "未找到SKU信息"
    lines = [f"共 {len(items)} 个SKU：\n"]
    for sku in items[:30]:
        lines.append("- " + format_item_with_labels(
            sku, _SKU_LABELS, transforms=_SKU_TRANSFORMS))
    return "\n".join(lines)


def format_warehouse_stock(data: Any, entry: ApiEntry) -> str:
    """仓库及商品库存"""
    items = data.get("list") or []
    if not items:
        return "查询返回 0 条仓库库存"
    lines = [f"共 {len(items)} 条仓库库存：\n"]
    for item in items[:50]:
        # 顶层有 outerId，嵌套 skus[] -> mainWareHousesStock[]
        outer_id = item.get("outerId") or ""
        skus = item.get("skus") or []
        if skus:
            # 嵌套结构：展开每个SKU的每个仓库（加上限防爆）
            for sku in skus[:10]:
                sku_code = sku.get("skuOuterId") or outer_id
                wh_stocks = sku.get("mainWareHousesStock") or []
                for wh in wh_stocks[:10]:
                    prefix = f"编码: {sku_code} | " if sku_code else ""
                    lines.append("- " + prefix + format_item_with_labels(
                        wh, _WH_STOCK_LABELS, transforms=_WH_STOCK_TRANSFORMS))
        else:
            # 扁平结构（兼容）
            name = item.get("title") or item.get("itemTitle") or ""
            code = item.get("outerId") or ""
            wh = item.get("warehouseName") or item.get("name") or ""
            qty = item.get("sellableNum") or item.get("quantity") or 0
            lines.append(
                f"- {name} | 编码: {code} | 仓库: {wh} | 可售: {qty}")
    return "\n".join(lines)


def format_stock_in_out(data: Any, entry: ApiEntry) -> str:
    """商品出入库记录"""
    rk = entry.response_key or "stockInOutRecordVos"
    items = data.get(rk) or []
    total = data.get("total", len(items))
    if not items:
        return "未找到出入库记录"
    lines = [f"共 {total} 条出入库记录：\n"]
    for r in items[:20]:
        lines.append("- " + format_item_with_labels(
            r, _STOCK_IO_LABELS, transforms=_STOCK_IO_TRANSFORMS))
    if int(total) > len(items):
        lines.append(f"\n（显示前{len(items)}条，共{total}条）")
    return "\n".join(lines)


PRODUCT_FORMATTERS: Dict[str, Callable] = {
    "format_product_list": format_product_list,
    "format_product_detail": format_product_detail,
    "format_inventory_list": format_inventory_list,
    "format_sku_info": format_sku_info,
    "format_warehouse_stock": format_warehouse_stock,
    "format_stock_in_out": format_stock_in_out,
}
