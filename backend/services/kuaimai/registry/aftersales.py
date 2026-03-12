"""
售后 API注册表

包含：售后工单、退货入仓、补款、维修单、售后日志 相关API
"""

from services.kuaimai.registry.base import ApiEntry

AFTERSALES_REGISTRY = {
    # ── 查询 ──────────────────────────────────────────
    "aftersale_list": ApiEntry(
        method="erp.aftersale.list.query",
        description="售后工单查询",
        param_map={
            "order_id": "tid",
            "system_id": "sid",
            "status": "status",
            "type": "afterSaleType",
            "start_date": "startTime",
            "end_date": "endTime",
            "buyer": "buyerNick",
        },
        formatter="format_aftersale_list",
    ),
    "refund_warehouse": ApiEntry(
        method="erp.aftersale.refund.warehouse.query",
        description="销退入库单查询",
        param_map={
            "order_id": "tid",
            "start_date": "startTime",
            "end_date": "endTime",
        },
        formatter="format_refund_warehouse",
    ),
    "replenish_list": ApiEntry(
        method="erp.aftersale.replenish.list.query",
        description="登记补款查询",
        param_map={
            "order_id": "tid",
            "start_date": "startTime",
            "end_date": "endTime",
        },
        formatter="format_replenish_list",
    ),
    "repair_list": ApiEntry(
        method="erp.aftersale.repair.list.query",
        description="维修单列表查询",
        param_map={
            "status": "status",
            "start_date": "startTime",
            "end_date": "endTime",
        },
        formatter="format_repair_list",
    ),
    "repair_detail": ApiEntry(
        method="erp.aftersale.repair.detail.query",
        description="维修单详情查询",
        param_map={
            "repair_id": "repairId",
            "repair_no": "repairNo",
        },
        formatter="format_repair_detail",
        response_key=None,
    ),
    "aftersale_log": ApiEntry(
        method="erp.aftersale.operate.log.query",
        description="售后日志查询",
        param_map={
            "work_order_no": "workOrderNo",
        },
        formatter="format_aftersale_log",
    ),
    # ── 写入操作 ──────────────────────────────────────
    "workorder_create": ApiEntry(
        method="erp.aftersale.workorder.create",
        description="创建售后工单",
        param_map={
            "order_id": "tid",
        },
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
        confirm_template="确认为订单「{order_id}」创建售后工单？",
    ),
    "workorder_cancel": ApiEntry(
        method="erp.aftersale.workorder.cancel",
        description="作废售后工单",
        param_map={
            "work_order_no": "workOrderNo",
        },
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
        confirm_template="确认作废售后工单「{work_order_no}」？此操作不可逆！",
    ),
    "workorder_resolve": ApiEntry(
        method="erp.aftersale.workorder.resolve",
        description="解决售后工单",
        param_map={
            "work_order_no": "workOrderNo",
        },
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
    ),
    "workorder_goods_received": ApiEntry(
        method="erp.aftersale.workorder.goods.received",
        description="售后工单退货入仓",
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
    ),
    "workorder_tag_update": ApiEntry(
        method="erp.aftersale.workorder.tag.update",
        description="更新售后工单标记",
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
    ),
    "workorder_explains_update": ApiEntry(
        method="erp.aftersale.workorder.explains.update",
        description="更新售后单售后说明",
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
    ),
    "workorder_batch_change_type": ApiEntry(
        method="erp.aftersale.workorder.batchChangeType",
        description="批量修改售后类型",
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
    ),
    "repair_process": ApiEntry(
        method="erp.aftersale.repair.order.process",
        description="维修单处理",
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
    ),
    "repair_edit_money": ApiEntry(
        method="erp.aftersale.repair.order.edit.repairMoney",
        description="维修单修改费用",
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
    ),
    "repair_pay": ApiEntry(
        method="erp.aftersale.repair.order.pay",
        description="维修单付款",
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
    ),
    "workorder_remark_update": ApiEntry(
        method="erp.aftersale.workorder.remark.update",
        description="修改售后工单备注",
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
    ),
    "update_platform_refund_money": ApiEntry(
        method="erp.aftersale.update.platformRefundMoney",
        description="更新工单平台实退金额",
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
    ),
    "update_express": ApiEntry(
        method="erp.aftersale.update.express",
        description="更新工单退货快递信息",
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
    ),
}
