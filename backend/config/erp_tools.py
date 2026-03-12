"""
ERP 工具定义（8个工具）

Registry + Category Dispatch 架构：
- 6个ERP查询工具：按类别分组，action enum 路由到具体API
- 1个淘宝奇门查询工具：通过淘宝网关查询订单/售后
- 1个执行工具：所有写操作，需用户确认

工具定义从 Registry 动态生成，新增API只需修改注册表。
"""

from typing import Any, Dict, List, Set

from services.kuaimai.registry import (
    AFTERSALES_REGISTRY,
    BASIC_REGISTRY,
    DISTRIBUTION_REGISTRY,
    PRODUCT_REGISTRY,
    PURCHASE_REGISTRY,
    QIMEN_REGISTRY,
    TRADE_REGISTRY,
    WAREHOUSE_REGISTRY,
)

# ERP 工具名集合
ERP_SYNC_TOOLS: Set[str] = {
    "erp_info_query",
    "erp_product_query",
    "erp_trade_query",
    "erp_aftersales_query",
    "erp_warehouse_query",
    "erp_purchase_query",
    "erp_taobao_query",
    "erp_execute",
}


def _read_actions(registry: dict) -> tuple:
    """从注册表提取读操作的 enum 列表和描述"""
    actions = []
    descs = []
    for name, entry in registry.items():
        if not entry.is_write:
            actions.append(name)
            descs.append(f"{name}={entry.description}")
    return actions, ", ".join(descs)


def _write_actions_by_category() -> str:
    """构建写操作分类描述（给 erp_execute 用）"""
    cats = {
        "basic": ("基础", BASIC_REGISTRY),
        "product": ("商品", PRODUCT_REGISTRY),
        "trade": ("交易", TRADE_REGISTRY),
        "aftersales": ("售后", AFTERSALES_REGISTRY),
        "warehouse": ("仓储", WAREHOUSE_REGISTRY),
        "purchase": ("采购", PURCHASE_REGISTRY),
        "distribution": ("分销", DISTRIBUTION_REGISTRY),
    }
    parts = []
    for cat_key, (cat_name, registry) in cats.items():
        writes = [
            f"{n}={e.description}"
            for n, e in registry.items() if e.is_write
        ]
        if writes:
            parts.append(f"{cat_key}({cat_name}): {', '.join(writes)}")
    return "; ".join(parts)


def _build_query_tool(
    name: str,
    desc: str,
    registry: dict,
    extra_params: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """构建单个查询工具定义"""
    actions, action_desc = _read_actions(registry)
    params = {
        "action": {
            "type": "string",
            "enum": actions,
            "description": action_desc,
        },
        **extra_params,
        "page": {
            "type": "integer",
            "description": "页码（默认1）",
        },
    }
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": {
                "type": "object",
                "properties": params,
                "required": ["action"],
            },
        },
    }


# ── ERP 工具 Schema（用于参数验证） ──────────────────
ERP_TOOL_SCHEMAS: Dict[str, Dict[str, Any]] = {
    tool: {"required": ["action"], "properties": {"action": {"type": "string"}}}
    for tool in ERP_SYNC_TOOLS if tool != "erp_execute"
}
ERP_TOOL_SCHEMAS["erp_execute"] = {
    "required": ["category", "action"],
    "properties": {
        "category": {"type": "string"},
        "action": {"type": "string"},
        "params": {"type": "object"},
    },
}


def build_erp_tools() -> List[Dict[str, Any]]:
    """构建8个ERP工具定义（6 ERP查询 + 1 淘宝奇门查询 + 1 写入）"""
    tools = [
        # 1. 基础信息查询
        _build_query_tool(
            "erp_info_query",
            "查询ERP基础信息：仓库、店铺、标签、客户、分销商。",
            BASIC_REGISTRY,
            {
                "name": {
                    "type": "string",
                    "description": "名称（仓库名/店铺名/客户名等）",
                },
                "code": {
                    "type": "string",
                    "description": "编码",
                },
            },
        ),
        # 2. 商品查询
        _build_query_tool(
            "erp_product_query",
            "查询ERP商品/SKU/库存/标签/分类/品牌信息。",
            PRODUCT_REGISTRY,
            {
                "keyword": {
                    "type": "string",
                    "description": "商品名称关键词",
                },
                "outer_id": {
                    "type": "string",
                    "description": "商家编码",
                },
                "item_id": {
                    "type": "string",
                    "description": "系统商品ID",
                },
                "barcode": {
                    "type": "string",
                    "description": "商品条码",
                },
                "warehouse_id": {
                    "type": "string",
                    "description": "仓库ID",
                },
                "start_date": {
                    "type": "string",
                    "description": "起始日期 yyyy-MM-dd",
                },
                "end_date": {
                    "type": "string",
                    "description": "结束日期 yyyy-MM-dd",
                },
            },
        ),
        # 3. 交易查询
        _build_query_tool(
            "erp_trade_query",
            "查询ERP订单/出库/物流/波次/唯一码信息。",
            TRADE_REGISTRY,
            {
                "order_id": {
                    "type": "string",
                    "description": "平台订单号",
                },
                "system_id": {
                    "type": "string",
                    "description": "系统单号",
                },
                "buyer": {
                    "type": "string",
                    "description": "买家昵称",
                },
                "status": {
                    "type": "string",
                    "description": (
                        "系统状态: WAIT_AUDIT(待审核), "
                        "SELLER_SEND_GOODS(已发货), "
                        "CLOSED(已关闭), FINISHED(已完成)"
                    ),
                },
                "time_type": {
                    "type": "string",
                    "description": (
                        "时间类型: created(下单时间), "
                        "pay_time(付款时间), "
                        "consign_time(发货时间), "
                        "audit_time(审核时间), "
                        "upd_time(修改时间,默认)"
                    ),
                },
                "start_date": {
                    "type": "string",
                    "description": "起始日期 yyyy-MM-dd",
                },
                "end_date": {
                    "type": "string",
                    "description": "结束日期 yyyy-MM-dd",
                },
                "shop_name": {
                    "type": "string",
                    "description": "店铺名称筛选",
                },
                "express_no": {
                    "type": "string",
                    "description": "快递单号",
                },
            },
        ),
        # 4. 售后查询
        _build_query_tool(
            "erp_aftersales_query",
            "查询ERP售后工单/退货/维修单/补款/日志。",
            AFTERSALES_REGISTRY,
            {
                "order_id": {
                    "type": "string",
                    "description": "平台订单号",
                },
                "work_order_no": {
                    "type": "string",
                    "description": "售后工单号",
                },
                "status": {
                    "type": "string",
                    "description": "状态筛选",
                },
                "start_date": {
                    "type": "string",
                    "description": "起始日期 yyyy-MM-dd",
                },
                "end_date": {
                    "type": "string",
                    "description": "结束日期 yyyy-MM-dd",
                },
            },
        ),
        # 5. 仓储查询
        _build_query_tool(
            "erp_warehouse_query",
            "查询ERP调拨/入出库/盘点/下架/货位/加工单信息。",
            WAREHOUSE_REGISTRY,
            {
                "order_no": {
                    "type": "string",
                    "description": "单号",
                },
                "status": {
                    "type": "string",
                    "description": "状态筛选",
                },
                "outer_id": {
                    "type": "string",
                    "description": "商家编码",
                },
                "warehouse_id": {
                    "type": "string",
                    "description": "仓库ID",
                },
                "start_date": {
                    "type": "string",
                    "description": "起始日期 yyyy-MM-dd",
                },
                "end_date": {
                    "type": "string",
                    "description": "结束日期 yyyy-MM-dd",
                },
            },
        ),
        # 6. 采购查询
        _build_query_tool(
            "erp_purchase_query",
            "查询ERP供应商/采购单/收货单/采退单/上架单/采购建议。",
            PURCHASE_REGISTRY,
            {
                "purchase_no": {
                    "type": "string",
                    "description": "采购单号",
                },
                "supplier_name": {
                    "type": "string",
                    "description": "供应商名称",
                },
                "status": {
                    "type": "string",
                    "description": "状态筛选",
                },
                "start_date": {
                    "type": "string",
                    "description": "起始日期 yyyy-MM-dd",
                },
                "end_date": {
                    "type": "string",
                    "description": "结束日期 yyyy-MM-dd",
                },
            },
        ),
        # 7. 淘宝奇门查询（通过淘宝网关）
        _build_query_tool(
            "erp_taobao_query",
            (
                "查询淘宝/天猫平台的订单和售后单（通过奇门接口）。"
                "返回 {total, trades/workOrders[]}。"
                "page_size=1 可只取计数。支持 shop_id 按店铺筛选。"
            ),
            QIMEN_REGISTRY,
            {
                "tid": {
                    "type": "string",
                    "description": "平台订单号",
                },
                "sid": {
                    "type": "string",
                    "description": "系统订单号",
                },
                "status": {
                    "type": "string",
                    "description": (
                        "订单状态: WAIT_BUYER_PAY(待付款), "
                        "WAIT_AUDIT(待审核), "
                        "WAIT_SEND_GOODS(待发货), "
                        "SELLER_SEND_GOODS(已发货), "
                        "FINISHED(交易完成), CLOSED(交易关闭)"
                    ),
                },
                "date_type": {
                    "type": "integer",
                    "description": (
                        "时间类型: 0=修改时间(默认), "
                        "1=创建时间, 2=线上下单时间, 3=发货时间"
                    ),
                },
                "shop_id": {
                    "type": "integer",
                    "description": "店铺编号",
                },
                "warehouse_id": {
                    "type": "integer",
                    "description": "订单分仓ID",
                },
                "start_date": {
                    "type": "string",
                    "description": "起始时间 yyyy-MM-dd",
                },
                "end_date": {
                    "type": "string",
                    "description": "结束时间 yyyy-MM-dd",
                },
                "types": {
                    "type": "string",
                    "description": (
                        "订单类型(逗号分隔): "
                        "0=普通, 7=合并, 8=拆分, 33=分销, 99=出库单 等"
                    ),
                },
                "refund_type": {
                    "type": "integer",
                    "description": (
                        "售后类型(仅refund_list): "
                        "1=退款, 2=退货, 3=补发, 4=换货, 5=发货前退款"
                    ),
                },
                "refund_id": {
                    "type": "integer",
                    "description": "售后工单号(仅refund_list)",
                },
                "page_size": {
                    "type": "integer",
                    "description": "每页条数(默认20, 最小1)",
                },
            },
        ),
        # 8. 写入/执行操作
        {
            "type": "function",
            "function": {
                "name": "erp_execute",
                "description": (
                    "执行ERP写操作（新增/修改/删除/作废等）。"
                    "操作前需用户确认。分类: "
                    + _write_actions_by_category()
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "category": {
                            "type": "string",
                            "enum": [
                                "basic", "product", "trade",
                                "aftersales", "warehouse",
                                "purchase", "distribution",
                            ],
                            "description": "操作分类",
                        },
                        "action": {
                            "type": "string",
                            "description": "操作名称（见各分类的写操作列表）",
                        },
                        "params": {
                            "type": "object",
                            "description": "操作参数（根据具体操作传入）",
                        },
                    },
                    "required": ["category", "action"],
                },
            },
        },
    ]
    return tools


# ERP 路由提示词片段
ERP_ROUTING_PROMPT = (
    "## ERP数据查询规则\n"
    "- 基础信息（仓库/店铺/标签/客户/分销商） → erp_info_query\n"
    "- 商品/SKU/库存/品牌/分类 → erp_product_query\n"
    "- 订单/出库/物流/波次 → erp_trade_query\n"
    "- 售后工单/退货/维修 → erp_aftersales_query\n"
    "- 调拨/入出库/盘点/货位/加工 → erp_warehouse_query\n"
    "- 供应商/采购/收货/上架 → erp_purchase_query\n"
    "- 淘宝/天猫订单查询 → erp_taobao_query(action=order_list)\n"
    "- 淘宝/天猫售后单 → erp_taobao_query(action=refund_list)\n"
    "- 写操作（新增/修改/删除/作废） → erp_execute\n"
    "- 如果ERP未配置，直接告知用户需要配置快麦ERP\n\n"
    "## ERP多步查询策略\n"
    "- 统计类问题（如'今天多少单'）：用 page_size=1 只取 total，不要翻页\n"
    "- 分维度统计（如'每个平台多少单'）：先查 shop_list 获取店铺列表，"
    "再按 shop_id 逐个查 total\n"
    "- 只在用户需要看明细时才用大 page_size\n"
    "- 复杂问题可跨类别多次查询（如先查订单再查库存再查供应商）\n"
    "- 查询订单时注意选择正确的 time_type/date_type\n"
    "- 所有必要数据收集完毕后，再用 route_to_chat 汇总回复用户\n"
)
