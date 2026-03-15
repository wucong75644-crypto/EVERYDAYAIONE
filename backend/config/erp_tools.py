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


def _format_action_desc(name: str, entry: "ApiEntry") -> str:
    """生成单个 action 的丰富描述：name=描述(参数列表)

    必填参数标记 * 前缀，无参数的 action 不加括号。
    示例：order_list=订单查询(order_id/buyer/status/*platform_ids)
    """
    params = list(entry.param_map.keys())
    if not params:
        return f"{name}={entry.description}"
    param_parts = [
        f"*{p}" if p in entry.required_params else p
        for p in params
    ]
    return f"{name}={entry.description}({'/'.join(param_parts)})"


def _read_actions(registry: dict) -> tuple:
    """从注册表提取读操作的 enum 列表和丰富描述（含参数名）"""
    actions = []
    descs = []
    for name, entry in registry.items():
        if not entry.is_write:
            actions.append(name)
            descs.append(_format_action_desc(name, entry))
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
            _format_action_desc(n, e)
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
        "page_size": {
            "type": "integer",
            "description": "每页条数（默认20，仅取计数可设1，查全量可设100-200）",
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
                "sku_outer_id": {
                    "type": "string",
                    "description": (
                        "SKU商家编码（与主编码二选一，"
                        "多个逗号隔开）"
                    ),
                },
                "brand": {
                    "type": "string",
                    "description": "品牌名称（多个逗号隔开）",
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
                        "系统状态(多个逗号隔开): "
                        "WAIT_BUYER_PAY(待付款), WAIT_AUDIT(待审核), "
                        "WAIT_FINANCE_AUDIT(待财审), FINISHED_AUDIT(审核完成), "
                        "WAIT_EXPRESS_PRINT(待打印快递单), WAIT_PACKAGE(待打包), "
                        "WAIT_WEIGHT(待称重), WAIT_SEND_GOODS(待发货), "
                        "SELLER_SEND_GOODS(已发货), "
                        "FINISHED(交易完成), CLOSED(交易关闭)"
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
                "shop_ids": {
                    "type": "string",
                    "description": (
                        "店铺ID（多个逗号隔开，最多10组，"
                        "通过 shop_list 查询获取）"
                    ),
                },
                "order_types": {
                    "type": "string",
                    "description": (
                        "订单类型（逗号分隔: 0=普通,7=合并,"
                        "8=拆分,24=京东直发,33=分销,99=出库单）"
                    ),
                },
                "tag_ids": {
                    "type": "string",
                    "description": (
                        "标签ID（多个逗号隔开，最多10组，"
                        "通过 tag_list 查询获取）"
                    ),
                },
                "express_no": {
                    "type": "string",
                    "description": "快递单号（多个逗号隔开）",
                },
                "query_type": {
                    "type": "integer",
                    "description": (
                        "查询范围: 0=三个月内订单(默认), "
                        "1=归档订单(三个月前)"
                    ),
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
                "work_order_id": {
                    "type": "string",
                    "description": "售后工单号/ID",
                },
                "type": {
                    "type": "string",
                    "description": (
                        "售后类型(仅aftersale_list): "
                        "0=其他,1=已发货仅退款,2=退货,3=补发,"
                        "4=换货,5=未发货仅退款,7=拒收退货,9=维修"
                    ),
                },
                "shop_ids": {
                    "type": "string",
                    "description": (
                        "店铺ID（多个逗号隔开，"
                        "通过 shop_list 查询获取）"
                    ),
                },
                "status": {
                    "type": "string",
                    "description": (
                        "状态筛选（不同action值不同，"
                        "维修单用repairStatus: 0=待审核,1=待收货,"
                        "2=待维修,3=待寄出,4=已完成,-1=已作废）"
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
            },
        ),
        # 5. 仓储查询
        _build_query_tool(
            "erp_warehouse_query",
            "查询ERP调拨/入出库/盘点/下架/货位/加工单信息。",
            WAREHOUSE_REGISTRY,
            {
                "code": {
                    "type": "string",
                    "description": "单据号/业务单据号",
                },
                "status": {
                    "type": "string",
                    "description": "状态筛选（不同action值不同）",
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
                "code": {
                    "type": "string",
                    "description": "单据号/采购单号",
                },
                "outer_code": {
                    "type": "string",
                    "description": "外部采购订单号",
                },
                "status": {
                    "type": "string",
                    "description": (
                        "状态筛选（不同action值不同，"
                        "采购单: WAIT_VERIFY=草稿,VERIFYING=待审核,"
                        "GOODS_NOT_ARRIVED=未到货,FINISHED=已完成）"
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
                "tag_ids": {
                    "type": "string",
                    "description": (
                        "标签ID（多个逗号隔开，"
                        "仅order_list，通过 tag_list 查询获取）"
                    ),
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
    "- 不确定用哪个action或参数时 → 先调 erp_api_search 查询API文档\n\n"
    "## 名称纠错与验证\n"
    "- 用户输入的名称（店铺/买家/商品/供应商等）可能有错别字或谐音字\n"
    "- 按名称查询无结果时，禁止直接返回「未找到」，必须用 ask_user 反问用户：\n"
    '  「没有找到名为"XXX"的店铺/买家/商品，请确认名称是否正确」\n'
    "- 如果用户的名称看起来像错别字（生僻组合、不像正常名称），"
    "查询前就应先确认，避免浪费无效查询\n\n"
    "## ERP 时间类型选择指南\n"
    "- 「今天多少订单」「新增订单」→ time_type=\"created\"（下单时间）\n"
    "- 「今日成交」「今日付款」「已付款订单」→ time_type=\"pay_time\"（付款时间）\n"
    "- 「今天发了多少」「发货统计」→ time_type=\"consign_time\"（发货时间）\n"
    "- 不传 time_type 默认按修改时间查，通常不是用户想要的\n\n"
    "## 易混淆场景决策（P0 高频）\n"
    "### 库存查询（5种，按场景选）\n"
    "- 「XX商品库存多少」→ erp_product_query(stock_status, outer_id=XX) 最常用，各仓汇总\n"
    "- 「这个商品在各仓库分别多少」→ erp_product_query(warehouse_stock, outer_id=XX) 按仓拆分\n"
    "- 「这个商品最近进出了多少」→ erp_product_query(stock_in_out, outer_id=XX) 时间轴流水\n"
    "- 「批次效期库存」→ erp_warehouse_query(batch_stock_list) 需要shop_id\n"
    "- 「货位上有什么」→ erp_warehouse_query(goods_section_list) 货位维度\n"
    "- 「虚拟仓」→ erp_product_query(virtual_warehouse)\n\n"
    "### 售后查询（5种，跨3个工具）\n"
    "- 「退货单」「售后工单」→ erp_aftersales_query(aftersale_list) 全平台售后，默认选这个\n"
    "- 「淘宝退款」「天猫售后」→ erp_taobao_query(refund_list) 仅淘宝/天猫\n"
    "- 「退货入库了吗」「退回仓库」→ erp_aftersales_query(refund_warehouse) 关注货物入仓\n"
    "- 「补款」「退了多少钱」→ erp_aftersales_query(replenish_list) 补款记录\n"
    "- 「维修单」→ erp_aftersales_query(repair_list)\n"
    "- 「这个售后单的操作记录」→ erp_aftersales_query(aftersale_log, work_order_id=XX)\n\n"
    "### 出库查询（4种，跨3个工具）\n"
    "- 「今天出了多少单」「销售出库」→ erp_trade_query(outstock_query) 订单维度销售出库\n"
    "- 「出库单详情」→ erp_trade_query(outstock_order_query) 出库单维度\n"
    "- 「这个商品最近出入库记录」→ erp_product_query(stock_in_out) 商品维度流水\n"
    "- 「其他出库」「报损出库」→ erp_warehouse_query(other_out_list) 非销售出库\n"
    "- 同理入库：「其他入库」→ erp_warehouse_query(other_in_list)\n\n"
    "### 三个月归档差异\n"
    "- 订单模块：query_type=1 即可查归档订单（同一个action）\n"
    "- 采购模块：必须换action！purchase_order_list → purchase_order_history，"
    "warehouse_entry_list → warehouse_entry_history，"
    "purchase_return_list → purchase_return_history，"
    "shelf_list → shelf_history\n"
    "- 归档action都需要 start_date + end_date 必填\n\n"
    "### 标签查询（同名action，不同工具！）\n"
    "- 「订单标签」→ erp_info_query(tag_list)\n"
    "- 「商品标签」→ erp_product_query(tag_list)\n"
    "- 两个tag_list完全不同，根据上下文判断用户说的是订单标签还是商品标签\n\n"
    "### erp_trade_query vs erp_taobao_query 选择\n"
    "- 默认查订单 → erp_trade_query（ERP内部数据，覆盖全平台）\n"
    "- 用户明确说「淘宝/天猫订单」→ erp_taobao_query\n"
    "- 注意时间参数不兼容！\n"
    "  erp_trade_query: time_type=\"created\"/\"pay_time\"/\"consign_time\"（字符串）\n"
    "  erp_taobao_query: date_type=1(创建)/0(修改)/2(下单)/3(发货)（整数）\n"
    "- 同理售后：默认 → erp_aftersales_query，明确淘宝 → erp_taobao_query(refund_list)\n\n"
    "### 售后类型值映射\n"
    "- 「退货」→ type=2 (aftersale_list) 或 refund_type=2 (erp_taobao_query)\n"
    "- 「换货」→ type=4 / refund_type=4\n"
    "- 「补发」→ type=3 / refund_type=3\n"
    "- 「仅退款（已发货）」→ type=1 / refund_type=1\n"
    "- 「仅退款（未发货）」→ type=5 / refund_type=5\n\n"
    "### 订单状态日常用语→系统值\n"
    "- 「未付款/待付款」→ status=\"WAIT_BUYER_PAY\"\n"
    "- 「待审核」→ status=\"WAIT_AUDIT\"\n"
    "- 「待发货」→ status=\"WAIT_SEND_GOODS\"\n"
    "- 「已发货」→ status=\"SELLER_SEND_GOODS\"\n"
    "- 「已完成」→ status=\"FINISHED\"\n"
    "- 「已关闭/已取消」→ status=\"CLOSED\"\n"
    "- 「未完成的订单」→ status=\"WAIT_AUDIT,WAIT_SEND_GOODS,SELLER_SEND_GOODS\"（多状态逗号分隔）\n"
    "- 「异常订单」「有问题的」→ 含义不明确，必须用 ask_user 追问具体什么状态\n\n"
    "### 必填参数陷阱（缺失会报错）\n"
    "- refund_warehouse: 必须传 time_type（如 time_type=\"created\"）\n"
    "- history_cost_price: 必须传 item_id + sku_id（两个都要）\n"
    "- batch_stock_list: 必须传 shop_id\n"
    "- order_log: 只接受 system_ids，不接受 order_id！\n"
    "  用户说「这个订单的操作记录」→ 先 order_list(order_id=XX) 拿 system_id → "
    "再 order_log(system_ids=XX)\n"
    "- 所有 _history 归档action: 必须传 start_date + end_date\n\n"
    "### 物流查询细分（4种action）\n"
    "- 「查快递」「快递到哪了」→ erp_trade_query(express_query, system_id=XX 或 express_no=XX)\n"
    "- 「物流公司列表」→ erp_trade_query(logistics_company_list) 配置数据\n"
    "- 「获取快递单号」→ erp_trade_query(waybill_get, system_ids=XX)\n"
    "- 不要把「查快递」误选成 logistics_company_list\n\n"
    "### shop_name vs shop_ids 使用策略\n"
    "- erp_trade_query 支持 shop_name 直接传店铺名称（模糊匹配）\n"
    "- 统计场景（分店铺汇总）→ 必须先 shop_list 拿 shop_ids，逐个精确查\n"
    "- 单次查询筛选 → 可以直接用 shop_name\n\n"
    "## 易混淆场景决策（P1 中频）\n"
    "### 商品查询action选择\n"
    "- 「搜商品/商品列表」→ product_list（列表搜索，支持状态/日期筛选）\n"
    "- 「某个商品详情」→ product_detail(outer_id=XX 或 item_id=XX) 按编码或ID查单个\n"
    "- 「批量查这几个商品」→ multi_product(outer_ids=\"A,B,C\") 多个编码\n"
    "- 「商品SKU信息」→ sku_list(outer_id=XX) 或 sku_info(sku_outer_id=XX)\n"
    "- 「条码查商品」→ multicode_query(code=XX) 多码查询\n"
    "- 「这个商品的供应商」→ item_supplier_list(outer_ids=XX)\n"
    "- 「商品成本价」→ history_cost_price(item_id=XX, sku_id=XX) 两个都必填\n"
    "- 「商品对应关系/在哪个店铺卖」→ outer_id_list(outer_ids=XX)\n"
    "- 「商品分类」→ cat_list（卖家自定义分类）或 classify_list（系统类目）\n"
    "- 「品牌列表」→ brand_list\n\n"
    "### 调拨三种单据\n"
    "- 「调拨单」→ erp_warehouse_query(allocate_list) 调拨任务单\n"
    "- 「调拨入库」→ erp_warehouse_query(allocate_in_list) 入库端\n"
    "- 「调拨出库」→ erp_warehouse_query(allocate_out_list) 出库端\n"
    "- 「某个调拨单明细」→ erp_warehouse_query(allocate_detail, code=XX)\n"
    "- 同理：other_in/other_out 各有 _list 和 _detail，"
    "inventory_sheet/unshelve/process_order 也各有 _list 和 _detail\n\n"
    "### 采购链路4阶段（按业务流程顺序）\n"
    "- 「采购单」→ erp_purchase_query(purchase_order_list)\n"
    "- 「到货了没/收货单」→ erp_purchase_query(warehouse_entry_list)\n"
    "- 「上架了没/上架单」→ erp_purchase_query(shelf_list)\n"
    "- 「采购退货/采退单」→ erp_purchase_query(purchase_return_list)\n"
    "- 「采购建议/该进什么货」→ erp_purchase_query(purchase_strategy, query_key=关键词)\n"
    "- 「供应商列表」→ erp_purchase_query(supplier_list)\n"
    "- 各阶段都有对应的 _detail action 查看单据详情\n\n"
    "### 订单号vs系统单号\n"
    "- 用户说「订单号」通常指平台订单号 → 用 order_id 参数\n"
    "- 用户说「系统单号」「ERP单号」→ 用 system_id 参数\n"
    "- 不确定时：先用 order_id 查，无结果再用 system_id 查\n"
    "- 纯数字长串（如20位+）多为平台订单号，短编号多为系统单号\n\n"
    "### 分销商查询\n"
    "- 「分销商列表」（简单信息）→ erp_info_query(distributor_list)\n"
    "- 「分销商品/供销小店」→ 涉及分销模块，用 erp_api_search(\"分销\") 查文档\n\n"
    "## 多步查询链路（P2）\n"
    "### 需要先查ID再查详情\n"
    "- 「某订单的快递」→ 先 order_list(order_id=XX) 拿 system_id → "
    "再 express_query(system_id=XX)\n"
    "- 「某采购单收货了没」→ 先 purchase_order_detail(purchase_id=XX) "
    "→ 再 warehouse_entry_list 关联查询\n"
    "- 「某商品在哪个店铺卖」→ outer_id_list(outer_ids=XX) 查对应关系\n\n"
    "### 统计类汇总策略\n"
    "- 「今天成交多少钱」→ 不能只看total，需要page_size=20拉明细算payment总和\n"
    "- 「退货率」→ 先查订单总量(page_size=1取total)，"
    "再查退货总量(page_size=1取total)，计算比率\n"
    "- 「各仓库库存」→ 先 warehouse_list 获取仓库列表，"
    "再逐仓库 stock_status(warehouse_id=XX)\n"
    "- 「各店铺XX」→ 先 shop_list 获取店铺列表，再逐店铺查询\n\n"
    "### 查不到时的降级策略\n"
    "- 订单查不到 → 检查是否需要 query_type=1（归档订单）\n"
    "- 采购单查不到 → 检查是否需要换 _history action\n"
    "- 按order_id查不到 → 尝试用system_id查\n"
    "- 按名称查不到 → 必须 ask_user 确认名称，禁止直接返回「未找到」\n\n"
    "## ERP调用示例\n"
    "用户：「今天多少订单」\n"
    "→ erp_trade_query(action=\"order_list\", time_type=\"created\", "
    "start_date=\"{today}\", end_date=\"{today}\", page_size=1)\n"
    "→ 只看 total 字段，不需要翻页\n\n"
    "用户：「统计今日成交」\n"
    "→ 第1步: erp_info_query(action=\"shop_list\") 获取所有店铺ID和名称\n"
    "→ 第2步: 对每个店铺 erp_trade_query(action=\"order_list\", "
    "shop_ids=\"店铺ID\", time_type=\"pay_time\", "
    "start_date=\"{today}\", end_date=\"{today}\", page_size=20)\n"
    "→ 汇总各店铺的 total 和 payment 金额\n"
    "→ 注意：必须先查shop_list拿到所有店铺，再逐店铺查，"
    "否则会漏掉部分平台的订单\n\n"
    "用户：「查订单 123456789」\n"
    "→ erp_trade_query(action=\"order_list\", order_id=\"123456789\")\n\n"
    "用户：「每个店铺今天发了多少单」\n"
    "→ 第1步: erp_info_query(action=\"shop_list\") 获取所有店铺\n"
    "→ 第2步: 对每个店铺 erp_trade_query(action=\"order_list\", "
    "shop_ids=\"店铺ID\", time_type=\"consign_time\", "
    "start_date=\"{today}\", end_date=\"{today}\", page_size=1)\n"
    "→ 汇总各店铺 total\n\n"
    "用户：「商品ABC123的库存」\n"
    "→ erp_product_query(action=\"stock_status\", outer_id=\"ABC123\")\n\n"
    "用户：「最近7天的退货单」\n"
    "→ erp_aftersales_query(action=\"aftersale_list\", "
    "start_date=\"{7天前}\", end_date=\"{today}\")\n"
)


def build_erp_search_tool() -> Dict[str, Any]:
    """构建 erp_api_search 工具定义"""
    return {
        "type": "function",
        "function": {
            "name": "erp_api_search",
            "description": (
                "搜索 ERP 可用的 API 操作和参数文档。"
                "当你不确定该用哪个 action 或哪些参数时调用此工具。"
                "支持关键词搜索（如「退款」「库存」）"
                "和精确查询（如「erp_trade_query:order_list」）。"
                "结果会返回给你，你可以参考后再决定调用哪个 ERP 工具。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "搜索关键词或 tool:action 精确查询"
                        ),
                    },
                },
                "required": ["query"],
            },
        },
    }
