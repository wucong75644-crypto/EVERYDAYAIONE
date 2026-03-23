"""
ERP 工具定义（8个API工具 + 8个本地查询工具）

Registry + Category Dispatch 架构：
- 6个ERP查询工具：按类别分组，action enum 路由到具体API
- 1个淘宝奇门查询工具：通过淘宝网关查询订单/售后
- 1个执行工具：所有写操作，需用户确认
- 8个本地查询工具：直接查PostgreSQL，毫秒级响应，优先使用

工具定义从 Registry 动态生成，新增API只需修改注册表。
"""

from typing import Any, Dict, List, Set

from config.erp_local_tools import (
    ERP_LOCAL_TOOLS,
    LOCAL_ROUTING_PROMPT,
    LOCAL_TOOL_SCHEMAS,
    build_local_tools,
)
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
) -> Dict[str, Any]:
    """构建单个查询工具定义（两步调用模式）

    Step 1: LLM 只传 action → 系统返回参数文档
    Step 2: LLM 在 params 中传入具体参数 → 系统执行查询
    """
    actions, action_desc = _read_actions(registry)
    params = {
        "action": {
            "type": "string",
            "enum": actions,
            "description": action_desc,
        },
        "params": {
            "type": "object",
            "description": (
                "操作参数。首次调用只传action获取参数文档，"
                "然后根据文档传入具体参数再次调用。"
                "已确定参数时可直接传入跳过文档。"
            ),
        },
        "page": {
            "type": "integer",
            "description": "页码（默认1）",
        },
        "page_size": {
            "type": "integer",
            "description": "每页条数（默认20，最小20）",
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
    tool: {
        "required": ["action"],
        "properties": {
            "action": {"type": "string"},
            "params": {"type": "object"},
        },
    }
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
ERP_TOOL_SCHEMAS.update(LOCAL_TOOL_SCHEMAS)


def build_erp_tools() -> List[Dict[str, Any]]:
    """构建16个ERP工具定义（8个API + 8个本地查询）"""
    tools = [
        # 1. 基础信息查询
        _build_query_tool(
            "erp_info_query",
            "查询ERP基础信息：仓库、店铺、标签、客户、分销商。",
            BASIC_REGISTRY,
        ),
        # 2. 商品查询
        _build_query_tool(
            "erp_product_query",
            "查询ERP商品/SKU/库存/标签/分类/品牌信息。",
            PRODUCT_REGISTRY,
        ),
        # 3. 交易查询
        _build_query_tool(
            "erp_trade_query",
            "查询ERP订单/出库/物流/波次/唯一码信息。",
            TRADE_REGISTRY,
        ),
        # 4. 售后查询
        _build_query_tool(
            "erp_aftersales_query",
            "查询ERP售后工单/退货/维修单/补款/日志。",
            AFTERSALES_REGISTRY,
        ),
        # 5. 仓储查询
        _build_query_tool(
            "erp_warehouse_query",
            "查询ERP调拨/入出库/盘点/下架/货位/加工单信息。",
            WAREHOUSE_REGISTRY,
        ),
        # 6. 采购查询
        _build_query_tool(
            "erp_purchase_query",
            "查询ERP供应商/采购单/收货单/采退单/上架单/采购建议。",
            PURCHASE_REGISTRY,
        ),
        # 7. 淘宝奇门查询（通过淘宝网关）
        _build_query_tool(
            "erp_taobao_query",
            (
                "查询淘宝/天猫平台的订单和售后单（通过奇门接口）。"
                "返回 {total, trades/workOrders[]}。"
                "page_size最小20。支持 shop_id 按店铺筛选。"
            ),
            QIMEN_REGISTRY,
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
    return tools + build_local_tools()


# ERP 路由提示词片段
ERP_ROUTING_PROMPT = (
    "## ERP两步查询模式\n"
    "1. 第一步只传 action → 系统返回该 action 的详细参数文档（含歧义消解提示）\n"
    "2. 第二步在 params 中传入具体参数 → 系统执行查询\n"
    "3. 简单统计查询（如'今天多少单'）可直接传 params 跳过文档\n"
    "4. page/page_size 在 tool 级别传，不放 params 里\n"
    "5. 需要用户提供的参数（如订单号、编码等），参照参数文档中的示例格式提示用户\n\n"
    "## ⚡ 查询优先级（重要！）\n"
    "1. 商品编码/SKU编码/条码/商品名/库存/订单/售后/采购 → 先用 local_ 开头的本地工具（毫秒级）\n"
    "2. 本地查不到或需要写操作/复杂查询 → 再用远程 API 工具\n"
    "3. 编码/单号类型不确定 → local_product_identify(code=XX) 识别\n\n"
    "## ERP远程API工具（本地查不到时使用）\n"
    "- 基础信息（仓库/店铺/标签/客户/分销商） → erp_info_query\n"
    "- 商品/SKU/库存/品牌/分类（本地无结果时） → erp_product_query\n"
    "- 订单/出库/物流/波次 → erp_trade_query\n"
    "- 售后工单/退货/维修 → erp_aftersales_query\n"
    "- 调拨/入出库/盘点/货位/加工 → erp_warehouse_query\n"
    "- 供应商/采购/收货/上架 → erp_purchase_query\n"
    "- 淘宝/天猫订单或售后 → erp_taobao_query\n"
    "- 写操作 → erp_execute\n"
    "- 编码/单号类型不确定 → local_product_identify\n"
    "- 不确定用哪个action → 先调 erp_api_search 查文档\n\n"
    "## 编码识别（前置步骤）\n"
    "- 首次遇到裸值编码/单号 → 先 local_product_identify(code=XX) 确认类型\n"
    "- 返回: 编码类型 + 关联参数(ID/编码/名称/SKU列表)\n"
    "- 套件(type=1/2)没有独立库存:\n"
    "  → local_product_identify 会返回子单品列表（outer_id + sku_outer_id）\n"
    "  → 对每个子单品调用 local_stock_query 查库存\n"
    "  → 汇总后告知用户各子单品库存情况\n"
    "- 识别后用返回的精确参数查询，不猜不试\n"
    "- 同一编码在同一对话中只需识别一次\n\n"
    "## ERP多步查询策略\n"
    "- 统计类（如'今天多少单'）：只取total，不翻页，不传status\n"
    "- 分维度统计（如'每个平台多少单'）：先 shop_list 获取店铺，逐个查total\n"
    "- 只在用户需要看明细时才用大 page_size\n"
    "- 复杂问题可跨类别多次查询\n"
    "- 所有数据收集完毕后，用 route_to_chat 汇总回复\n\n"
    "## 名称纠错\n"
    "- 按名称查无结果 → 必须 ask_user 确认名称，禁止直接返回「未找到」\n"
    "- 名称像错别字 → 查询前先确认\n\n"
    "## 时间类型选择\n"
    "- 「今天多少订单」→ time_type=\"created\"（下单时间）\n"
    "- 「今日成交/付款」→ time_type=\"pay_time\"（付款时间）\n"
    "- 「今天发了多少」→ time_type=\"consign_time\"（发货时间）\n"
    "- 不传 time_type 默认按修改时间查，通常不是用户想要的\n"
    "- erp_taobao_query 用 date_type（整数）: 0=修改/1=创建/2=下单/3=发货\n\n"
    "## 高频易混淆场景\n"
    "### 库存查询\n"
    "- 当前库存快照（总量/可售/锁定/预占）→ stock_status(outer_id或sku_outer_id)\n"
    "- 各仓库库存分布 → warehouse_stock(outer_id或sku_outer_id)\n"
    "- 出入库历史流水 → stock_in_out(outer_id=XX, order_type筛选类型)\n"
    "  order_type: 1=采购入库, 2=销售出库, 3=盘盈入库, 4=盘亏出库, 5=调拨入库, 6=调拨出库\n"
    "  不传order_type=返回所有类型混在一起\n"
    "- ⚠ outer_id/sku_outer_id 区分详见参数文档。系统会自动纠正编码类型错误，零结果时自动建议替代参数\n\n"
    "### 商品销量查询\n"
    "- 某商品销量 → erp_product_query(stock_in_out, outer_id=XX, order_type=2, start_date/end_date)\n"
    "- 返回每笔销售出库记录（含数量num字段），销量=所有记录num累加，不是记录条数\n"
    "- sku_outer_id也可以传（系统自动映射到outer_id）\n"
    "- 系统自动翻页拉取全部记录，无需手动设置page_size\n"
    "- ⚠ outstock_query是订单维度，不支持按商品编码筛选！不带订单号按时间查全量会超时\n"
    "  outstock_query适合：查某个订单的出库详情（有order_id/system_id时）\n"
    "  outstock_query不适合：查某商品卖了多少（没有编码筛选参数）\n\n"
    "### 售后查询（跨3个工具）\n"
    "- 默认 → erp_aftersales_query(aftersale_list)\n"
    "- 淘宝/天猫 → erp_taobao_query(refund_list)\n"
    "- 退货入库 → aftersale_query(refund_warehouse, 必须传time_type)\n"
    "- 补款 → aftersale_query(replenish_list)\n"
    "- 维修 → aftersale_query(repair_list)\n"
    "- ⚠ aftersale_list不支持system_id查询，必须用order_id(平台订单号)或work_order_id(工单ID)\n"
    "- 只有system_id时：先 order_list(system_id=XX) 拿到 order_id → 再 aftersale_list(order_id=XX)\n\n"
    "### 出库查询（跨3个工具，按场景选）\n"
    "- 查某订单的出库详情+快递 → erp_trade_query(outstock_query, order_id或system_id)\n"
    "- 查仓库作业状态（待处理/发货中/已发货）→ erp_trade_query(outstock_order_query)\n"
    "- 查某商品的出入库流水 → erp_product_query(stock_in_out, outer_id=XX)\n"
    "- 非销售出入库（手工出入库）→ erp_warehouse_query(other_out_list/other_in_list)\n"
    "- ⚠ outstock_query不带订单号按时间范围查：数据量大会报错\"查询结果数量过多\"或超时\n\n"
    "### 标签查询（同名action不同工具）\n"
    "- 订单标签 → erp_info_query(tag_list)\n"
    "- 商品标签 → erp_product_query(tag_list)\n\n"
    "### 订单状态映射\n"
    "- 未付款→WAIT_BUYER_PAY | 待审核→WAIT_AUDIT | 待发货→WAIT_SEND_GOODS\n"
    "- 已发货→SELLER_SEND_GOODS | 已完成→FINISHED | 已关闭→CLOSED\n"
    "- 多状态逗号分隔。统计类不要传status。「异常订单」含义不明确→ask_user\n\n"
    "### 售后类型值\n"
    "- 退货=2 | 补发=3 | 换货=4 | 仅退款(已发货)=1 | 仅退款(未发货)=5\n\n"
    "### 归档数据\n"
    "- 订单：query_type=1 查归档（同action）\n"
    "- 采购：必须换action（如 purchase_order_list → purchase_order_history），需 start_date + end_date\n\n"
    "### 必填参数陷阱\n"
    "- refund_warehouse: 必须传 time_type\n"
    "- history_cost_price: 必须传 item_id + sku_id\n"
    "- batch_stock_list: 必须传 shop_id\n"
    "- order_log: 只接受 system_ids！先 order_list 拿 system_id\n"
    "- _history action: 必须传 start_date + end_date\n\n"
    "## 查不到时的策略\n"
    "- 编码查询返回0条时，系统会自动用基础编码扩大查询并精确匹配，无需手动重试\n"
    "- 系统会自动建议替代参数（如 outer_id→sku_outer_id），按建议重试即可\n"
    "- 订单查不到 → 检查是否需要 query_type=1（归档）\n"
    "- 采购单查不到 → 换 _history action\n"
    "- 按名称查不到 → ask_user 确认名称\n\n"
    "## 禁止猜测原则\n"
    "- 裸值编码 → 先 local_product_identify，不猜测参数类型\n"
    "- 纯中文 → 可能是买家昵称，ask_user 要编码\n"
    "- 禁止不传参数直接调API返回全量数据\n\n"
) + LOCAL_ROUTING_PROMPT


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
