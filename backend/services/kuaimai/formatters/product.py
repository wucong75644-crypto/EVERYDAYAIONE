"""
商品/库存 格式化器

从 service.py 迁移: _format_product, _format_product_detail, _format_inventory
新增: SKU列表、分类、品牌等格式化
"""

from typing import Any, Callable, Dict

from services.kuaimai.formatters.common import format_timestamp
from services.kuaimai.registry.base import ApiEntry

# 库存状态映射
_STOCK_STATUS_LABELS = {
    0: "正常",
    1: "警戒",
    2: "无货",
    3: "超卖",
}


def format_product_list(data: Any, entry: ApiEntry) -> str:
    """商品列表"""
    items = data.get("items") or data.get("list") or []
    total = data.get("total", len(items))
    if not items:
        return "未找到符合条件的商品"
    lines = [f"共找到 {total} 个商品：\n"]
    for item in items[:20]:
        lines.append(_format_product(item))
    if int(total) > len(items):
        lines.append(f"\n（显示前{len(items)}个，共{total}个）")
    return "\n".join(lines)


def format_product_detail(data: Any, entry: ApiEntry) -> str:
    """商品详情"""
    return _format_product_detail(data)


def format_inventory_list(data: Any, entry: ApiEntry) -> str:
    """库存状态列表"""
    items = data.get("stockStatusVoList") or data.get("list") or []
    total = data.get("total", len(items))
    if not items:
        return "未找到符合条件的库存记录"
    lines = [f"共找到 {total} 条库存记录：\n"]
    for item in items[:100]:
        lines.append(_format_inventory(item))
    if int(total) > len(items):
        lines.append(f"\n（显示前{len(items)}条，共{total}条）")
    return "\n".join(lines)


def format_sku_info(data: Any, entry: ApiEntry) -> str:
    """SKU信息"""
    items = data.get("items") or data.get("list") or []
    if isinstance(data, dict) and not items:
        # 可能是单个SKU详情
        return _format_sku_detail(data)
    if not items:
        return "未找到SKU信息"
    lines = [f"共 {len(items)} 个SKU：\n"]
    for sku in items[:30]:
        lines.append(_format_sku_line(sku))
    return "\n".join(lines)


def format_warehouse_stock(data: Any, entry: ApiEntry) -> str:
    """仓库及商品库存"""
    items = data.get("list") or []
    if not items:
        return "未找到仓库库存信息"
    lines = [f"共 {len(items)} 条仓库库存：\n"]
    for item in items[:50]:
        name = item.get("title") or item.get("itemTitle") or ""
        code = item.get("outerId") or ""
        wh = item.get("warehouseName") or item.get("wareHouseId") or ""
        qty = item.get("sellableNum") or item.get("quantity") or 0
        lines.append(f"- {name} | 编码: {code} | 仓库: {wh} | 可售: {qty}")
    return "\n".join(lines)


def format_stock_in_out(data: Any, entry: ApiEntry) -> str:
    """商品出入库记录"""
    items = data.get("list") or []
    total = data.get("total", len(items))
    if not items:
        return "未找到出入库记录"
    lines = [f"共 {total} 条出入库记录：\n"]
    for r in items[:20]:
        time = format_timestamp(r.get("created") or r.get("modified"))
        biz_type = r.get("bizType") or ""
        qty = r.get("changeNum") or r.get("quantity") or 0
        code = r.get("outerId") or ""
        lines.append(f"- [{time}] {biz_type} | 编码: {code} | 变动: {qty}")
    return "\n".join(lines)


def _format_product(item: Dict[str, Any]) -> str:
    """格式化商品列表项"""
    title = item.get("title", "")
    outer_id = item.get("outerId", "")
    barcode = item.get("barcode", "")
    active = item.get("activeStatus", 1)
    is_sku = item.get("isSkuItem", 0)
    weight = item.get("weight", 0)

    parts = [f"- {title}"]
    if outer_id:
        parts.append(f"编码: {outer_id}")
    if barcode:
        parts.append(f"条码: {barcode}")
    if weight:
        parts.append(f"重量: {weight}g")
    parts.append(f"多规格: {'是' if is_sku else '否'}")
    parts.append(f"状态: {'启用' if active == 1 else '停用'}")
    return " | ".join(parts)


def _format_product_detail(item: Dict[str, Any]) -> str:
    """格式化商品详情"""
    title = item.get("title", "")
    outer_id = item.get("outerId", "")
    barcode = item.get("barcode", "")
    weight = item.get("weight", "")
    unit = item.get("unit", "")
    active = item.get("activeStatus", 1)
    is_sku = item.get("isSkuItem", 0)

    lines = [f"商品详情：{title}"]
    if outer_id:
        lines.append(f"  商家编码: {outer_id}")
    if barcode:
        lines.append(f"  条形码: {barcode}")
    if weight:
        lines.append(f"  重量: {weight}g")
    if unit:
        lines.append(f"  单位: {unit}")
    lines.append(f"  状态: {'启用' if active == 1 else '停用'}")
    lines.append(f"  多规格: {'是' if is_sku else '否'}")

    cats = item.get("sellerCats") or []
    if cats:
        cat_names = [c.get("name", "") for c in cats if c.get("name")]
        if cat_names:
            lines.append(f"  分类: {' > '.join(cat_names)}")

    skus = item.get("items") or []
    if skus:
        lines.append(f"\n  SKU列表（共{len(skus)}个）：")
        for sku in skus[:10]:
            lines.append(_format_sku_line(sku, indent="    "))
    return "\n".join(lines)


def _format_inventory(item: Dict[str, Any]) -> str:
    """格式化库存行"""
    name = item.get("title", item.get("shortTitle", ""))
    outer_id = item.get("mainOuterId", "")
    sku_id = item.get("outerId", "")
    props = item.get("propertiesName", "")
    total_qty = item.get("totalAvailableStockSum", 0)
    available = item.get("sellableNum", 0)
    locked = item.get("totalLockStock", 0)
    warehouse_id = item.get("wareHouseId", "")
    status_code = item.get("stockStatus", 0)
    status_label = _STOCK_STATUS_LABELS.get(status_code, str(status_code))
    purchase_price = item.get("purchasePrice", "")

    parts = [f"- {name}"]
    if outer_id:
        parts.append(f"编码: {outer_id}")
    if sku_id and sku_id != outer_id:
        parts.append(f"SKU: {sku_id}")
    if props:
        parts.append(f"规格: {props}")
    parts.append(f"总库存: {total_qty}")
    parts.append(f"可售: {available}")
    if locked:
        parts.append(f"锁定: {locked}")
    if warehouse_id:
        parts.append(f"仓库ID: {warehouse_id}")
    if purchase_price:
        parts.append(f"采购价: ¥{purchase_price}")
    parts.append(f"状态: {status_label}")
    return " | ".join(parts)


def _format_sku_line(sku: Dict[str, Any], indent: str = "  ") -> str:
    """格式化单个SKU行"""
    code = sku.get("skuOuterId") or sku.get("outerId") or ""
    props = sku.get("propertiesName") or ""
    barcode = sku.get("barcode") or ""
    active = sku.get("activeStatus", 1)
    parts = [f"{indent}- {code}"]
    if props:
        parts.append(props)
    if barcode:
        parts.append(f"条码: {barcode}")
    parts.append("启用" if active == 1 else "停用")
    return " | ".join(parts)


def _format_sku_detail(data: Dict[str, Any]) -> str:
    """格式化单个SKU详情"""
    code = data.get("skuOuterId") or data.get("outerId") or ""
    title = data.get("title") or ""
    props = data.get("propertiesName") or ""
    barcode = data.get("barcode") or ""
    lines = [f"SKU详情: {title} ({code})"]
    if props:
        lines.append(f"  规格: {props}")
    if barcode:
        lines.append(f"  条码: {barcode}")
    return "\n".join(lines)


PRODUCT_FORMATTERS: Dict[str, Callable] = {
    "format_product_list": format_product_list,
    "format_product_detail": format_product_detail,
    "format_inventory_list": format_inventory_list,
    "format_sku_info": format_sku_info,
    "format_warehouse_stock": format_warehouse_stock,
    "format_stock_in_out": format_stock_in_out,
}
