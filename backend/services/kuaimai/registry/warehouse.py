"""
仓储 API注册表

包含：调拨、入出库、盘点、下架、货位、加工单、批次库存 相关API
"""

from services.kuaimai.registry.base import ApiEntry

WAREHOUSE_REGISTRY = {
    # ── 调拨 查询 ─────────────────────────────────────
    "allocate_list": ApiEntry(
        method="erp.allocate.task.query",
        description="查询调拨单列表",
        param_map={
            "status": "status",
            "code": "code",
            "start_date": "startModified",
            "end_date": "endModified",
            "label_name": "labelName",
        },
        formatter="format_allocate_list",
    ),
    "allocate_detail": ApiEntry(
        method="erp.allocate.task.detail.query",
        description="查询调拨单明细",
        param_map={
            "code": "code",
            "start_date": "startModified",
            "end_date": "endModified",
            "label_name": "labelName",
        },
        formatter="format_allocate_detail",
        response_key=None,
    ),
    "allocate_in_list": ApiEntry(
        method="allocate.in.task.query",
        description="查询调拨入库单列表",
        param_map={
            "status": "status",
            "code": "code",
            "custom_type": "customType",
            "start_date": "startModified",
            "end_date": "endModified",
        },
        formatter="format_other_in_out_list",
    ),
    "allocate_in_detail": ApiEntry(
        method="allocate.in.task.get",
        description="查询调拨入库单明细",
        formatter="format_generic_detail",
        response_key=None,
    ),
    "allocate_out_list": ApiEntry(
        method="allocate.out.task.query",
        description="查询调拨出库单列表",
        param_map={
            "status": "status",
            "code": "code",
            "time_type": "timeType",
            "start_date": "startModified",
            "end_date": "endModified",
        },
        formatter="format_other_in_out_list",
    ),
    "allocate_out_detail": ApiEntry(
        method="allocate.out.task.get",
        description="查询调拨出库单明细",
        formatter="format_generic_detail",
        response_key=None,
    ),
    # ── 其他入出库 查询 ────────────────────────────────
    "other_in_list": ApiEntry(
        method="other.in.order.query",
        description="查询其他入库单",
        param_map={
            "status": "status",
            "code": "code",
            "outer_code": "outerCode",
            "custom_type": "customTypeStr",
            "start_date": "startModified",
            "end_date": "endModified",
        },
        formatter="format_other_in_out_list",
    ),
    "other_in_detail": ApiEntry(
        method="other.in.order.get",
        description="查询其他入库单明细",
        formatter="format_generic_detail",
        response_key=None,
    ),
    "other_out_list": ApiEntry(
        method="other.out.order.query",
        description="查询其他出库单",
        param_map={
            "status": "status",
            "code": "code",
            "outer_code": "outerCode",
            "custom_type": "customTypeStr",
            "start_date": "startModified",
            "end_date": "endModified",
        },
        formatter="format_other_in_out_list",
    ),
    "other_out_detail": ApiEntry(
        method="other.out.order.get",
        description="查询其他出库单明细",
        formatter="format_generic_detail",
        response_key=None,
    ),
    # ── 盘点 查询 ─────────────────────────────────────
    "inventory_sheet_list": ApiEntry(
        method="inventory.sheet.query",
        description="查询盘点单列表",
        param_map={
            "status": "status",
            "code": "code",
            "start_date": "startModified",
            "end_date": "endModified",
        },
        formatter="format_inventory_sheet_list",
    ),
    "inventory_sheet_detail": ApiEntry(
        method="inventory.sheet.get",
        description="查询盘点单明细",
        formatter="format_inventory_sheet_detail",
        response_key=None,
    ),
    # ── 下架 查询 ─────────────────────────────────────
    "unshelve_list": ApiEntry(
        method="erp.wms.unshelve.order.query",
        description="查询下架单列表",
        param_map={
            "code": "code",
            "start_date": "startModified",
            "end_date": "endModified",
        },
        formatter="format_unshelve_list",
    ),
    "unshelve_detail": ApiEntry(
        method="erp.wms.unshelve.order.get",
        description="查询下架单明细",
        formatter="format_generic_detail",
        response_key=None,
    ),
    # ── 货位/库存 查询 ────────────────────────────────
    "goods_section_list": ApiEntry(
        method="asso.goods.section.sku.query",
        description="货位库存查询列表",
        param_map={
            "start_date": "startModified",
            "end_date": "endModified",
        },
        formatter="format_goods_section_list",
    ),
    "batch_stock_list": ApiEntry(
        method="erp.wms.product.stock.query",
        description="商品批次效期库存查询",
        param_map={
            "sku_ids": "skuIds",
            "num_iids": "numIids",
            "tids": "tids",
            "shop_id": "userId",
        },
        required_params=["shop_id"],
        formatter="format_batch_stock_list",
    ),
    # ── 加工单 查询 ───────────────────────────────────
    "process_order_list": ApiEntry(
        method="erp.stock.product.order.query",
        description="查询加工单列表",
        param_map={
            "status": "status",
            "code": "code",
            "type": "type",
            "start_date": "modifiedStart",
            "end_date": "modifiedEnd",
            "product_start": "productTimeStart",
            "product_end": "productTimeEnd",
            "finished_start": "finishedTimeStart",
            "finished_end": "finishedTimeEnd",
            "created_start": "createdStart",
            "created_end": "createdEnd",
        },
        formatter="format_process_order_list",
    ),
    "process_order_detail": ApiEntry(
        method="erp.stock.product.order.get",
        description="查询加工单明细",
        formatter="format_generic_detail",
        response_key=None,
    ),
    # ── 货位进出记录 ──────────────────────────────────
    "section_record_list": ApiEntry(
        method="goods.section.in.out.record.query",
        description="货位进出记录查询",
        param_map={
            "order_number": "orderNumber",
            "start_date": "operateStartTime",
            "end_date": "operateEndTime",
        },
        formatter="format_section_record_list",
    ),
    # ── 写入操作 ──────────────────────────────────────
    "allocate_add": ApiEntry(
        method="allocate.task.add",
        description="新增完成的调拨单",
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
        confirm_template="确认新增调拨单？",
    ),
    "allocate_create": ApiEntry(
        method="allocate.task.status.create",
        description="新增调拨单",
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
        confirm_template="确认新增调拨单？",
    ),
    "allocate_in_receive": ApiEntry(
        method="allocate.in.task.receive",
        description="调拨入库单收货",
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
    ),
    "allocate_out_direct": ApiEntry(
        method="erp.allocate.out.task.out",
        description="调拨出库单直接出库",
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
    ),
    "other_in_add": ApiEntry(
        method="other.in.order.add",
        description="新增其他入库单",
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
        confirm_template="确认新增其他入库单？",
    ),
    "other_in_cancel": ApiEntry(
        method="erp.other.in.order.cancel",
        description="作废其他入库单",
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
        confirm_template="确认作废其他入库单？此操作不可逆！",
    ),
    "other_out_add": ApiEntry(
        method="other.out.order.add",
        description="新增其他出库单",
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
        confirm_template="确认新增其他出库单？",
    ),
    "other_out_cancel": ApiEntry(
        method="erp.other.out.order.cancel",
        description="作废其他出库单",
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
        confirm_template="确认作废其他出库单？此操作不可逆！",
    ),
    "inventory_batch_update": ApiEntry(
        method="inventory.sheet.batch.update",
        description="盘点单库存盘点",
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
    ),
    "unshelve_save": ApiEntry(
        method="erp.wms.unshelve.order.save",
        description="新建/修改下架单",
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
    ),
    "unshelve_execute": ApiEntry(
        method="erp.wms.unshelve.order.unshelve",
        description="下架单下架",
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
    ),
    "goods_section_delete": ApiEntry(
        method="asso.goods.section.sku.del.query",
        description="货位库存删除数据列表",
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
    ),
    "upshelf_batch": ApiEntry(
        method="erp.wms.upshelf.batch",
        description="暂存区批量上架",
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
    ),
}
