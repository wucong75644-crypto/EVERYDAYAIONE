"""
ERP 本地查询工具定义（8个工具）

本地工具直接查询 PostgreSQL，毫秒级响应，优先于 API 工具使用。
注册到 Agent Loop Phase2 工具循环。

设计文档: docs/document/TECH_ERP数据本地索引系统.md §6
"""

from typing import Any, Dict, List, Set


ERP_LOCAL_TOOLS: Set[str] = {
    "local_data",              # 统一查询引擎（替代 7 个碎片工具）
    "local_product_stats",
    "local_stock_query",
    "local_product_identify",
    "local_platform_map_query",
    "local_compare_stats",     # 时间事实层 — 同比/环比 (PR1 §6.2.3)
    "local_shop_list",
    "local_warehouse_list",
    "local_supplier_list",
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
    """构建本地查询工具定义（1 统一查询 + 7 专用 + 1 同步）"""
    return [
        # 1. 统一查询引擎（替代 purchase_query/aftersale_query/order_query/
        #    product_flow/doc_query/global_stats/db_export 共 7 个工具）
        _tool(
            "local_data",
            "本地数据库统一查询工具。支持查询/统计/导出 ERP 全部业务表数据。\n"
            "除单据表外还支持：库存(stock)、商品(product)、SKU(sku)、"
            "日统计(daily_stats)、平台映射(platform_map)、批次库存(batch_stock)、"
            "订单日志(order_log)、售后日志(aftersale_log)。\n"
            "用 filters 数组指定过滤条件，任意字段组合均可。\n\n"
            "常用字段：\n"
            "- order_status/doc_status: 状态(WAIT_AUDIT/WAIT_SEND_GOODS/SELLER_SEND_GOODS/FINISHED/CLOSED)\n"
            "- consign_time: 发货时间  |  pay_time: 付款时间  |  doc_created_at: 创建时间\n"
            "- shop_name: 店铺名  |  platform: 平台(tb/jd/pdd/fxg/kuaishou/xhs/1688)\n"
            "- outer_id: 商品主编码  |  sku_outer_id: SKU编码  |  item_name: 商品名称\n"
            "- order_no: 平台订单号  |  express_no: 快递单号\n"
            "- supplier_name: 供应商  |  warehouse_name: 仓库\n"
            "- amount: 金额  |  pay_amount: 实付金额  |  quantity: 数量\n"
            "- cost: 成本  |  gross_profit: 毛利润  |  price: 单价\n"
            "- post_fee: 运费  |  discount_fee: 优惠金额  |  refund_money: 退款金额\n"
            "- is_scalping: 是否刷单/空包(0=正常,1=刷单)  |  is_exception: 是否异常订单(0/1)\n"
            "- is_refund: 是否退款(0/1)  |  is_cancel: 是否取消(0/1)\n"
            "- aftersale_type: 售后类型(0~9)  |  refund_status: 退款状态  |  text_reason: 售后原因\n"
            "- buyer_nick: 买家昵称  |  status_name: 状态中文名\n\n"
            "op: eq(等于) ne(不等于) gt(大于) gte(>=) lt(<) lte(<=) "
            "in(在列表中) like(模糊) is_null(是否空) between(区间)\n\n"
            "示例1：4月14日已发货订单列表\n"
            "  doc_type=order, mode=detail, filters=[\n"
            '    {"field":"order_status","op":"eq","value":"SELLER_SEND_GOODS"},\n'
            '    {"field":"consign_time","op":"gte","value":"2026-04-14 00:00:00"},\n'
            '    {"field":"consign_time","op":"lt","value":"2026-04-15 00:00:00"}]\n\n'
            "示例2：淘宝金额>500订单按店铺统计\n"
            "  doc_type=order, mode=summary, filters=[\n"
            '    {"field":"platform","op":"eq","value":"tb"},\n'
            '    {"field":"amount","op":"gt","value":500}],\n'
            "  group_by=[\"shop_name\"]\n\n"
            "示例3：导出3月拼多多发货订单\n"
            "  doc_type=order, mode=export, filters=[...],\n"
            "  fields=[\"order_no\",\"amount\",\"consign_time\"]",
            {
                "doc_type": _enum(
                    "单据类型（新增：stock=库存快照/product=商品/sku=SKU/"
                    "daily_stats=日统计/platform_map=平台映射/"
                    "batch_stock=批次库存/order_log=订单日志/aftersale_log=售后日志）",
                    ["order", "purchase", "aftersale", "receipt",
                     "shelf", "purchase_return",
                     "stock", "product", "sku", "daily_stats",
                     "platform_map", "batch_stock",
                     "order_log", "aftersale_log"],
                ),
                "mode": _enum(
                    "输出模式：summary=聚合统计，detail=明细列表，export=导出文件",
                    ["summary", "detail", "export"],
                ),
                "filters": {
                    "type": "array",
                    "description": "过滤条件数组，每个元素 {field, op, value}",
                    "items": {
                        "type": "object",
                        "properties": {
                            "field": {"type": "string", "description": "列名"},
                            "op": {
                                "type": "string",
                                "enum": ["eq", "ne", "gt", "gte", "lt", "lte",
                                         "in", "like", "is_null", "between"],
                            },
                            "value": {"description": "过滤值"},
                        },
                        "required": ["field", "op", "value"],
                    },
                },
                "group_by": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "分组字段（mode=summary时），如 [\"shop_name\"] 或 [\"platform\"]",
                },
                "sort_by": _str("排序字段（mode=detail时）"),
                "sort_dir": _enum("排序方向", ["asc", "desc"]),
                "fields": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "返回/导出字段列表。mode=export 不传则返回可用字段文档",
                },
                "limit": _int("返回行数上限（detail默认20，export默认5000，上限10000）"),
                "time_type": _enum(
                    "时间列（控制 summary 模式的统计时间维度）。"
                    "含「付款」用 pay_time，含「发货」用 consign_time，默认 doc_created_at",
                    ["doc_created_at", "pay_time", "consign_time"],
                ),
            },
            ["doc_type", "filters"],
        ),
        # 2. 统计报表查询（预聚合表，不走 local_data）
        _tool(
            "local_product_stats",
            "按商品编码查统计报表（日/周/月维度）。毫秒级响应。"
            "返回聚合指标：订单数/销量/销售额、采购数/采购额、"
            "售后数/退货数。支持日期范围对比。"
            "⚠ 需精确编码，模糊时先 local_product_identify。"
            "相关工具：全局统计（不按商品）用 local_data(mode=summary)；"
            "查供应链流转用 local_data 按 doc_type 分查。",
            {
                "product_code": _str("商品编码"),
                "period": _enum("统计周期", ["day", "week", "month"]),
                "start_date": _str("起始日期(YYYY-MM-DD)，默认当月1号"),
                "end_date": _str("结束日期(YYYY-MM-DD)，默认今天"),
            },
            ["product_code"],
        ),
        # 3. 库存查询（erp_stock_status 表，不走 local_data）
        _tool(
            "local_stock_query",
            "按商品精确编码查库存（可售/总库存/锁定/在途/各仓分布）。毫秒级响应。"
            "⚠ 需要精确编码，用户给模糊名称/简称时先调 local_product_identify 确认。"
            "相关工具：需要实时数据或本地查不到时改用 erp_product_query(stock_status)；"
            "查采购到货进度用 local_data(doc_type=purchase)。",
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
            "⚠ 返回多条匹配时，必须用 ask_user 让用户确认目标商品，禁止自行选择第一条。"
            "相关工具：识别到编码后可用 local_stock_query 查库存、local_data 查订单/采购/售后、"
            "local_product_stats 查统计。",
            {
                "code": _str("商品编码/SKU编码/条码（精确匹配）"),
                "name": _str("商品名称关键词（模糊搜索）"),
                "spec": _str("规格名称关键词（模糊搜索，如'红色''120g'）"),
            },
            [],
        ),
        # 5. 平台映射查询
        _tool(
            "local_platform_map_query",
            "查ERP编码↔平台商品映射（上架/下架检查）。毫秒级响应。"
            "返回编码在哪些平台有售，或平台商品ID反查ERP编码。"
            "product_code 和 num_iid 至少传一个。"
            "相关工具：编码模糊时先 local_product_identify；"
            "查库存用 local_stock_query；"
            "查该商品在某平台的订单用 local_data(product_code=..., platform=...)。",
            {
                "product_code": _str(
                    "ERP商品编码（查此编码在哪些平台有售）"
                ),
                "num_iid": _str("平台商品ID（反查对应ERP编码）"),
                "user_id": _str("店铺ID过滤（只查指定店铺）"),
            },
            [],
        ),
        # 6. 同比/环比对比统计 (时间事实层 §6.2.3)
        _tool(
            "local_compare_stats",
            "时间维度对比统计（同比/环比/任意区间对比）。毫秒级响应。"
            "由后端确定地计算对比基线（含中文星期/相对时间标签），"
            "返回结构化双时间块 + 数据对比。"
            "⚠ 涉及「对比/同比/环比/比上周/比上月/比去年」的查询必须用本工具，"
            "禁止调用 local_data 两次再让模型口述对比。"
            "适合：今天 vs 昨天、本周 vs 上周、本月 vs 上月同期、订单同比去年同期等。"
            "相关工具：对比后需要明细数据用 local_data(mode=export)；"
            "单商品对比趋势用 local_product_stats。",
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
                "shop_user_id": _str("按店铺ID精确过滤（从local_shop_list获取）"),
                "platform": _str(
                    "按平台过滤(tb=淘宝/jd=京东/pdd=拼多多/fxg=抖音/"
                    "kuaishou=快手/xhs=小红书/1688)",
                ),
                "supplier_name": _str("按供应商过滤（模糊匹配）"),
                "supplier_code": _str("按供应商编码精确过滤（从supplier_list获取）"),
                "warehouse_name": _str("按仓库过滤"),
                "warehouse_id": _str("按仓库ID精确过滤（从local_warehouse_list获取）"),
            },
            ["doc_type", "compare_kind", "current_period"],
        ),
        # 7. 店铺列表
        _tool(
            "local_shop_list",
            "查询店铺列表（含所有店铺，包括新开未出单的）。毫秒级响应。"
            "返回店铺名称/平台/状态/ID，按平台分组显示。"
            "支持按平台过滤（如只看拼多多店铺）。"
            "适合：有哪些店铺、拼多多店铺列表、各平台店铺等查询。"
            "相关工具：拿到店铺名后可用 local_data(mode=summary, shop_name=...) 统计该店铺数据。",
            {
                "platform": _enum(
                    "按平台过滤(tb=淘宝,jd=京东,pdd=拼多多,fxg=抖音,kuaishou=快手,xhs=小红书)",
                    ["tb", "jd", "pdd", "fxg", "kuaishou", "xhs", "1688"],
                ),
            },
            [],
        ),
        # 8. 仓库列表
        _tool(
            "local_warehouse_list",
            "查询仓库列表（实体仓+虚拟仓）。毫秒级响应。"
            "返回仓库名称/编码/类型/状态/地址。"
            "适合：有哪些仓库、仓库地址、仓库编码等查询。"
            "相关工具：拿到仓库名后可用 local_stock_query 查库存分布；"
            "查某仓库出入库统计用 local_data(mode=summary, warehouse_name=...)。",
            {
                "is_virtual": _bool("是否只查虚拟仓（true=只看虚拟仓, false=只看实体仓, 不传=全部）"),
            },
            [],
        ),
        # 9. 供应商列表
        _tool(
            "local_supplier_list",
            "查询供应商列表（含编码/联系人/分类）。毫秒级响应。"
            "返回供应商名称/编码/状态/联系人/分类，按分类分组显示。"
            "支持按分类过滤（如只看某采购员负责的供应商）。"
            "适合：有哪些供应商、供应商列表、供应商联系方式等查询。"
            "相关工具：拿到供应商名后可用 local_data(doc_type=purchase, mode=summary, "
            "supplier_name=...) 统计该供应商采购数据。",
            {
                "category": _str("按分类/采购员过滤（模糊匹配，如'采购陈'）"),
                "status": _int("按状态过滤（0=停用, 1=启用，不传=全部）"),
            },
            [],
        ),
        # 10. 手动触发同步
        _tool(
            "trigger_erp_sync",
            "手动触发ERP数据同步。"
            "仅在 local 工具返回 ⚠ 同步警告或查不到预期数据时使用。"
            "商品/库存查不到不要用此工具（local_product_identify 会自动兜底）。"
            "同步完成后重新调用原查询工具。"
            "相关工具：同步后用原查询工具（如 local_data/local_stock_query）验证数据。",
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
    "local_data": {
        "required": ["doc_type", "filters"],
        "properties": {
            "doc_type": {"type": "string"},
            "filters": {"type": "array"},
            "mode": {"type": "string"},
        },
    },
    "local_product_stats": {
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
    "local_supplier_list": {
        "required": [],
        "properties": {
            "category": {"type": "string"},
            "status": {"type": "integer"},
        },
    },
    "trigger_erp_sync": {
        "required": ["sync_type"],
        "properties": {"sync_type": {"type": "string"}},
    },
}

# LOCAL_ROUTING_PROMPT 已移除 — 工具路由由 tool_selector 处理，
# 核心规则已整合到 ERP_ROUTING_PROMPT（40 行精简版）
