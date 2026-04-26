"""ERP Agent 能力清单 + 工具描述格式化。

从 plan_builder.py / erp_agent.py 拆出，减少主文件行数。
- get_capability_manifest(): 唯一 Source of Truth
- build_tool_description(): 从 manifest 格式化为 5 段式描述文本

ERPAgent.build_tool_description() 委托此模块。
"""
from __future__ import annotations


def get_capability_manifest() -> dict:
    """导出 erp_agent 完整能力清单（唯一 Source of Truth）。

    所有内容结构化，build_tool_description() 纯格式化消费。
    设计文档: docs/document/TECH_Agent能力通信架构.md §3.3.1
    """
    from services.agent.plan_builder import (
        VALID_DOMAINS, VALID_MODES, VALID_DOC_TYPES,
    )
    from services.kuaimai.erp_unified_schema import (
        GROUP_BY_MAP, VALID_TIME_COLS, PLATFORM_NORMALIZE,
        EXPORT_COLUMNS,
    )
    group_by_dims = sorted({v for v in GROUP_BY_MAP.values()})
    platform_names = sorted({
        k for k in PLATFORM_NORMALIZE if not k.isascii()
    })
    field_categories = {
        category: [cn_name for _, cn_name in fields]
        for category, fields in EXPORT_COLUMNS.items()
    }

    return {
        "domains": sorted(VALID_DOMAINS),
        "modes": sorted(VALID_MODES),
        "doc_types": sorted(VALID_DOC_TYPES),
        "group_by": group_by_dims,
        "filters": [
            "platform", "product_code", "order_no", "include_invalid",
            "shop_name", "warehouse_name", "supplier_name",
            "express_no", "buyer_nick", "order_status", "doc_status",
            "aftersale_type", "refund_status", "express_company",
            "receiver_state", "receiver_city", "item_name",
            "is_cancel", "is_refund", "is_exception", "is_halt",
            "is_urgent", "is_presell",
            "receiver_district", "receiver_address", "reason",
        ],
        "time_cols": sorted(VALID_TIME_COLS),
        "platforms": platform_names,
        "field_categories": field_categories,
        "summary": (
            "ERP 数据查询专员，查询订单/库存/采购/售后等数据，"
            "口语化表达和错别字自动识别"
        ),
        "use_when": [
            "用户问任何涉及订单/库存/采购/售后/发货/物流/商品/销量的问题",
            "含操作性词汇（对账/核对/处理/优先处理/多少钱/价格）需要先查数据",
            ("口语/错别字也要识别：'丁单'=订单，'酷存'=库存，"
             "'够不够卖'=库存查询，'到了没'=采购到货，"
             "'退了'=售后，'爆单'=销量统计，'查一下呗'=数据查询"),
        ],
        "dont_use_when": [
            {"场景": "写操作（创建/修改/取消）", "替代": "erp_execute"},
            {"场景": "非 ERP 数据（天气/新闻）", "替代": "web_search"},
            {"场景": "业务规则/操作流程", "替代": "search_knowledge"},
        ],
        "returns": [
            "summary 模式：统计数字（总量/金额/分组明细），直接内联",
            "export 模式：数据存 staging parquet + 返回 profile 摘要（行数/字段/预览）",
            "导出工作流：erp_agent 查数据存 staging → code_execute 读 staging 写 Excel",
            "跨域并行：各域数据独立时一次返回多域数据 + 关联计算提示，code_execute 按提示关联",
            "计划模式（status=plan）：超出一次执行能力时返回执行计划，调用方按计划逐步调用并传递中间结果",
        ],
        "limits": [
            "编码/单号IN匹配：单次最多500个值。超过500个的跨域关联查询，"
            "应分别导出两份数据到staging，再用code_execute按编码JOIN",
        ],
        "examples": [
            {"query": "昨天淘宝退货按店铺统计",
             "effect": "summary + platform=taobao + group_by=shop"},
            {"query": "导出本周订单明细", "effect": "export → staging + profile"},
            {"query": "编码 HZ001 的库存", "effect": "product_code 过滤"},
            {"query": "上月采购到货按供应商统计",
             "effect": "summary + group_by=supplier"},
            {"query": "包含刷单的订单有多少",
             "effect": "include_invalid=true"},
            {"query": "今天刷单有多少",
             "effect": "is_scalping=true + include_invalid=true"},
        ],
        "parallel_hint": (
            "支持并行多次调用：用户请求包含多个独立子任务时，"
            "同时发起多个 erp_agent 调用，每个 task 只写一个子任务"
        ),
        "auto_behaviors": [
            ">200行自动导出 staging 文件",
            "返回格式自动适配（文本/表格/文件链接）",
            "降级链：AI提取 → 关键词匹配 → abort",
        ],
    }


def build_tool_description() -> str:
    """从 capability manifest 格式化为 5 段式描述文本。"""
    m = get_capability_manifest()

    lines = [m["summary"]]
    lines.append("\n使用场景：" + "；".join(m["use_when"]))
    dont = " / ".join(
        f"{d['场景']}→{d['替代']}" for d in m["dont_use_when"]
    )
    lines.append(f"不要用于：{dont}")

    lines.append("\n能力：")
    lines.append(f"- 输出模式：{' / '.join(m['modes'])}（>200行自动导出文件）")
    lines.append(f"- 分组统计：按{'/'.join(m['group_by'])}统计")
    lines.append(f"- 过滤：自动识别{'、'.join(m['platforms'])}、商品编码、订单号")
    lines.append(f"- 时间列：{' / '.join(m['time_cols'])}（默认 doc_created_at）")
    lines.append("- 异常数据：默认排除刷单，query 中写'包含刷单'则包含")
    lines.append(
        "- 跨域查询：各域数据独立时一次并行查询；"
        "超出一次执行能力时进入计划模式（status=plan），返回执行计划由调用方逐步执行"
    )
    categories = m.get("field_categories", {})
    if categories:
        lines.append(f"- 可查询信息：{'/'.join(categories.keys())}")
        lines.append(
            "  （query 中提到具体信息如'备注''地址''快递单号'"
            "会自动返回对应字段）",
        )

    if m.get("parallel_hint"):
        lines.append(f"\n并行调用：{m['parallel_hint']}")

    lines.append("\n返回：")
    for r in m["returns"]:
        lines.append(f"- {r}")

    if m.get("limits"):
        lines.append("\n限制：")
        for lim in m["limits"]:
            lines.append(f"- {lim}")

    lines.append("\nquery 示例：")
    for ex in m["examples"]:
        lines.append(f"· \"{ex['query']}\" → {ex['effect']}")

    return "\n".join(lines)
