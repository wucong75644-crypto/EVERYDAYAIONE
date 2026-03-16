"""
奇门自定义接口 API注册表

通过淘宝奇门网关调用快麦API：
- kuaimai.order.list.query: 淘宝订单查询
- kuaimai.refund.list.query: 淘宝售后单查询

网关地址和系统参数（target_app_key/customerId）从 config 读取。
"""

from core.config import settings
from services.kuaimai.registry.base import ApiEntry

QIMEN_REGISTRY = {
    "order_list": ApiEntry(
        method="kuaimai.order.list.query",
        description="淘宝订单列表查询",
        base_url=settings.qimen_order_url,
        system_params={"target_app_key": settings.qimen_target_app_key},
        response_key="trades",
        param_map={
            "tid": "tid",
            "order_id": "tid",  # 兼容 TRADE 风格参数名
            "sid": "sid",
            "system_id": "sid",  # 兼容 TRADE 风格参数名
            "status": "status",
            "date_type": "dateType",
            "time_type": "dateType",  # 兼容 TRADE 风格参数名
            "shop_id": "userId",
            "warehouse_id": "warehouseId",
            "start_date": "startTime",
            "end_date": "endTime",
            "types": "types",
            "tag_ids": "tagIds",
        },
        param_docs={
            "tid": "淘宝/天猫平台订单号（18位纯数字）。与sid二选一。示例: 126036803257340376",
            "order_id": "淘宝/天猫平台订单号（18位纯数字）。tid的别名，与sid二选一。示例: 126036803257340376",
            "sid": "ERP系统单号（16位纯数字）。与tid二选一。示例: 5759422420146938",
            "system_id": "ERP系统单号（16位纯数字）。sid的别名，与tid二选一。示例: 5759422420146938",
            "status": "订单状态。可选值: wait_check(待审核), wait_goods(待配货), wait_consign(待发货), consigned(已发货), cancelled(已作废), suspended(挂起中), wait_merge(待合并)。多个逗号隔开。示例: wait_check,wait_goods",
            "date_type": "时间类型。可选值: create(创建时间), modified(修改时间), pay(付款时间), consign(发货时间)。默认create。示例: create",
            "time_type": "时间类型。date_type的别名。可选值: create(创建时间), modified(修改时间), pay(付款时间), consign(发货时间)。示例: create",
            "shop_id": "店铺ID（通过shop_list获取）。示例: 12345",
            "warehouse_id": "仓库ID（通过warehouse_list获取）。示例: 1001",
            "start_date": "起始日期。格式: YYYY-MM-DD。示例: 2026-03-01",
            "end_date": "结束日期。格式: YYYY-MM-DD。示例: 2026-03-15",
            "types": "订单类型（多个逗号隔开）。可选值: fixed(一口价), auction(拍卖), step(分阶段), presell(预售)。示例: fixed",
            "tag_ids": "标签ID（多个逗号隔开，通过tag_list获取）。示例: 101,102",
        },
        formatter="format_qimen_order_list",
    ),
    "refund_list": ApiEntry(
        method="kuaimai.refund.list.query",
        description="淘宝售后单列表查询",
        base_url=settings.qimen_refund_url,
        system_params={"target_app_key": settings.qimen_target_app_key},
        response_key="workOrders",
        param_map={
            "tid": "tid",
            "refund_id": "id",
            "refund_type": "refundType",
            "as_version": "asVersion",
            "shop_id": "userId",
            "warehouse_id": "warehouseId",
            "start_date": "startTime",
            "end_date": "endTime",
        },
        param_docs={
            "tid": "淘宝/天猫平台订单号（18位纯数字）。示例: 126036803257340376",
            "refund_id": "售后工单ID。示例: 50001",
            "refund_type": "退款类型。可选值: 1(仅退款), 2(退货退款), 3(换货), 4(补发)。示例: 2",
            "as_version": "售后版本。默认2。可选值: 1(旧版), 2(新版)。示例: 2",
            "shop_id": "店铺ID（通过shop_list获取）。示例: 12345",
            "warehouse_id": "仓库ID（通过warehouse_list获取）。示例: 1001",
            "start_date": "起始日期。格式: YYYY-MM-DD。示例: 2026-03-01",
            "end_date": "结束日期。格式: YYYY-MM-DD。示例: 2026-03-15",
        },
        defaults={"asVersion": 2},
        formatter="format_qimen_refund_list",
    ),
}
