"""
快麦通(分销) API注册表

包含：分销商品、分销商管理、同步 相关API
"""

from services.kuaimai.registry.base import ApiEntry

DISTRIBUTION_REGISTRY = {
    # ── 查询 ──────────────────────────────────────────
    "distributor_item_list": ApiEntry(
        method="kmt.api.dms.query.page.distributor.item",
        description="分页查询供销小店商品",
        formatter="format_generic_list",
    ),
    "distributor_item_detail": ApiEntry(
        method="kmt.api.dms.query.detail.distributor.item",
        description="查询供销小店商品详情",
        formatter="format_generic_detail",
        response_key=None,
    ),
    "supplier_view_item_list": ApiEntry(
        method="kmt.api.dms.query.page.distributor.item.supplier.view",
        description="供销商视角分页供销小店商品",
        formatter="format_generic_list",
    ),
    "supplier_view_item_detail": ApiEntry(
        method="kmt.api.dms.query.detail.distributor.item.supplier.view",
        description="供销商视角查询供销小店商品详情",
        formatter="format_generic_detail",
        response_key=None,
    ),
    "distributor_list": ApiEntry(
        method="kmt.api.dms.query.distributor.list",
        description="分销商信息查询",
        formatter="format_generic_list",
    ),
    "pay_prompt": ApiEntry(
        method="kmt.api.dms.pay.prompt",
        description="查询在线支付方式提示文案",
        formatter="format_generic_detail",
        response_key=None,
    ),
    "item_video_info": ApiEntry(
        method="kmt.api.dms.query.item.video.info",
        description="获取最新的视频链接信息",
        formatter="format_generic_detail",
        response_key=None,
    ),
    # ── 写入操作 ──────────────────────────────────────
    "add_distributor_money": ApiEntry(
        method="kmt.api.dms.add.distributor.money",
        description="增加分销余额",
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
        confirm_template="确认增加分销余额？",
    ),
    "add_distribution_item": ApiEntry(
        method="kmt.api.dms.add.distribution.item.fromsupplier",
        description="添加分销商品",
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
    ),
    "add_distributor": ApiEntry(
        method="kmt.api.dms.add.distributor",
        description="注册分销商",
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
        confirm_template="确认注册新分销商？",
    ),
    "submit_sync_item": ApiEntry(
        method="kmt.api.dms.submit.sync.item",
        description="提交分销小店商品的同步",
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
    ),
    "sync_status_item": ApiEntry(
        method="kmt.api.dms.sync.status.item",
        description="获取小店商品的同步状态",
        formatter="format_generic_detail",
        response_key=None,
    ),
}
