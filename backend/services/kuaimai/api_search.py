"""
ERP API 搜索服务

提供按需发现 ERP API 操作和参数文档的能力。
支持两种搜索模式：
- 精确查询：tool:action 格式（如 erp_trade_query:order_list）
- 关键词匹配：自然语言搜索（如 "退款" "库存"）

搜索范围：TOOL_REGISTRIES 中所有注册的 action + ApiEntry 元数据。
"""

from typing import List, Tuple

from services.kuaimai.registry import TOOL_REGISTRIES
from services.kuaimai.registry.base import ApiEntry

# 搜索结果最大条数
_MAX_RESULTS = 5

# 场景指南文档（P1/P2 易混淆决策指南）
# 从 ERP_ROUTING_PROMPT 迁移，按需加载而非全部塞进系统提示词
_SCENARIO_DOCS: dict[str, str] = {
    "商品查询": (
        "商品查询action选择指南：\n"
        "- 搜商品/商品列表 → product_list（支持状态/日期筛选）\n"
        "- 某个商品详情 → product_detail(outer_id=XX 或 item_id=XX)\n"
        "- 批量查多个商品 → multi_product(outer_ids=\"A,B,C\")\n"
        "- 商品SKU/规格列表 → sku_list(outer_id=XX)\n"
        "- 条码查商品 → multicode_query(code=XX)\n"
        "- 商品供应商 → item_supplier_list(outer_ids=XX)\n"
        "- 商品成本价 → history_cost_price(item_id+sku_id 两个都必填)\n"
        "- 商品在哪个店铺卖 → outer_id_list(outer_ids=XX)\n"
        "- 商品分类 → cat_list(卖家分类) 或 classify_list(系统类目)\n"
        "- 品牌列表 → brand_list"
    ),
    "调拨": (
        "调拨三种单据：\n"
        "- 调拨单(任务) → erp_warehouse_query(allocate_list)\n"
        "- 调拨入库 → erp_warehouse_query(allocate_in_list)\n"
        "- 调拨出库 → erp_warehouse_query(allocate_out_list)\n"
        "- 调拨单明细 → erp_warehouse_query(allocate_detail, code=XX)\n"
        "- 同理：other_in/other_out、inventory_sheet、unshelve、"
        "process_order 各有 _list 和 _detail"
    ),
    "采购": (
        "采购链路4阶段（按业务流程顺序）：\n"
        "1. 采购单 → purchase_order_list\n"
        "2. 到货/收货单 → warehouse_entry_list\n"
        "3. 上架单 → shelf_list\n"
        "4. 采购退货 → purchase_return_list\n"
        "- 采购建议 → purchase_strategy(query_key=关键词)\n"
        "- 供应商列表 → supplier_list\n"
        "- 各阶段都有 _detail action 查看单据详情\n"
        "- 归档：purchase_order_history/warehouse_entry_history/"
        "purchase_return_history/shelf_history（需 start_date+end_date）"
    ),
    "物流": (
        "物流查询细分（4种action）：\n"
        "- 查快递轨迹 → express_query(system_id=XX 或 express_no=XX)\n"
        "- 物流公司列表 → logistics_company_list（配置数据）\n"
        "- 获取快递单号 → waybill_get(system_ids=XX)\n"
        "- 注意：「查快递」→ express_query，不是 logistics_company_list"
    ),
    "统计": (
        "统计类汇总策略：\n"
        "- 今天成交多少钱 → 需翻页拉明细算payment总和，不能只看total\n"
        "- 退货率 → 订单total ÷ 退货total\n"
        "- 各仓库库存 → 先 warehouse_list 再逐仓 stock_status(warehouse_id=XX)\n"
        "- 各店铺XX → 先 shop_list 再逐店铺查\n"
        "- shop_ids 是数字ID，不支持名称筛选，需先 shop_list 获取ID"
    ),
    "分销": (
        "分销商查询：\n"
        "- 分销商列表 → erp_info_query(distributor_list)\n"
        "- 分销订单 → erp_distribution_query\n"
        "- 分销商品/供销小店 → erp_api_search(\"分销\") 查文档"
    ),
    "多步查询": (
        "需要先查ID再查详情的多步链路：\n"
        "- 某订单的快递 → 先 order_list(order_id=XX) 拿 system_id → "
        "再 express_query(system_id=XX)\n"
        "- 某采购单收货了没 → 先 purchase_order_detail → "
        "再 warehouse_entry_list 关联查询\n"
        "- 某商品在哪个店铺卖 → outer_id_list(outer_ids=XX)"
    ),
    "订单号": (
        "订单号vs系统单号：\n"
        "- 用户说「订单号」→ 平台订单号，用 order_id\n"
        "- 用户说「系统单号」「ERP单号」→ 用 system_id\n"
        "- 不确定 → 先用 order_id，系统会在零结果时自动建议改用 system_id"
    ),
}


def search_erp_api(query: str) -> str:
    """搜索 ERP 可用的 API 操作和参数文档

    Args:
        query: 自然语言关键词或 'tool:action' 精确查询

    Returns:
        格式化的 API 文档文本
    """
    query = query.strip()
    if not query:
        return "请输入搜索关键词"

    # 精确查询模式：tool:action
    if ":" in query:
        return _exact_search(query)

    # 关键词搜索模式
    return _keyword_search(query)


def _exact_search(query: str) -> str:
    """精确查询：tool_name:action_name"""
    parts = query.split(":", 1)
    tool_name = parts[0].strip()
    action_name = parts[1].strip() if len(parts) > 1 else ""

    registry = TOOL_REGISTRIES.get(tool_name)
    if not registry:
        available_tools = ", ".join(sorted(TOOL_REGISTRIES.keys()))
        return f"未找到工具「{tool_name}」，可用工具: {available_tools}"

    if action_name:
        entry = registry.get(action_name)
        if not entry:
            available = ", ".join(sorted(registry.keys()))
            return (
                f"工具 {tool_name} 无操作「{action_name}」，"
                f"可用操作: {available}"
            )
        return _format_entry_detail(tool_name, action_name, entry)

    # 只指定了 tool_name，列出所有 action
    return _format_tool_actions(tool_name, registry)


def _keyword_search(query: str) -> str:
    """关键词搜索：在 action 名称、描述和场景指南中匹配"""
    keywords = query.lower().split()
    matches: List[Tuple[int, str, str, ApiEntry]] = []

    for tool_name, registry in TOOL_REGISTRIES.items():
        if not isinstance(registry, dict):
            continue
        for action_name, entry in registry.items():
            if not isinstance(entry, ApiEntry):
                continue
            score = _calc_match_score(
                keywords, tool_name, action_name, entry,
            )
            if score > 0:
                matches.append((score, tool_name, action_name, entry))

    # 匹配场景指南
    scenario_hits = _match_scenarios(keywords)

    if not matches and not scenario_hits:
        return f"未找到与「{query}」匹配的 ERP API 操作，请尝试其他关键词"

    # 按匹配度降序排序，取前 N 条
    matches.sort(key=lambda x: x[0], reverse=True)
    top = matches[:_MAX_RESULTS]

    lines = []
    # 场景指南优先展示
    if scenario_hits:
        lines.append("📖 场景指南：\n")
        lines.extend(scenario_hits)
        lines.append("")

    if top:
        lines.append(f"找到 {len(matches)} 个匹配，显示前 {len(top)} 个：\n")
        for _, tool_name, action_name, entry in top:
            lines.append(
                _format_entry_brief(tool_name, action_name, entry)
            )
    return "\n".join(lines)


def _match_scenarios(keywords: list[str]) -> list[str]:
    """在场景指南中匹配关键词，返回命中的指南内容"""
    hits = []
    for title, content in _SCENARIO_DOCS.items():
        search_text = f"{title} {content}".lower()
        if any(kw in search_text for kw in keywords):
            hits.append(f"【{title}】\n{content}\n")
    return hits


def _calc_match_score(
    keywords: List[str],
    tool_name: str,
    action_name: str,
    entry: ApiEntry,
) -> int:
    """计算关键词匹配分数（越高越匹配）"""
    score = 0
    search_text = (
        f"{tool_name} {action_name} {entry.description} "
        f"{' '.join(entry.param_map.keys())}"
    ).lower()

    for kw in keywords:
        if kw in action_name.lower():
            score += 3  # action 名称匹配权重最高
        elif kw in entry.description:
            score += 2  # 描述匹配
        elif kw in search_text:
            score += 1  # 参数名等其他匹配
    return score


def _format_entry_detail(
    tool_name: str, action_name: str, entry: ApiEntry,
) -> str:
    """格式化单个 API 操作的完整文档"""
    params = entry.param_map
    required = set(entry.required_params)

    lines = [
        f"📋 {tool_name}:{action_name}",
        f"描述: {entry.description}",
        f"API方法: {entry.method}",
    ]

    if params:
        param_lines = []
        for user_key, api_key in params.items():
            marker = "（必填）" if user_key in required else ""
            doc = entry.param_docs.get(user_key, "")
            doc_str = f": {doc}" if doc else ""
            param_lines.append(
                f"  - {user_key}{marker}{doc_str} → {api_key}"
            )
        lines.append("参数:")
        lines.extend(param_lines)
    else:
        lines.append("参数: 无（仅需指定 action）")

    if entry.defaults:
        lines.append(f"默认值: {entry.defaults}")

    if entry.is_write:
        lines.append("类型: 写操作（需用户确认）")

    if entry.error_codes:
        lines.append("错误码:")
        for code, desc in entry.error_codes.items():
            lines.append(f"  - {code}: {desc}")

    return "\n".join(lines)


def _format_entry_brief(
    tool_name: str, action_name: str, entry: ApiEntry,
) -> str:
    """格式化 API 操作的简要信息"""
    params = list(entry.param_map.keys())
    required = set(entry.required_params)
    param_parts = [
        f"*{p}" if p in required else p for p in params
    ]
    param_str = f"({'/'.join(param_parts)})" if param_parts else ""
    return f"- {tool_name}:{action_name} — {entry.description}{param_str}"


def _format_tool_actions(
    tool_name: str, registry: dict,
) -> str:
    """列出工具的所有操作"""
    lines = [f"工具 {tool_name} 的所有操作：\n"]
    for action_name, entry in sorted(registry.items()):
        if not isinstance(entry, ApiEntry):
            continue
        lines.append(
            _format_entry_brief(tool_name, action_name, entry)
        )
    return "\n".join(lines)
