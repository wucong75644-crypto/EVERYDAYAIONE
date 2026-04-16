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
from services.kuaimai.registry.base import ApiEntry

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


def _format_action_desc(name: str, entry: ApiEntry) -> str:
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
                "操作参数（key 必须用下划线格式如 time_type/start_date，"
                "禁止驼峰如 timeType/startTime）。"
                "不确定参数时只传 action 可获取完整参数文档。"
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


# ERP 精简提示词 — 统一查询引擎版本
# 设计文档: docs/document/TECH_统一查询引擎FilterDSL.md §8
ERP_ROUTING_PROMPT = (
    "## 工具选择规则\n\n"
    "### 层级：local > erp > fetch_all_pages > code_execute\n"
    "- 禁止跳过 local 工具直接用 erp 远程 API\n"
    "- code_execute 是纯计算沙盒，不能查数据\n\n"
    "### 核心工具\n"
    "- **local_data**：单据数据统一查询（订单/采购/售后/收货/上架/采退）。\n"
    "  支持 summary（聚合统计）/ detail（明细列表）/ export（导出文件）三种模式。\n"
    "  用 filters 数组指定任意字段组合过滤，不限参数组合。\n"
    "- **local_compare_stats**：时间维度对比（同比/环比/自定义），禁止调 local_data 两次自行对比\n"
    "- **local_product_identify**：编码识别，模糊名称时先调它确认精确编码\n"
    "- **local_stock_query**：库存查询（不同表，不走 local_data）\n"
    "- **local_product_stats**：商品维度统计报表（预聚合表，按编码+时间段）\n"
    "- **local_platform_map_query**：编码↔平台映射\n"
    "- **local_shop_list / local_warehouse_list**：店铺/仓库列表\n"
    "- **trigger_erp_sync**：手动触发数据同步\n"
    "- **fetch_all_pages**：本地没有的数据（如物流轨迹）全量翻页拉取\n"
    "- **erp_* 远程工具**：物流轨迹、操作日志、仓库操作、写入操作\n"
    "- **code_execute**：纯计算沙盒，读 staging 文件做计算/导出 Excel\n\n"
    "### 常见场景\n"
    "- 今天/本周/本月多少单 → local_data(doc_type=order, mode=summary, filters=[时间条件])\n"
    "- 已发货/未发货订单 → local_data(filters=[{field:order_status, op:eq, value:SELLER_SEND_GOODS}])\n"
    "- 按店铺/平台统计 → local_data(mode=summary, group_by=[shop_name])\n"
    "- 按商品排名 → local_data(mode=summary, group_by=[outer_id])\n"
    "- 导出 Excel → local_data(mode=export, fields=[...]) → code_execute 生成 Excel\n"
    "- 查某订单详情 → local_data(mode=detail, filters=[{field:order_no, op:eq, value:xxx}])\n"
    "- 对比/同比/环比 → local_compare_stats\n"
    "- 某商品流转 → local_data 按 6 种 doc_type 各调一次 summary\n"
    "- 某商品编码的采购/售后/订单 → local_data(filters=[{field:outer_id, op:eq, value:编码}])\n\n"
    "### 时间规范\n"
    "- 日期用 ISO: 2026-04-14 00:00:00\n"
    "- 含「付款」→ time_type=pay_time / filters 中用 pay_time 字段\n"
    "- 含「发货」→ time_type=consign_time / filters 中用 consign_time 字段\n"
    "- 默认 doc_created_at\n"
    "- 工具返回的时间块（含中文星期）必须逐字复述\n\n"
    "### 降级策略\n"
    "- local 工具返回错误 → 改用 erp 远程工具重试\n"
    "- 连续 2 次空结果 → ask_user 确认条件\n\n"
    "### 参数充分度判断（决策框架）\n"
    "执行查询前，判断关键参数是否充分：\n"
    "- **充分**（用户明确给出）→ 直接查\n"
    "- **可推断且无歧义**（只有一个合理值）→ 直接查，结果中说明假设\n"
    "- **有歧义**（多个合理值）→ 调 ask_user，告知用户可选的查询条件\n\n"
    "追问时告知用户可选条件，帮助用户一次说清楚：\n"
    "- 统计类（销量/销售额/订单数）：时间范围、店铺、平台、商品、是否排除异常\n"
    "- 商品类（库存/价格/编码）：商品名称或编码、仓库\n"
    "- 单据类（订单/采购/售后）：单号、店铺、时间范围、状态\n\n"
    "补充规则：\n"
    "- 多条匹配 → ask_user 列出候选让用户选择\n"
    "- 查询结果中 is_exception > 0 → 主动告知异常分布并询问是否排除\n"
    "- 写操作（取消/修改/标记）→ 必须 ask_user 确认对象和影响\n\n"
    "### ERP 远程工具协议\n"
    "1. 两步查询：先传 action 拿参数文档 → 再传 params 执行\n"
    "2. page/page_size 在 tool 级别传，不放 params 里\n\n"
    "### 编码识别\n"
    "- 裸值编码/单号 → 先 local_product_identify(code=XX) 确认类型\n"
    "- 套件无独立库存 → 查子单品逐个查\n"
    "- 同一编码每会话只识别一次\n\n"
    "### 规则\n"
    "- 禁止猜测参数值，有歧义时 ask_user\n"
    "- 参数明确时直接查询，禁止试探性查询\n"
    "- 名称搜索无结果 → ask_user 确认\n"
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


def build_fetch_all_pages_tool() -> Dict[str, Any]:
    """构建 fetch_all_pages 工具定义（独立翻页工具）"""
    return {
        "type": "function",
        "function": {
            "name": "fetch_all_pages",
            "description": (
                "全量翻页工具。包装任意 erp_* 远程查询工具，自动翻页拉取全部数据。"
                "适合：导出Excel、全量数据分析、跨数据源关联等需要完整数据的场景。"
                "结果自动存为 staging 文件，返回文件路径。"
                "配合 code_execute 使用：先用本工具拿全量数据，"
                "再用 code_execute 的 read_file 读取并计算/导出。"
                "⚠ 翻页耗时较长（100条/页，每页约1秒），"
                "请根据预估数据量合理设置 max_pages。"
                "⚠ 使用前需先通过 erp_* 工具的两步协议确认参数格式。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "tool": {
                        "type": "string",
                        "description": (
                            "要翻页的查询工具名"
                            "（如 erp_trade_query、erp_product_query）"
                        ),
                    },
                    "action": {
                        "type": "string",
                        "description": "操作名（如 order_list、stock_status）",
                    },
                    "params": {
                        "type": "object",
                        "description": (
                            "查询参数（与直接调用该工具时的 params 相同）"
                        ),
                    },
                    "page_size": {
                        "type": "integer",
                        "description": (
                            "每页条数（默认100，最小20，快麦API限制）"
                        ),
                    },
                    "max_pages": {
                        "type": "integer",
                        "description": (
                            "最大翻页数（默认200）。"
                            "预估数据量少时设小可加速，"
                            "如预估500条设 max_pages=5"
                        ),
                    },
                },
                "required": ["tool", "action"],
            },
        },
    }
