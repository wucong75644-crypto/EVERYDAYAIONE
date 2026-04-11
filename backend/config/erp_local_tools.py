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
    "local_compare_stats",  # 时间事实层 — 同比/环比 (PR1 §6.2.3)
    "local_shop_list",
    "local_warehouse_list",
    "local_db_export",
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
            "按商品编码查采购到货进度（含采退单）。毫秒级响应。"
            "返回各采购单明细（单号/供应商/状态/到货数量），默认最近30天。"
            "⚠ 需精确编码，模糊时先 local_product_identify。"
            "相关工具：更多采购操作（上架/归档）用 erp_purchase_query；"
            "查该商品库存用 local_stock_query。",
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
            "按商品编码查售后情况（退货/退款/换货/补发等）。毫秒级响应。"
            "返回按类型汇总 + 最近5条工单明细，默认最近30天。"
            "⚠ 需精确编码，模糊时先 local_product_identify。"
            "相关工具：淘宝/天猫退款用 erp_taobao_query(refund_list)；"
            "查该商品订单用 local_order_query。",
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
            "按商品编码查销售订单（支持按平台/店铺/状态过滤）。毫秒级响应。"
            "返回平台汇总 + 最近5条订单明细（单号/金额/状态），默认最近30天。"
            "⚠ 需精确编码，模糊时先 local_product_identify。"
            "相关工具：需要收货地址/物流轨迹用 erp_trade_query；"
            "按店铺维度统计用 local_global_stats(group_by='shop')。",
            {
                "product_code": _str("商品编码（主商家编码或SKU编码）"),
                "shop_name": _str("店铺名称过滤"),
                "platform": _enum(
                    "平台过滤(tb=淘宝,jd=京东,pdd=拼多多,fxg=抖音,kuaishou=快手,xhs=小红书)",
                    ["tb", "jd", "pdd", "fxg", "kuaishou", "xhs", "1688"],
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
            "按商品编码查统计报表（日/周/月维度）。毫秒级响应。"
            "返回聚合指标：订单数/销量/销售额、采购数/采购额、"
            "售后数/退货数。支持日期范围对比。"
            "⚠ 需精确编码，模糊时先 local_product_identify。"
            "相关工具：全局统计（不按商品）用 local_global_stats；"
            "查供应链全流程用 local_product_flow。",
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
            "（采购→收货→上架→销售→售后→采退）。毫秒级响应。"
            "返回各环节汇总指标（笔数/数量/金额）+ 售后率。"
            "⚠ 需精确编码，模糊时先 local_product_identify。"
            "相关工具：各环节详情用 local_purchase_query/local_order_query/local_aftersale_query。",
            {
                "product_code": _str("商品编码"),
                "days": _int("查询最近N天，默认30"),
            },
            ["product_code"],
        ),
        # 6. 库存查询
        _tool(
            "local_stock_query",
            "按商品精确编码查库存（可售/总库存/锁定/在途/各仓分布）。毫秒级响应。"
            "⚠ 需要精确编码，用户给模糊名称/简称时先调 local_product_identify 确认。"
            "相关工具：需要实时数据或本地查不到时改用 erp_product_query(stock_status)；"
            "查采购到货进度用 local_purchase_query。",
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
            "编码识别与商品搜索。毫秒级响应。"
            "三种模式：编码精确匹配（返回1条）、"
            "商品名模糊搜索（返回最多20条）、"
            "规格名模糊搜索（返回最多20条）。"
            "返回编码/名称/规格/条码/供应商。code/name/spec 至少传一个。"
            "相关工具：识别到编码后可用 local_stock_query 查库存、local_order_query 查订单、"
            "local_product_stats 查统计、local_product_flow 查供应链流转。",
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
            "查ERP编码↔平台商品映射（上架/下架检查）。毫秒级响应。"
            "返回编码在哪些平台有售，或平台商品ID反查ERP编码。"
            "product_code 和 num_iid 至少传一个。"
            "相关工具：编码模糊时先 local_product_identify；"
            "查库存用 local_stock_query。",
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
            "多维度单据查询（无需商品编码）。毫秒级响应。"
            "支持按订单号/快递号/采购单号/供应商/店铺查询，"
            "返回单据明细（最多20条）含所有关联ID"
            "（sid/order_no/express_no/outer_id），"
            "可直接用于跨工具查询（如拿 sid 查物流）。"
            "相关工具：拿到编码后可用 local_stock_query/local_order_query 深入查询；"
            "需要物流轨迹用 erp_trade_query(express_query)。",
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
            "全局统计查询（无需商品编码）。毫秒级响应。"
            "直接返回聚合结果：总单数、总数量、总金额。"
            "支持按店铺/平台/供应商/仓库分组，支持排名（TOP10）。"
            "支持精确到分钟/秒的时间范围查询（用start_time+end_time），"
            "如「到下午4点」「上午10点到12点」「某天某时段」等。"
            "适合：今天多少单、各店铺销量排名、平台对比、"
            "退货统计、销售额趋势、同时段对比等全局维度的统计需求。"
            "⚠ 含「付款/已付/支付/成交」→ time_type=\"pay_time\"；"
            "含「发货/已发/物流」→ time_type=\"consign_time\"；"
            "默认按下单时间。"
            "相关工具：按商品维度统计用 local_product_stats；"
            "店铺名称不确定时先 local_shop_list 获取列表。",
            {
                "doc_type": _enum(
                    "统计类型",
                    ["order", "purchase", "aftersale", "receipt",
                     "shelf", "purchase_return"],
                ),
                "date": _str("统计日期(YYYY-MM-DD)，默认今天。"
                             "如需精确到小时，改用start_time+end_time"),
                "period": _enum("统计周期", ["day", "week", "month"]),
                "time_type": _enum(
                    "时间类型（仅order类型有效）。"
                    "含「付款/成交」用pay_time，含「发货」用consign_time，"
                    "默认doc_created_at（下单时间）",
                    ["doc_created_at", "pay_time", "consign_time"],
                ),
                "start_time": _str(
                    "精确起始时间(YYYY-MM-DD HH:MM 或 YYYY-MM-DD HH:MM:SS)，"
                    "如'2026-04-06 00:00'。"
                    "与end_time配合使用，优先级高于date/period"),
                "end_time": _str(
                    "精确结束时间(YYYY-MM-DD HH:MM 或 YYYY-MM-DD HH:MM:SS)，"
                    "如'2026-04-06 16:00'。"
                    "与start_time配合使用，优先级高于date/period"),
                "shop_name": _str("按店铺过滤（模糊匹配）"),
                "platform": _str("按平台过滤(tb=淘宝/jd=京东/pdd=拼多多/fxg=抖音/kuaishou=快手/xhs=小红书/1688)"),
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
        # 10b. 同比/环比对比统计 (时间事实层 §6.2.3)
        _tool(
            "local_compare_stats",
            "时间维度对比统计（同比/环比/任意区间对比）。毫秒级响应。"
            "由后端确定地计算对比基线（含中文星期/相对时间标签），"
            "返回结构化双时间块 + 数据对比。"
            "⚠ 涉及「对比/同比/环比/比上周/比上月/比去年」的查询必须用本工具，"
            "禁止调用 local_global_stats 两次再让模型口述对比。"
            "适合：今天 vs 昨天、本周 vs 上周、本月 vs 上月同期、订单同比去年同期等。",
            {
                "doc_type": _enum(
                    "统计类型",
                    ["order", "purchase", "aftersale", "receipt",
                     "shelf", "purchase_return"],
                ),
                "compare_kind": _enum(
                    "对比模式：wow=环比上周同期；mom=环比上月同期；"
                    "yoy=同比去年同期；spring_aligned=春节对齐同比（电商专用）；"
                    "custom=自定义两个区间",
                    ["wow", "mom", "yoy", "spring_aligned", "custom"],
                ),
                "current_period": _enum(
                    "当前期：today=今天；yesterday=昨天；this_week=本周；"
                    "this_month=本月；last_n_days=最近 N 天（配合 current_n）；"
                    "custom=自定义（配合 current_start/current_end）",
                    ["today", "yesterday", "this_week", "this_month",
                     "last_n_days", "custom"],
                ),
                "current_n": _int(
                    "current_period=last_n_days 时使用，如 7 表示最近 7 天",
                ),
                "current_start": _str(
                    "current_period=custom 时使用，"
                    "格式 YYYY-MM-DD HH:MM:SS 或 YYYY-MM-DD",
                ),
                "current_end": _str("同上"),
                "baseline_start": _str(
                    "compare_kind=custom 时使用，自定义基线起始时间",
                ),
                "baseline_end": _str("同上"),
                "time_type": _enum(
                    "时间类型（仅 order 类型有效）。"
                    "默认 doc_created_at（下单时间）",
                    ["doc_created_at", "pay_time", "consign_time"],
                ),
                "shop_name": _str("按店铺过滤（模糊匹配）"),
                "platform": _str(
                    "按平台过滤(tb=淘宝/jd=京东/pdd=拼多多/fxg=抖音/"
                    "kuaishou=快手/xhs=小红书/1688)",
                ),
                "supplier_name": _str("按供应商过滤（模糊匹配）"),
                "warehouse_name": _str("按仓库过滤"),
            },
            ["doc_type", "compare_kind", "current_period"],
        ),
        # 11. 店铺列表
        _tool(
            "local_shop_list",
            "查询店铺列表（含所有店铺，包括新开未出单的）。毫秒级响应。"
            "返回店铺名称/平台/状态/ID，按平台分组显示。"
            "支持按平台过滤（如只看拼多多店铺）。"
            "适合：有哪些店铺、拼多多店铺列表、各平台店铺等查询。"
            "相关工具：拿到店铺名后可用 local_global_stats(shop_name=...) 统计该店铺数据。",
            {
                "platform": _enum(
                    "按平台过滤(tb=淘宝,jd=京东,pdd=拼多多,fxg=抖音,kuaishou=快手,xhs=小红书)",
                    ["tb", "jd", "pdd", "fxg", "kuaishou", "xhs", "1688"],
                ),
            },
            [],
        ),
        # 12. 仓库列表
        _tool(
            "local_warehouse_list",
            "查询仓库列表（实体仓+虚拟仓）。毫秒级响应。"
            "返回仓库名称/编码/类型/状态/地址。"
            "适合：有哪些仓库、仓库地址、仓库编码等查询。"
            "相关工具：拿到仓库名后可用 local_stock_query 查库存分布。",
            {
                "is_virtual": _bool("是否只查虚拟仓（true=只看虚拟仓, false=只看实体仓, 不传=全部）"),
            },
            [],
        ),
        # 13. 本地数据库导出（两步协议 + staging 文件）
        _tool(
            "local_db_export",
            "从本地数据库导出明细数据到 staging 文件（两步协议）。毫秒级响应。"
            "Step1: 只传 doc_type（不传 columns）→ 返回可导出字段文档。"
            "Step2: 传 doc_type + columns → 按字段导出到 staging，配合 code_execute 生成 Excel。"
            "⚠ 本地有的数据优先用本工具（毫秒级），"
            "本地没有的数据（如物流轨迹）才用 fetch_all_pages（远程API）。",
            {
                "doc_type": _enum(
                    "数据类型",
                    ["order", "purchase", "aftersale", "receipt",
                     "shelf", "purchase_return"],
                ),
                "columns": _str(
                    "导出字段（逗号分隔）。不传=返回字段文档（Step1），"
                    "传入=按字段导出（Step2）。如: order_no,shop_name,amount,pay_time"
                ),
                "days": _int("导出最近N天数据（默认1，即今天）"),
                "time_type": _enum(
                    "时间类型（仅order有效）。"
                    "含「付款/成交」用pay_time，含「发货」用consign_time，"
                    "默认doc_created_at（下单时间）",
                    ["doc_created_at", "pay_time", "consign_time"],
                ),
                "shop_name": _str("按店铺过滤（模糊匹配）"),
                "platform": _str("按平台过滤(tb=淘宝/jd=京东/pdd=拼多多/fxg=抖音/kuaishou=快手/xhs=小红书/1688)"),
                "product_code": _str("按商品编码过滤"),
                "status": _str("按状态过滤"),
                "max_rows": _int("最大导出行数（默认5000，上限10000）"),
            },
            ["doc_type"],
        ),
        # 14. 手动触发同步
        _tool(
            "trigger_erp_sync",
            "手动触发ERP数据同步。"
            "仅在 local 工具返回 ⚠ 同步警告或查不到预期数据时使用。"
            "商品/库存查不到不要用此工具（local_product_identify 会自动兜底）。"
            "同步完成后重新调用原查询工具。"
            "相关工具：同步后用原查询工具（如 local_order_query/local_stock_query）验证数据。",
            {
                "sync_type": _enum(
                    "同步类型",
                    ["order", "purchase", "receipt", "shelf",
                     "aftersale", "purchase_return",
                     "product", "stock", "supplier", "platform_map",
                     "shop", "warehouse", "tag", "category",
                     "logistics_company",
                     "allocate", "allocate_in", "allocate_out",
                     "other_in", "other_out",
                     "inventory_sheet", "unshelve", "process_order",
                     "section_record", "goods_section"],
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
    "local_compare_stats": {
        "required": ["doc_type", "compare_kind", "current_period"],
        "properties": {
            "doc_type": {"type": "string"},
            "compare_kind": {"type": "string"},
            "current_period": {"type": "string"},
        },
    },
    "local_shop_list": {
        "required": [],
        "properties": {
            "platform": {"type": "string"},
        },
    },
    "local_warehouse_list": {
        "required": [],
        "properties": {
            "is_virtual": {"type": "boolean"},
        },
    },
    "local_db_export": {
        "required": ["doc_type"],
        "properties": {"doc_type": {"type": "string"}},
    },
    "trigger_erp_sync": {
        "required": ["sync_type"],
        "properties": {"sync_type": {"type": "string"}},
    },
}

# LOCAL_ROUTING_PROMPT 已移除 — 工具路由由 tool_selector 处理，
# 核心规则已整合到 ERP_ROUTING_PROMPT（40 行精简版）
