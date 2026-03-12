"""
交易 API注册表

包含：订单查询/管理、出库、物流、波次、唯一码 相关API
"""

from services.kuaimai.registry.base import ApiEntry

TRADE_REGISTRY = {
    # ── 订单查询 ──────────────────────────────────────
    "order_list": ApiEntry(
        method="erp.trade.list.query",
        description="订单查询",
        param_map={
            "order_id": "tid",
            "system_id": "sid",
            "buyer": "buyerNick",
            "shop_name": "shopName",
            "status": "sysStatus",
            "time_type": "timeType",
            "start_date": "startTime",
            "end_date": "endTime",
            "outer_id": "outerId",
            "receiver_name": "receiverName",
            "receiver_phone": "receiverPhone",
        },
        formatter="format_order_list",
    ),
    "order_log": ApiEntry(
        method="erp.trade.trace.list",
        description="订单操作日志",
        param_map={
            "order_id": "tid",
            "system_id": "sid",
        },
        formatter="format_order_log",
    ),
    # ── 出库/物流 查询 ────────────────────────────────
    "outstock_query": ApiEntry(
        method="erp.trade.outstock.simple.query",
        description="销售出库查询",
        param_map={
            "order_id": "tid",
            "system_id": "sid",
            "status": "sysStatus",
            "start_date": "startTime",
            "end_date": "endTime",
            "express_no": "outSid",
            "shop_name": "shopName",
            "warehouse_name": "warehouseName",
        },
        formatter="format_shipment_list",
    ),
    "express_query": ApiEntry(
        method="erp.trade.multi.packs.query",
        description="多快递单号查询",
        param_map={
            "order_id": "tid",
            "system_id": "sid",
        },
        formatter="format_express_list",
    ),
    "outstock_order_query": ApiEntry(
        method="erp.wave.logistics.order.query",
        description="销售出库单查询",
        param_map={
            "order_id": "tid",
            "system_id": "sid",
            "start_date": "startTime",
            "end_date": "endTime",
        },
        formatter="format_outstock_order_list",
    ),
    # ── 波次 查询 ─────────────────────────────────────
    "wave_query": ApiEntry(
        method="erp.trade.waves.query",
        description="波次信息查询",
        param_map={
            "wave_id": "waveId",
            "wave_no": "waveNo",
            "start_date": "startTime",
            "end_date": "endTime",
        },
        formatter="format_generic_list",
    ),
    "wave_sorting_query": ApiEntry(
        method="erp.trade.wave.sorting.query",
        description="波次分拣信息查询",
        param_map={
            "wave_id": "waveId",
        },
        formatter="format_generic_list",
    ),
    # ── 唯一码 查询 ───────────────────────────────────
    "unique_code_query": ApiEntry(
        method="erp.item.unique.code.query",
        description="查询唯一码",
        param_map={
            "code": "uniqueCode",
            "outer_id": "outerId",
        },
        formatter="format_generic_list",
    ),
    # ── 物流 查询 ─────────────────────────────────────
    "logistics_company_list": ApiEntry(
        method="erp.trade.logistics.company.user.list",
        description="用户物流公司列表",
        formatter="format_logistics_company",
    ),
    "logistics_template_list": ApiEntry(
        method="erp.trade.logistics.template.user.list",
        description="用户物流模板列表",
        formatter="format_generic_list",
    ),
    "waybill_get": ApiEntry(
        method="erp.trade.waybill.code.get",
        description="获取物流单号",
        param_map={
            "order_id": "tid",
            "system_id": "sid",
        },
        formatter="format_generic_detail",
        response_key=None,
    ),
    "upload_memo_flag": ApiEntry(
        method="erp.trade.upload.memo.flag",
        description="上传备注与旗帜",
        param_map={
            "order_id": "tid",
        },
        formatter="format_generic_detail",
        response_key=None,
    ),
    # ── 写入操作 ──────────────────────────────────────
    "order_create": ApiEntry(
        method="erp.trade.create",
        description="创建系统手工单",
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
        confirm_template="确认创建系统手工单？",
    ),
    "order_create_new": ApiEntry(
        method="erp.trade.create.new",
        description="创建自建平台订单",
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
        confirm_template="确认创建自建平台订单？",
    ),
    "receiver_update": ApiEntry(
        method="erp.trade.receiver.info.update",
        description="修改订单收货地址",
        param_map={
            "order_id": "tid",
            "system_id": "sid",
        },
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
        confirm_template="确认修改订单「{order_id}」的收货地址？",
    ),
    "seller_memo_update": ApiEntry(
        method="erp.trade.seller.memo.upload",
        description="修改订单卖家备注与旗帜",
        param_map={
            "order_id": "tid",
            "system_id": "sid",
        },
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
    ),
    "tag_batch_update": ApiEntry(
        method="erp.trade.tag.batch.update",
        description="批量修改订单标签",
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
    ),
    "order_cancel": ApiEntry(
        method="erp.trade.cancel",
        description="订单作废",
        param_map={
            "order_id": "tid",
            "system_id": "sid",
        },
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
        confirm_template="确认作废订单「{order_id}」？此操作不可逆！",
    ),
    "order_intercept": ApiEntry(
        method="erp.trade.send.goods.intercept",
        description="订单发货拦截",
        param_map={
            "order_id": "tid",
            "system_id": "sid",
        },
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
        confirm_template="确认拦截订单「{order_id}」的发货？",
    ),
    "trade_consign": ApiEntry(
        method="erp.trade.consign",
        description="上传发货",
        param_map={
            "order_id": "tid",
            "system_id": "sid",
        },
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
        confirm_template="确认上传发货信息？",
    ),
    "trade_pack": ApiEntry(
        method="erp.trade.pack",
        description="包装验货",
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
    ),
    "wave_pick_hand": ApiEntry(
        method="erp.trade.wave.pick.hand",
        description="波次手动拣选",
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
    ),
    "unique_code_receive": ApiEntry(
        method="erp.trade.unique.code.receive",
        description="订单唯一码收货",
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
    ),
    "wave_seed": ApiEntry(
        method="erp.trade.wave.seed",
        description="波次播种回传",
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
    ),
    "unique_code_generate": ApiEntry(
        method="erp.item.unique.code.generate",
        description="新增商品唯一码",
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
    ),
    "unique_code_validate": ApiEntry(
        method="erp.wave.unique.code.validate",
        description="校验波次唯一码",
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
    ),
    "unique_code_update": ApiEntry(
        method="erp.item.unique.code.update",
        description="商品唯一码更新",
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
    ),
    "change_warehouse": ApiEntry(
        method="erp.trade.change.warehouse",
        description="修改订单仓库",
        param_map={
            "order_id": "tid",
            "system_id": "sid",
        },
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
        confirm_template="确认修改订单仓库？",
    ),
    "logistics_template_update": ApiEntry(
        method="erp.trade.logistics.template.update",
        description="更新订单物流模板",
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
    ),
    "fast_stock_update": ApiEntry(
        method="erp.fast.stock.update",
        description="即入即出匹配",
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
    ),
    "order_remark_update": ApiEntry(
        method="erp.trade.order.remark.update",
        description="修改订单商品备注",
        param_map={
            "order_id": "tid",
        },
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
    ),
    "order_remark_batch_update": ApiEntry(
        method="erp.trade.order.remark.batchUpdate",
        description="批量修改订单商品备注",
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
    ),
    "trade_halt": ApiEntry(
        method="erp.trade.halt",
        description="订单挂起",
        param_map={
            "order_id": "tid",
            "system_id": "sid",
        },
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
        confirm_template="确认挂起订单「{order_id}」？",
    ),
    "trade_unhalt": ApiEntry(
        method="erp.trade.unhalt",
        description="订单解挂",
        param_map={
            "order_id": "tid",
            "system_id": "sid",
        },
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
    ),
}
