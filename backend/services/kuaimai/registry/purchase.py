"""
采购 API注册表

包含：供应商、采购单、收货单、采退单、上架单、采购建议 相关API
"""

from services.kuaimai.registry.base import ApiEntry

PURCHASE_REGISTRY = {
    # ── 供应商 查询 ───────────────────────────────────
    "supplier_list": ApiEntry(
        method="supplier.list.query",
        description="查询供应商列表",
        param_map={
            "name": "supplierName",
            "code": "supplierCode",
            "status": "status",
        },
        formatter="format_supplier_list",
    ),
    # ── 采购单 查询 ───────────────────────────────────
    "purchase_order_list": ApiEntry(
        method="purchase.order.query",
        description="采购单查询",
        param_map={
            "purchase_no": "purchaseNo",
            "supplier_name": "supplierName",
            "status": "status",
            "start_date": "startTime",
            "end_date": "endTime",
        },
        formatter="format_purchase_order_list",
    ),
    "purchase_order_detail": ApiEntry(
        method="purchase.order.get",
        description="采购单详情",
        param_map={
            "purchase_no": "purchaseNo",
            "purchase_id": "purchaseId",
        },
        formatter="format_purchase_order_detail",
        response_key=None,
    ),
    # ── 采退单 查询 ───────────────────────────────────
    "purchase_return_list": ApiEntry(
        method="purchase.return.list.query",
        description="采退单查询列表",
        param_map={
            "status": "status",
            "start_date": "startTime",
            "end_date": "endTime",
        },
        formatter="format_purchase_return_list",
    ),
    "purchase_return_detail": ApiEntry(
        method="purchase.return.list.get",
        description="采退单详情",
        formatter="format_generic_detail",
        response_key=None,
    ),
    # ── 收货单 查询 ───────────────────────────────────
    "warehouse_entry_list": ApiEntry(
        method="warehouse.entry.list.query",
        description="收货单查询列表",
        param_map={
            "status": "status",
            "start_date": "startTime",
            "end_date": "endTime",
        },
        formatter="format_warehouse_entry_list",
    ),
    "warehouse_entry_detail": ApiEntry(
        method="warehouse.entry.list.get",
        description="收货单详情",
        formatter="format_generic_detail",
        response_key=None,
    ),
    # ── 上架单 查询 ───────────────────────────────────
    "shelf_list": ApiEntry(
        method="erp.purchase.shelf.query",
        description="查询上架单",
        param_map={
            "status": "status",
            "start_date": "startTime",
            "end_date": "endTime",
        },
        formatter="format_shelf_list",
    ),
    "shelf_detail": ApiEntry(
        method="erp.purchase.shelf.get",
        description="查询上架单详情",
        formatter="format_generic_detail",
        response_key=None,
    ),
    # ── 采购建议 ──────────────────────────────────────
    "purchase_strategy": ApiEntry(
        method="sale.purchase.strategy.query",
        description="查询已售采购建议",
        formatter="format_purchase_strategy",
    ),
    "purchase_strategy_calculate": ApiEntry(
        method="sale.purchase.strategy.calculate",
        description="计算已售采购建议",
        formatter="format_generic_detail",
        response_key=None,
    ),
    "purchase_progress": ApiEntry(
        method="purchase.progress.query",
        description="进度获取",
        formatter="format_generic_detail",
        response_key=None,
    ),
    # ── 收货 操作 ─────────────────────────────────────
    "warehouse_entry_receive": ApiEntry(
        method="warehouse.entry.receive",
        description="收货单收货",
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
    ),
    "warehouse_entry_fast_receive": ApiEntry(
        method="warehouse.entry.fast.receive",
        description="采购快速收货",
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
    ),
    "warehouse_entry_revert": ApiEntry(
        method="warehouse.entry.finished.revert",
        description="收货单打回",
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
    ),
    "shelf_save": ApiEntry(
        method="erp.purchase.shelf.save",
        description="上架单上架",
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
    ),
    # ── 归档 查询 ─────────────────────────────────────
    "purchase_order_history": ApiEntry(
        method="purchase.order.history.query",
        description="归档采购单查询",
        param_map={
            "purchase_no": "purchaseNo",
            "start_date": "startTime",
            "end_date": "endTime",
        },
        formatter="format_purchase_order_list",
    ),
    "purchase_order_history_detail": ApiEntry(
        method="purchase.order.history.get",
        description="归档采购单详情",
        formatter="format_purchase_order_detail",
        response_key=None,
    ),
    "warehouse_entry_history": ApiEntry(
        method="warehouse.entry.history.list.query",
        description="归档收货单查询列表",
        formatter="format_warehouse_entry_list",
    ),
    "warehouse_entry_history_detail": ApiEntry(
        method="warehouse.entry.history.list.get",
        description="归档收货单详情",
        formatter="format_generic_detail",
        response_key=None,
    ),
    "purchase_return_history": ApiEntry(
        method="purchase.return.history.list.query",
        description="归档采退单查询列表",
        formatter="format_purchase_return_list",
    ),
    "purchase_return_history_detail": ApiEntry(
        method="purchase.return.history.list.get",
        description="归档采退单详情",
        formatter="format_generic_detail",
        response_key=None,
    ),
    "shelf_history": ApiEntry(
        method="erp.purchase.shelf.history.query",
        description="归档上架单查询",
        formatter="format_shelf_list",
    ),
    "shelf_history_detail": ApiEntry(
        method="erp.purchase.shelf.history.get",
        description="归档上架单详情查询",
        formatter="format_generic_detail",
        response_key=None,
    ),
    # ── 写入操作 ──────────────────────────────────────
    "supplier_add_update": ApiEntry(
        method="supplier.addorupdate",
        description="新建/修改供应商",
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
        confirm_template="确认新建/修改供应商？",
    ),
    "purchase_add": ApiEntry(
        method="purchase.add",
        description="新增采购单",
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
        confirm_template="确认新增采购单？",
    ),
    "purchase_add_update": ApiEntry(
        method="purchase.addorupdate",
        description="新建/修改采购单",
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
        confirm_template="确认新建/修改采购单？",
    ),
    "purchase_status_update": ApiEntry(
        method="purchase.status.update",
        description="采购单状态更新",
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
    ),
    "purchase_update_special": ApiEntry(
        method="purchase.update.ignore.status",
        description="更新采购单特殊字段",
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
    ),
    "purchase_un_audit": ApiEntry(
        method="purchase.unAudit",
        description="采购单反审核",
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
        confirm_template="确认反审核采购单？",
    ),
    "purchase_return_save": ApiEntry(
        method="purchase.return.save",
        description="采退单保存",
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
    ),
    "purchase_return_out": ApiEntry(
        method="purchase.return.out",
        description="采退单出库",
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
    ),
    "purchase_return_cancel": ApiEntry(
        method="purchase.return.cancel",
        description="采退单作废",
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
        confirm_template="确认作废采退单？此操作不可逆！",
    ),
    "warehouse_entry_add_update": ApiEntry(
        method="warehouse.entry.addorupdate",
        description="收货单新增/修改",
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
    ),
    "warehouse_entry_cancel": ApiEntry(
        method="warehouse.entry.cancel",
        description="收货单作废",
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
        confirm_template="确认作废收货单？此操作不可逆！",
    ),
    "pre_in_order_add": ApiEntry(
        method="purchase.pre.in.order.add",
        description="预约入库单新增",
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
    ),
    "pre_in_order_update": ApiEntry(
        method="purchase.pre.in.order.update",
        description="预约入库单修改",
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
    ),
    "pre_in_order_anti_audit": ApiEntry(
        method="purchase.pre.in.order.anti.audit",
        description="预约入库单反审核",
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
    ),
}
