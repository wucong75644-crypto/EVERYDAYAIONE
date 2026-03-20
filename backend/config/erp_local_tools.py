"""
ERP 本地查询工具定义（8个工具）

本地工具直接查询 PostgreSQL，毫秒级响应，优先于 API 工具使用。
注册到 Agent Loop Phase2 工具循环。

设计文档: docs/document/TECH_ERP数据本地索引系统.md §6
"""

from typing import Any, Dict, List, Set


ERP_LOCAL_TOOLS: Set[str] = {
    "local_purchase_query",
    "local_aftersale_query",
    "local_order_query",
    "local_product_stats",
    "local_product_flow",
    "local_stock_query",
    "local_product_identify",
    "local_platform_map_query",
    "local_doc_query",
    "local_global_stats",
    "trigger_erp_sync",
}


def _tool(
    name: str, desc: str,
    props: Dict[str, Any],
    required: list[str],
) -> Dict[str, Any]:
    """构建紧凑工具定义"""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": {
                "type": "object",
                "properties": props,
                "required": required,
            },
        },
    }


def _str(desc: str) -> Dict[str, str]:
    """字符串参数"""
    return {"type": "string", "description": desc}


def _int(desc: str) -> Dict[str, Any]:
    """整数参数"""
    return {"type": "integer", "description": desc}


def _bool(desc: str) -> Dict[str, Any]:
    """布尔参数"""
    return {"type": "boolean", "description": desc}


def _enum(desc: str, values: list[str]) -> Dict[str, Any]:
    """枚举参数"""
    return {"type": "string", "enum": values, "description": desc}


def build_local_tools() -> List[Dict[str, Any]]:
    """构建11个本地查询工具定义（8原有 + 3新增）"""
    return [
        # 1. 采购到货查询（含采退）
        _tool(
            "local_purchase_query",
            "按商品编码查采购到货进度（含采退单）。本地查询，毫秒级响应。",
            {
                "product_code": _str("商品编码（主商家编码或SKU编码）"),
                "status": _enum(
                    "采购单状态过滤",
                    ["GOODS_NOT_ARRIVED", "GOODS_PART_ARRIVED", "FINISHED"],
                ),
                "include_return": _bool("是否包含采退单，默认true"),
                "days": _int("查询最近N天，默认30"),
            },
            ["product_code"],
        ),
        # 2. 售后查询
        _tool(
            "local_aftersale_query",
            "按商品编码查售后情况（退货/退款/换货/补发等）。本地查询。",
            {
                "product_code": _str("商品编码"),
                "aftersale_type": _enum(
                    "售后类型(0=其他/1=已发货仅退款/2=退货/3=补发"
                    "/4=换货/5=未发货仅退款/7=拒收退货/8=档口退货/9=维修)",
                    ["0", "1", "2", "3", "4", "5", "7", "8", "9"],
                ),
                "days": _int("查询最近N天，默认30"),
            },
            ["product_code"],
        ),
        # 3. 销售订单查询
        _tool(
            "local_order_query",
            "按商品编码查销售订单（支持按平台/店铺/状态过滤）。本地查询。",
            {
                "product_code": _str("商品编码（主商家编码或SKU编码）"),
                "shop_name": _str("店铺名称过滤"),
                "platform": _enum(
                    "平台过滤",
                    ["tb", "jd", "pdd", "dy", "xhs", "1688"],
                ),
                "status": _enum(
                    "订单状态过滤",
                    ["WAIT_AUDIT", "WAIT_SEND_GOODS",
                     "SELLER_SEND_GOODS", "FINISHED", "CLOSED"],
                ),
                "days": _int("查询最近N天，默认30"),
            },
            ["product_code"],
        ),
        # 4. 统计报表查询
        _tool(
            "local_product_stats",
            "按商品编码查统计数据（月度/周度/日度销售、采购、售后报表）。"
            "查聚合表，秒级响应。",
            {
                "product_code": _str("商品编码"),
                "period": _enum("统计周期", ["day", "week", "month"]),
                "start_date": _str("起始日期(YYYY-MM-DD)，默认当月1号"),
                "end_date": _str("结束日期(YYYY-MM-DD)，默认今天"),
            },
            ["product_code"],
        ),
        # 5. 全链路流转
        _tool(
            "local_product_flow",
            "按商品编码查完整供应链流转"
            "（采购→收货→上架→销售→售后→采退）。本地查询。",
            {
                "product_code": _str("商品编码"),
                "days": _int("查询最近N天，默认30"),
            },
            ["product_code"],
        ),
        # 6. 库存查询
        _tool(
            "local_stock_query",
            "按商品编码查库存状态（可售/总库存/锁定/在途）。本地查询。",
            {
                "product_code": _str("商品编码（主商家编码或SKU编码）"),
                "stock_status": _enum(
                    "库存状态(1=正常/2=警戒/3=无货/4=超卖/6=有货)",
                    ["1", "2", "3", "4", "6"],
                ),
                "low_stock": _bool("仅显示库存预警，默认false"),
            },
            ["product_code"],
        ),
        # 7. 本地编码识别
        _tool(
            "local_product_identify",
            "本地编码识别（替代API调用）。支持三种模式：编码精确匹配、"
            "商品名模糊搜索、规格名模糊搜索。毫秒级响应。"
            "code/name/spec 至少传一个。",
            {
                "code": _str("商品编码/SKU编码/条码（精确匹配）"),
                "name": _str("商品名称关键词（模糊搜索）"),
                "spec": _str("规格名称关键词（模糊搜索，如'红色''120g'）"),
            },
            [],
        ),
        # 8. 平台映射查询
        _tool(
            "local_platform_map_query",
            "查ERP编码↔平台商品映射（下架检查）。"
            "product_code 和 num_iid 至少传一个。",
            {
                "product_code": _str(
                    "ERP商品编码（查此编码在哪些平台有售）"
                ),
                "num_iid": _str("平台商品ID（反查对应ERP编码）"),
                "user_id": _str("店铺ID过滤（只查指定店铺）"),
            },
            [],
        ),
        # 9. 多维度单据查询
        _tool(
            "local_doc_query",
            "多维度单据查询。支持按订单号/快递号/采购单号/供应商/店铺查询单据，"
            "返回完整信息含所有关联ID（sid/order_no/express_no/outer_id），"
            "可直接用于跨工具查询（如拿 sid 查物流）。毫秒级响应。",
            {
                "product_code": _str("商品编码（主编码或SKU编码）"),
                "order_no": _str("平台订单号（淘宝/京东/拼多多等平台单号）"),
                "doc_code": _str("单据编号（采购单号/收货单号/上架单号/采退单号）"),
                "express_no": _str("快递单号"),
                "supplier_name": _str("供应商名称（模糊搜索）"),
                "shop_name": _str("店铺名称（模糊搜索）"),
                "doc_type": _enum(
                    "单据类型过滤",
                    ["order", "purchase", "receipt", "shelf",
                     "aftersale", "purchase_return"],
                ),
                "status": _str("状态过滤"),
                "days": _int("查询最近N天，默认30"),
            },
            [],
        ),
        # 10. 全局统计/排名
        _tool(
            "local_global_stats",
            "全局统计查询（无需商品编码）。支持按时间/类型/店铺/供应商"
            "统计订单数/销售额/退货数等，支持排名。本地查询。",
            {
                "doc_type": _enum(
                    "统计类型",
                    ["order", "purchase", "aftersale", "receipt",
                     "shelf", "purchase_return"],
                ),
                "date": _str("统计日期(YYYY-MM-DD)，默认今天"),
                "period": _enum("统计周期", ["day", "week", "month"]),
                "shop_name": _str("按店铺过滤（模糊匹配）"),
                "platform": _str("按平台过滤(tb/jd/pdd/dy/xhs)"),
                "supplier_name": _str("按供应商过滤（模糊匹配）"),
                "warehouse_name": _str("按仓库过滤"),
                "rank_by": _enum(
                    "排名维度（返回TOP10）",
                    ["count", "quantity", "amount"],
                ),
                "group_by": _enum(
                    "分组维度",
                    ["product", "shop", "platform", "supplier", "warehouse"],
                ),
            },
            ["doc_type"],
        ),
        # 11. 手动触发同步
        _tool(
            "trigger_erp_sync",
            "手动触发ERP数据同步。仅在本地查询无数据且同步状态异常时使用。"
            "商品/库存查不到不要用此工具（local_product_identify 会自动兜底）。"
            "同步完成后重新调用原查询工具。",
            {
                "sync_type": _enum(
                    "同步类型",
                    ["order", "purchase", "receipt", "shelf",
                     "aftersale", "purchase_return",
                     "product", "stock", "supplier", "platform_map"],
                ),
            },
            ["sync_type"],
        ),
    ]


# ── Schema（参数验证用） ─────────────────────────────────

LOCAL_TOOL_SCHEMAS: Dict[str, Dict[str, Any]] = {
    "local_purchase_query": {
        "required": ["product_code"],
        "properties": {"product_code": {"type": "string"}},
    },
    "local_aftersale_query": {
        "required": ["product_code"],
        "properties": {"product_code": {"type": "string"}},
    },
    "local_order_query": {
        "required": ["product_code"],
        "properties": {"product_code": {"type": "string"}},
    },
    "local_product_stats": {
        "required": ["product_code"],
        "properties": {"product_code": {"type": "string"}},
    },
    "local_product_flow": {
        "required": ["product_code"],
        "properties": {"product_code": {"type": "string"}},
    },
    "local_stock_query": {
        "required": ["product_code"],
        "properties": {"product_code": {"type": "string"}},
    },
    "local_product_identify": {
        "required": [],
        "properties": {
            "code": {"type": "string"},
            "name": {"type": "string"},
            "spec": {"type": "string"},
        },
    },
    "local_platform_map_query": {
        "required": [],
        "properties": {
            "product_code": {"type": "string"},
            "num_iid": {"type": "string"},
        },
    },
    "local_doc_query": {
        "required": [],
        "properties": {
            "product_code": {"type": "string"},
            "order_no": {"type": "string"},
            "doc_code": {"type": "string"},
            "express_no": {"type": "string"},
            "supplier_name": {"type": "string"},
            "shop_name": {"type": "string"},
        },
    },
    "local_global_stats": {
        "required": ["doc_type"],
        "properties": {"doc_type": {"type": "string"}},
    },
    "trigger_erp_sync": {
        "required": ["sync_type"],
        "properties": {"sync_type": {"type": "string"}},
    },
}


# ── 本地工具路由提示词 ──────────────────────────────────

LOCAL_ROUTING_PROMPT = (
    "## 本地查询工具\n"
    "本地工具直接查数据库，毫秒级响应。\n\n"

    "### 输入识别 → 工具选择\n"
    "- 商品编码/条码/商品名 → local_product_identify（编码识别入口）\n"
    "- 商品编码 + 查库存 → local_stock_query\n"
    "- 商品编码 + 查采购/订单/售后/流转 → local_doc_query(product_code=XX, doc_type=YY)\n"
    "- 商品编码 + 查统计趋势 → local_product_stats\n"
    "- 商品编码 + 查全链路 → local_product_flow\n"
    "- 商品编码 + 查平台映射 → local_platform_map_query\n"
    "- 平台订单号 → local_doc_query(order_no=XX)\n"
    "- 快递单号 → local_doc_query(express_no=XX)\n"
    "- 采购/收货单号 → local_doc_query(doc_code=XX)\n"
    "- 供应商名 + 查采购 → local_doc_query(supplier_name=XX, doc_type=purchase)\n"
    "- 店铺名 + 查订单/售后 → local_doc_query(shop_name=XX, doc_type=YY)\n"
    "- 全局统计(今天多少单/退货排名/各平台对比) → local_global_stats\n\n"

    "### 查询优先级\n"
    "1. 所有查询先走本地工具（毫秒级）\n"
    "2. 商品/库存查不到 → local_product_identify 自动 API 兜底，无需额外操作\n"
    "3. 单据查不到 + 同步状态异常(返回中有⚠标记) → trigger_erp_sync → 重查\n"
    "4. 单据查不到 + 同步状态正常 → 数据确实不存在，直接告知用户\n"
    "5. 超出本地能力（物流轨迹/操作日志/买家查询/仓库操作/写操作）\n"
    "   → 用本地返回的 sid/order_no/outer_id 精准调 API\n\n"

    "### 中转钥匙用法\n"
    "local_doc_query 返回中包含 sid(system_id)、order_no、express_no、outer_id 等字段，\n"
    "可直接用于跨工具查询：\n"
    "- 查物流轨迹 → 用返回的 sid 调 express_query(system_id=sid)\n"
    "- 查操作日志 → 用返回的 sid 调 order_log(system_ids=sid)\n"
    "- 查退货入库 → 用返回的 doc_id(工单号) 调 refund_warehouse(work_order_ids=XX)\n"
    "- 查商品详情 → 用返回的 outer_id 调 local_product_identify\n\n"

    "### trigger_erp_sync 使用规则\n"
    "- 仅在「单据查不到 + 同步状态异常」时触发\n"
    "- 商品/库存查不到 → 不要用此工具（identify 自动兜底）\n"
    "- 同步状态正常(无⚠标记) → 不触发，数据就是不存在\n"
    "- 用户说「刚录入」「刚下单」→ 可触发\n"
    "- sync_type: 订单→order, 采购→purchase, 售后→aftersale, "
    "收货→receipt, 上架→shelf, 采退→purchase_return\n\n"

    "### 仍需 API 的场景\n"
    "- 物流轨迹详情 → express_query(system_id)\n"
    "- 操作日志 → order_log(system_ids) / aftersale_log(work_order_id)\n"
    "- 退货入库详情 → refund_warehouse(work_order_ids)\n"
    "- 买家维度查询 → order_list(buyer=XX)\n"
    "- 各仓库存分布 → warehouse_stock(outer_id)\n"
    "- 仓库操作(调拨/盘点/加工) → erp_warehouse_query\n"
    "- 写操作 → erp_execute\n"
    "- 波次/唯一码 → erp_trade_query\n"
)
