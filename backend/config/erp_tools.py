"""Compatibility projections; tool definitions are owned by ToolSpec. Prompt selection stays here."""

from typing import Any, Dict, List, Set
from services.tools.catalog import definition_registry, group_schemas, validation_schemas
from services.tools.definitions.erp_schemas import (
    _format_action_desc, _read_actions, _write_actions_by_category, _build_query_tool
)
from config.erp_local_tools import LOCAL_TOOL_SCHEMAS, build_local_tools
from services.tools.definitions.erp_schemas import (AFTERSALES_REGISTRY, BASIC_REGISTRY, DISTRIBUTION_REGISTRY, PRODUCT_REGISTRY, PURCHASE_REGISTRY, QIMEN_REGISTRY, TRADE_REGISTRY, WAREHOUSE_REGISTRY, ApiEntry)


ERP_SYNC_TOOLS: Set[str] = {s.name for s in definition_registry().specs() if 'erp_tools' in s.catalog_groups and 'erp_local_tools' not in s.catalog_groups}


ERP_TOOL_SCHEMAS: Dict[str, Dict[str, Any]] = validation_schemas('erp_tools')


def build_erp_tools() -> List[Dict[str, Any]]:
    return group_schemas('erp_tools')


def build_erp_search_tool() -> Dict[str, Any]:
    return definition_registry().require('erp_api_search').to_schema('erp_search')


def build_fetch_all_pages_tool() -> Dict[str, Any]:
    return definition_registry().require('fetch_all_pages').to_schema()


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
    "- **local_shop_list / local_warehouse_list / local_supplier_list**：店铺/仓库/供应商列表\n"
    "- **trigger_erp_sync**：手动触发数据同步\n"
    "- **fetch_all_pages**：本地没有的数据（如物流轨迹）全量翻页拉取\n"
    "- **erp_* 远程工具**：物流轨迹、操作日志、仓库操作、写入操作\n"
    "- **code_execute**：纯计算沙盒，做公式计算/生成图表\n"
    "- **file_search**：搜索/准备工作区文件，数据文件自动转 Parquet 存 staging\n\n"
    "### 常见场景\n"
    "- 今天/本周/本月多少单 → local_data(doc_type=order, mode=summary, filters=[时间条件])\n"
    "- 已发货/未发货订单 → local_data(filters=[{field:order_status, op:eq, value:SELLER_SEND_GOODS}])\n"
    "- 按店铺/平台统计 → local_data(mode=summary, group_by=[shop_name])\n"
    "- 按商品排名 → local_data(mode=summary, group_by=[outer_id])\n"
    "- 导出 Excel → local_data(mode=export, fields=[...]) → code_execute 写 Excel\n"
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
    "- 连续 2 次空结果 → 向用户确认条件\n\n"
    "### 参数充分度判断（决策框架）\n"
    "执行查询前，判断关键参数是否充分：\n"
    "- **充分**（用户明确给出）→ 直接查\n"
    "- **可推断且无歧义**（只有一个合理值）→ 直接查，结果中说明假设\n"
    "- **有歧义**（多个合理值）→ 向用户列出可选的查询条件\n\n"
    "追问时告知用户可选条件，帮助用户一次说清楚：\n"
    "- 统计类（销量/销售额/订单数）：时间范围、店铺、平台、商品、是否排除异常\n"
    "- 商品类（库存/价格/编码）：商品名称或编码、仓库\n"
    "- 单据类（订单/采购/售后）：单号、店铺、时间范围、状态\n\n"
    "补充规则：\n"
    "- 多条匹配 → 列出候选让用户选择\n"
    "- 查询结果中 is_exception > 0 → 主动告知异常分布并询问是否排除\n"
    "- 写操作（取消/修改/标记）→ 必须向用户确认对象和影响\n\n"
    "### ERP 远程工具协议\n"
    "1. 两步查询：先传 action 拿参数文档 → 再传 params 执行\n"
    "2. page/page_size 在 tool 级别传，不放 params 里\n\n"
    "### 编码识别\n"
    "- 裸值编码/单号 → 先 local_product_identify(code=XX) 确认类型\n"
    "- 套件无独立库存 → 查子单品逐个查\n"
    "- 同一编码每会话只识别一次\n\n"
    "### 规则\n"
    "- 禁止猜测参数值，有歧义时向用户确认\n"
    "- 参数明确时直接查询，禁止试探性查询\n"
    "- 名称搜索无结果 → 向用户确认\n"
)
