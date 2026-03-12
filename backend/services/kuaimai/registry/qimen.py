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
            "sid": "sid",
            "status": "status",
            "date_type": "dateType",
            "shop_id": "userId",
            "warehouse_id": "warehouseId",
            "start_date": "startTime",
            "end_date": "endTime",
            "types": "types",
            "tag_ids": "tagIds",
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
        defaults={"asVersion": 2},
        formatter="format_qimen_refund_list",
    ),
}
