"""
ERP 多表统一查询 — 新表列白名单 + 字段标签。

从 erp_unified_schema.py 拆出，保持主 schema 文件 < 500 行。
设计文档: docs/document/TECH_ERP多表统一查询.md §5.1
"""
from __future__ import annotations

from services.kuaimai.erp_unified_schema import ColumnMeta


# ── 8 张新表的列白名单 ──────────────────────────────────

STOCK_COLUMNS: dict[str, ColumnMeta] = {
    "outer_id": ColumnMeta("text"), "sku_outer_id": ColumnMeta("text"),
    "item_name": ColumnMeta("text"), "properties_name": ColumnMeta("text"),
    "total_stock": ColumnMeta("numeric"), "sellable_num": ColumnMeta("numeric"),
    "available_stock": ColumnMeta("numeric"), "lock_stock": ColumnMeta("numeric"),
    "purchase_num": ColumnMeta("numeric"), "on_the_way_num": ColumnMeta("numeric"),
    "defective_stock": ColumnMeta("numeric"), "virtual_stock": ColumnMeta("numeric"),
    "stock_status": ColumnMeta("integer"),
    "purchase_price": ColumnMeta("numeric"), "selling_price": ColumnMeta("numeric"),
    "market_price": ColumnMeta("numeric"), "allocate_num": ColumnMeta("numeric"),
    "refund_stock": ColumnMeta("numeric"), "purchase_stock": ColumnMeta("numeric"),
    "supplier_codes": ColumnMeta("text"), "supplier_names": ColumnMeta("text"),
    "warehouse_id": ColumnMeta("text"),
    "stock_modified_time": ColumnMeta("timestamp"),
    "synced_at": ColumnMeta("timestamp"),
    "cid_name": ColumnMeta("text"),
}

PRODUCT_COLUMNS: dict[str, ColumnMeta] = {
    "outer_id": ColumnMeta("text"), "title": ColumnMeta("text"),
    "item_type": ColumnMeta("integer"), "is_virtual": ColumnMeta("boolean"),
    "active_status": ColumnMeta("integer"), "barcode": ColumnMeta("text"),
    "purchase_price": ColumnMeta("numeric"), "selling_price": ColumnMeta("numeric"),
    "market_price": ColumnMeta("numeric"), "weight": ColumnMeta("numeric"),
    "unit": ColumnMeta("text"), "is_gift": ColumnMeta("boolean"),
    "sys_item_id": ColumnMeta("text"), "brand": ColumnMeta("text"),
    "shipper": ColumnMeta("text"), "remark": ColumnMeta("text"),
    "created_at": ColumnMeta("timestamp"), "modified_at": ColumnMeta("timestamp"),
    "pic_url": ColumnMeta("text"),
    "length": ColumnMeta("numeric"), "width": ColumnMeta("numeric"),
    "height": ColumnMeta("numeric"),
    "classify_name": ColumnMeta("text"), "seller_cat_name": ColumnMeta("text"),
    "is_sku_item": ColumnMeta("boolean"),
    "synced_at": ColumnMeta("timestamp"),
}

SKU_COLUMNS: dict[str, ColumnMeta] = {
    "outer_id": ColumnMeta("text"), "sku_outer_id": ColumnMeta("text"),
    "properties_name": ColumnMeta("text"), "barcode": ColumnMeta("text"),
    "purchase_price": ColumnMeta("numeric"), "selling_price": ColumnMeta("numeric"),
    "market_price": ColumnMeta("numeric"), "weight": ColumnMeta("numeric"),
    "unit": ColumnMeta("text"), "shipper": ColumnMeta("text"),
    "pic_url": ColumnMeta("text"), "sys_sku_id": ColumnMeta("text"),
    "active_status": ColumnMeta("integer"),
    "length": ColumnMeta("numeric"), "width": ColumnMeta("numeric"),
    "height": ColumnMeta("numeric"),
    "sku_remark": ColumnMeta("text"),
    "platform_map_checked_at": ColumnMeta("timestamp"),
    "synced_at": ColumnMeta("timestamp"),
}

DAILY_STATS_COLUMNS: dict[str, ColumnMeta] = {
    "stat_date": ColumnMeta("timestamp"),
    "outer_id": ColumnMeta("text"), "sku_outer_id": ColumnMeta("text"),
    "item_name": ColumnMeta("text"),
    "purchase_count": ColumnMeta("integer"), "purchase_qty": ColumnMeta("numeric"),
    "purchase_received_qty": ColumnMeta("numeric"), "purchase_amount": ColumnMeta("numeric"),
    "receipt_count": ColumnMeta("integer"), "receipt_qty": ColumnMeta("numeric"),
    "shelf_count": ColumnMeta("integer"), "shelf_qty": ColumnMeta("numeric"),
    "purchase_return_count": ColumnMeta("integer"), "purchase_return_qty": ColumnMeta("numeric"),
    "purchase_return_amount": ColumnMeta("numeric"),
    "aftersale_count": ColumnMeta("integer"), "aftersale_refund_count": ColumnMeta("integer"),
    "aftersale_return_count": ColumnMeta("integer"), "aftersale_exchange_count": ColumnMeta("integer"),
    "aftersale_reissue_count": ColumnMeta("integer"), "aftersale_reject_count": ColumnMeta("integer"),
    "aftersale_repair_count": ColumnMeta("integer"), "aftersale_other_count": ColumnMeta("integer"),
    "aftersale_qty": ColumnMeta("numeric"), "aftersale_amount": ColumnMeta("numeric"),
    "order_count": ColumnMeta("integer"), "order_qty": ColumnMeta("numeric"),
    "order_amount": ColumnMeta("numeric"), "order_shipped_count": ColumnMeta("integer"),
    "order_finished_count": ColumnMeta("integer"), "order_refund_count": ColumnMeta("integer"),
    "order_cancelled_count": ColumnMeta("integer"), "order_cost": ColumnMeta("numeric"),
    "updated_at": ColumnMeta("timestamp"),
}

PLATFORM_MAP_COLUMNS: dict[str, ColumnMeta] = {
    "outer_id": ColumnMeta("text"), "num_iid": ColumnMeta("text"),
    "user_id": ColumnMeta("text"), "title": ColumnMeta("text"),
    "synced_at": ColumnMeta("timestamp"),
}

BATCH_STOCK_COLUMNS: dict[str, ColumnMeta] = {
    "outer_id": ColumnMeta("text"), "sku_outer_id": ColumnMeta("text"),
    "item_name": ColumnMeta("text"), "batch_no": ColumnMeta("text"),
    "production_date": ColumnMeta("text"), "expiry_date": ColumnMeta("text"),
    "shelf_life_days": ColumnMeta("integer"), "stock_qty": ColumnMeta("integer"),
    "warehouse_name": ColumnMeta("text"), "shop_id": ColumnMeta("text"),
    "synced_at": ColumnMeta("timestamp"),
}

ORDER_LOG_COLUMNS: dict[str, ColumnMeta] = {
    "system_id": ColumnMeta("text"), "operator": ColumnMeta("text"),
    "action": ColumnMeta("text"), "content": ColumnMeta("text"),
    "operate_time": ColumnMeta("timestamp"),
    "synced_at": ColumnMeta("timestamp"),
}

AFTERSALE_LOG_COLUMNS: dict[str, ColumnMeta] = {
    "work_order_id": ColumnMeta("text"), "operator": ColumnMeta("text"),
    "action": ColumnMeta("text"), "content": ColumnMeta("text"),
    "operate_time": ColumnMeta("timestamp"),
    "synced_at": ColumnMeta("timestamp"),
}

# doc_type → 对应列白名单
TABLE_COLUMNS: dict[str, dict[str, ColumnMeta]] = {
    "stock": STOCK_COLUMNS,
    "product": PRODUCT_COLUMNS,
    "sku": SKU_COLUMNS,
    "daily_stats": DAILY_STATS_COLUMNS,
    "platform_map": PLATFORM_MAP_COLUMNS,
    "batch_stock": BATCH_STOCK_COLUMNS,
    "order_log": ORDER_LOG_COLUMNS,
    "aftersale_log": AFTERSALE_LOG_COLUMNS,
}


# ── 新表字段 → 中文标签 ──────────────────────────────────

FIELD_LABEL_CN: dict[str, str] = {
    # stock
    "total_stock": "总库存", "sellable_num": "可售数量",
    "available_stock": "可用库存", "lock_stock": "锁定库存",
    "purchase_num": "采购在途", "on_the_way_num": "在途数量",
    "defective_stock": "残次品库存", "virtual_stock": "虚拟库存",
    "stock_status": "库存状态", "purchase_price": "采购价",
    "selling_price": "销售价", "market_price": "市场价",
    "allocate_num": "调拨数量", "refund_stock": "退货库存",
    "purchase_stock": "采购库存", "supplier_codes": "供应商编码",
    "supplier_names": "供应商名称", "warehouse_id": "仓库ID",
    "stock_modified_time": "库存更新时间", "cid_name": "类目名称",
    # product
    "title": "商品名称", "item_type": "商品类型",
    "is_virtual": "是否虚拟", "active_status": "状态",
    "barcode": "条码", "unit": "单位",
    "is_gift": "是否赠品", "sys_item_id": "系统商品ID",
    "brand": "品牌", "shipper": "发货人",
    "created_at": "创建时间", "modified_at": "修改时间",
    "pic_url": "图片URL", "length": "长(cm)",
    "width": "宽(cm)", "height": "高(cm)",
    "classify_name": "分类名称", "seller_cat_name": "卖家自定义分类",
    "is_sku_item": "是否有SKU",
    # sku
    "properties_name": "规格属性", "sys_sku_id": "系统SKU-ID",
    "sku_remark": "SKU备注", "platform_map_checked_at": "平台映射校验时间",
    # daily_stats
    "stat_date": "统计日期",
    "purchase_count": "采购单数", "purchase_qty": "采购数量",
    "purchase_received_qty": "采购到货数量", "purchase_amount": "采购金额",
    "receipt_count": "收货单数", "receipt_qty": "收货数量",
    "shelf_count": "上架单数", "shelf_qty": "上架数量",
    "purchase_return_count": "采退单数", "purchase_return_qty": "采退数量",
    "purchase_return_amount": "采退金额",
    "aftersale_count": "售后总数", "aftersale_refund_count": "仅退款数",
    "aftersale_return_count": "退货退款数", "aftersale_exchange_count": "换货数",
    "aftersale_reissue_count": "补发数", "aftersale_reject_count": "拒收数",
    "aftersale_repair_count": "维修数", "aftersale_other_count": "其他售后数",
    "aftersale_qty": "售后数量", "aftersale_amount": "售后金额",
    "order_count": "订单数", "order_qty": "订单数量",
    "order_amount": "订单金额", "order_shipped_count": "已发货数",
    "order_finished_count": "已完成数", "order_refund_count": "退款订单数",
    "order_cancelled_count": "取消订单数", "order_cost": "订单成本",
    "updated_at": "更新时间",
    # platform_map
    "num_iid": "平台商品ID", "user_id": "店铺用户ID",
    # batch_stock
    "batch_no": "批次号", "production_date": "生产日期",
    "expiry_date": "过期日期", "shelf_life_days": "保质期(天)",
    "stock_qty": "批次库存数量", "warehouse_name": "仓库名称",
    "shop_id": "店铺ID",
    # logs
    "system_id": "订单系统ID", "work_order_id": "工单号",
    "operator": "操作人", "action": "操作类型",
    "content": "操作内容", "operate_time": "操作时间",
    # common
    "synced_at": "同步时间",
}
