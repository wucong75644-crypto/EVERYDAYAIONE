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
            "远程API查询ERP基础信息：仓库、店铺、标签、客户、分销商。",
            BASIC_REGISTRY,
        ),
        # 2. 商品查询
        _build_query_tool(
            "erp_product_query",
            "远程API查询ERP商品/SKU/库存/标签/分类/品牌信息。"
            "适合本地工具不支持的字段或需要实时数据。",
            PRODUCT_REGISTRY,
        ),
        # 3. 交易查询
        _build_query_tool(
            "erp_trade_query",
            "远程API查询ERP订单/出库/物流/波次/唯一码信息。"
            "适合本地工具不支持的操作或需要实时数据。",
            TRADE_REGISTRY,
        ),
        # 4. 售后查询
        _build_query_tool(
            "erp_aftersales_query",
            "远程API查询ERP售后工单/退货/维修单/补款/日志。"
            "适合本地工具不支持的操作。",
            AFTERSALES_REGISTRY,
        ),
        # 5. 仓储查询
        _build_query_tool(
            "erp_warehouse_query",
            "远程API查询ERP调拨/入出库/盘点/下架/货位/加工单信息。"
            "仓储操作无本地工具，必须使用此远程API。",
            WAREHOUSE_REGISTRY,
        ),
        # 6. 采购查询
        _build_query_tool(
            "erp_purchase_query",
            "远程API查询ERP供应商/采购单/收货单/采退单/上架单/采购建议。"
            "适合本地工具不支持的操作。",
            PURCHASE_REGISTRY,
        ),
        # 7. 淘宝奇门查询（通过淘宝网关）
        _build_query_tool(
            "erp_taobao_query",
            (
                "远程API查询淘宝/天猫平台的订单和售后单（通过奇门接口）。"
                "返回平台原始数据 {total, trades/workOrders[]}。"
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


# ERP 精简提示词（工具使用协议，不规定工具选择）
ERP_ROUTING_PROMPT = (
    "## 工具能力说明\n"
    "- local_* 工具：查本地数据库，毫秒级响应，数据来自每分钟自动同步\n"
    "- erp_* 工具：查远程ERP API，适合本地工具不支持的操作或需要实时数据\n"
    "- code_execute：代码沙盒，适合其他工具无法完成的复杂计算/文件生成\n"
    "- 根据工具描述自行判断最合适的工具\n\n"
    "## 数据新鲜度\n"
    "- 如果 local 工具返回结果包含 ⚠ 同步警告，"
    "先 trigger_erp_sync 触发同步再重查，或改用 erp_* 远程查询\n"
    "- 查不到 + 同步正常 → 数据确实不存在，告知用户\n"
    "- 商品/库存查不到 → 不用触发同步（identify 自动回退 API）\n\n"
    "## ERP 远程工具使用协议\n"
    "1. 两步查询：先传 action 拿参数文档 → 再传 params 执行\n"
    "2. page/page_size 在 tool 级别传，不放 params 里\n\n"
    "## 编码识别\n"
    "- 裸值编码/单号 → 先 local_product_identify(code=XX) 确认类型\n"
    "- 套件(type=1/2)无独立库存 → 查子单品逐个查\n"
    "- 同一编码每会话只识别一次\n\n"
    "## 时间类型（ERP远程工具专用，必须严格匹配）\n"
    "- 含「付款/已付/支付」→ time_type=\"pay_time\"（付款时间）\n"
    "- 含「下单/创建/新增」→ time_type=\"created\"（创建时间）\n"
    "- 含「发货/已发/物流」→ time_type=\"consign_time\"（发货时间）\n"
    "- 无明确时间类型关键词 → time_type=\"created\"（默认创建时间）\n"
    "- 不传 time_type 默认是 modified（通常不是用户想要的，务必显式传）\n"
    "- erp_taobao_query 用 date_type（整数）: 0=修改/1=创建/2=下单/3=发货\n\n"
    "## 销量计算\n"
    "- 销量 = sum(每条记录的 num 字段)，不是记录条数\n\n"
    "## 售后跨工具\n"
    "- 默认 → aftersale_list\n"
    "- 淘宝/天猫 → erp_taobao_query(refund_list)\n"
    "- 退仓 → refund_warehouse（必传 time_type）\n\n"
    "## 归档数据\n"
    "- 老订单查不到 → query_type=1（归档）\n"
    "- 老采购查不到 → 换 _history action + 必传 start_date/end_date\n\n"
    "## 中继键\n"
    "- local_doc_query 返回的 sid/order_no/outer_id → 直接用于 API 跨查\n"
    "- 物流轨迹 → express_query(system_id=sid)\n"
    "- 操作日志 → order_log(system_ids=sid)\n\n"
    "## 规则\n"
    "- 禁止猜测参数类型，不确定时 ask_user\n"
    "- 名称搜索无结果 → 必须 ask_user 确认，禁止返回「未找到」\n"
    "- 编码查询返回0条时，系统会自动用基础编码扩大查询并精确匹配，无需手动重试\n"
    "- 数据采集完毕 → route_to_chat 汇总回复\n\n"
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
