"""
ChatHandler 统一工具注册

单循环 Agent 架构下，ChatHandler 直接持有的顶层工具列表。
AI 大脑看到这些工具描述后自主选择调用，无需路由层。

并发安全标记 (is_concurrency_safe)：
  True  — 只读查询，可与其他只读工具并行执行
  False — 写操作或有副作用，必须串行执行

安全级别 (safety_level)：
  safe      — 直接执行，不通知用户
  confirm   — 通知用户后执行（消耗积分类）
  dangerous — 必须用户确认才执行（写操作类）
"""

import re
from enum import Enum
from typing import Any, Dict, List, Set


class SafetyLevel(str, Enum):
    """工具安全级别（参考 Claude Code Permission Check）"""
    SAFE = "safe"            # 只读查询，直接执行
    CONFIRM = "confirm"      # 消耗资源，通知用户后执行
    DANGEROUS = "dangerous"  # 写操作/不可逆，必须用户确认


class ToolGroup(str, Enum):
    """工具业务分组"""
    ERP_LOCAL = "erp_local"     # 本地 ERP 查询（毫秒级）
    ERP_REMOTE = "erp_remote"   # 远程 ERP API（秒级）
    ERP_WRITE = "erp_write"     # ERP 写操作
    SEARCH = "search"           # 搜索类（知识库/互联网/ERP文档）
    MEDIA = "media"             # 图片/视频生成
    CRAWLER = "crawler"         # 社交平台爬虫
    CODE = "code"               # 代码执行

from config.erp_tools import build_erp_tools  # 已含 build_local_tools
from config.crawler_tools import build_crawler_tools
from config.code_tools import build_code_tools

# ============================================================
# 工具并发安全标记
# ============================================================

# 只读工具 — 可并行
_CONCURRENT_SAFE_TOOLS: Set[str] = {
    # ERP 查询（远程 + 本地）
    "erp_info_query", "erp_product_query", "erp_trade_query",
    "erp_aftersales_query", "erp_warehouse_query", "erp_purchase_query",
    "erp_taobao_query",
    "local_product_identify", "local_stock_query", "local_order_query",
    "local_purchase_query", "local_aftersale_query", "local_doc_query",
    "local_product_stats", "local_product_flow", "local_global_stats",
    "local_platform_map_query",
    # 搜索类
    "erp_api_search", "search_knowledge", "web_search",
    "social_crawler",
    # 代码执行（沙箱隔离，可并行）
    "code_execute",
}

# 写操作工具 — 必须串行
# erp_execute, trigger_erp_sync, generate_image, generate_video 等
# 不在 _CONCURRENT_SAFE_TOOLS 中的都视为串行


def is_concurrency_safe(tool_name: str) -> bool:
    """判断工具是否可以并行执行"""
    return tool_name in _CONCURRENT_SAFE_TOOLS


# ============================================================
# 工具安全级别
# ============================================================

# 非 safe 的工具（数量少，显式列出）
# 未列出的工具默认为 safe（查询类占绝大多数）
_SAFETY_LEVELS: Dict[str, SafetyLevel] = {
    # confirm — 消耗资源，通知用户
    "generate_image": SafetyLevel.CONFIRM,
    "generate_video": SafetyLevel.CONFIRM,
    "code_execute": SafetyLevel.CONFIRM,
    # dangerous — 写操作，必须用户确认
    "erp_execute": SafetyLevel.DANGEROUS,
    "trigger_erp_sync": SafetyLevel.DANGEROUS,
}


def get_safety_level(tool_name: str) -> SafetyLevel:
    """获取工具的安全级别，未标记的默认为 safe"""
    return _SAFETY_LEVELS.get(tool_name, SafetyLevel.SAFE)


# ============================================================
# 全局工具使用指引（系统提示词）
# ============================================================

TOOL_SYSTEM_PROMPT = """## 工具使用规则

### 一、用户意图理解（口语/错别字/简称映射）
用户是电商运营人员，说话口语化。遇到以下表达时按映射理解：
- 订单类：「丁单」「单子」「多少单」→ 订单查询；「卖了多少」「成交」「爆单」→ 销量统计
- 库存类：「酷存」「够不够卖」「缺货」「断货」「还剩多少」→ 库存查询
- 采购类：「到了没」「到货」「进货」→ 采购到货查询
- 发货类：「发了多少」「到哪了」→ 物流/发货查询
- 售后类：「退了」「退货」「退款」→ 售后查询
- 统计类：「多少钱」「赚了」「亏了」→ 销售额/利润统计
- 平台：「淘宝」=天猫/淘宝，「拼多多」=PDD，「抖音」=抖店

### 二、工具选择规则
1. **编码识别优先**：用户提到商品名称/简称/模糊编码时，先调 local_product_identify 确认精确编码，再用对应查询工具。同一编码每次对话只需识别一次。

2. **本地优先远程**：local_* 工具查本地数据库（毫秒级），erp_* 工具查远程API（秒级）。优先用本地工具，本地查不到或需要实时数据时再用远程。

3. **不确定先搜索**：不确定用哪个工具时，必须先调 erp_api_search 搜索，不要猜测。

4. **多工具并行**：你可以在一次回复中调用多个工具。没有依赖关系的工具必须并行调用；有依赖关系的串行调用（如先 identify 拿编码再查库存）。

5. **两步查询**：远程 erp_* 工具先传 action 获取参数文档，再传 params 执行。已确定参数时可一步完成。

6. **时间语义**：「多少订单」→ time_type="created"；「发了多少」→ time_type="consign_time"。

### 三、禁止行为（CRITICAL）
- NEVER 不调工具就回答业务数据问题——你就是ERP查询入口，必须查数据再回答
- NEVER 说"我无法查看"或"建议您去ERP查看"——用你的工具查
- NEVER 因为口语化/错别字就放弃理解用户意图"""


def get_tool_system_prompt() -> str:
    """获取全局工具使用指引（注入到 ChatHandler 的系统提示词中）"""
    return TOOL_SYSTEM_PROMPT


# ============================================================
# 顶层工具 schema（始终在 messages 中）
# ============================================================

def _build_common_tools() -> List[Dict[str, Any]]:
    """构建通用工具（非 ERP）"""
    return [
        {
            "type": "function",
            "function": {
                "name": "erp_api_search",
                "description": (
                    "搜索 ERP 可用的 API 操作和参数文档。"
                    "不确定用哪个工具或 action 时必须先调此工具搜索。"
                    "支持关键词（如'退货''库存''调拨'）或精确查询（如'erp_trade_query:order_list'）。"
                    "搜索结果会推荐工具名、action 和必填参数，可直接用于下一步调用。"
                ),
                "parameters": {
                    "type": "object",
                    "required": ["query"],
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "搜索关键词或 tool:action 精确查询",
                        },
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "search_knowledge",
                "description": (
                    "搜索企业知识库中的经验和文档。"
                    "适合查找业务规则、操作流程、历史经验等非数据类问题。"
                    "数据查询用 ERP 工具，不要用知识库。"
                ),
                "parameters": {
                    "type": "object",
                    "required": ["query"],
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "搜索关键词",
                        },
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": (
                    "搜索互联网获取实时信息（天气/新闻/行业资讯等）。"
                    "ERP 业务数据用 local_*/erp_* 工具，不要用互联网搜索。"
                    "社交平台内容（小红书/抖音）用 social_crawler。"
                ),
                "parameters": {
                    "type": "object",
                    "required": ["query"],
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "搜索关键词",
                        },
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "generate_image",
                "description": (
                    "生成/画/绘制图片。"
                    "调用后返回 task_id，图片异步生成。"
                ),
                "parameters": {
                    "type": "object",
                    "required": ["prompt"],
                    "properties": {
                        "prompt": {
                            "type": "string",
                            "description": "图片描述（英文效果更好）",
                        },
                        "aspect_ratio": {
                            "type": "string",
                            "enum": ["1:1", "3:4", "4:3", "9:16", "16:9"],
                            "description": "画面比例，默认 1:1",
                        },
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "generate_video",
                "description": "生成/制作视频。调用后返回 task_id，视频异步生成。",
                "parameters": {
                    "type": "object",
                    "required": ["prompt"],
                    "properties": {
                        "prompt": {
                            "type": "string",
                            "description": "视频描述",
                        },
                    },
                },
            },
        },
    ]


def get_chat_tools(org_id: str | None = None) -> List[Dict[str, Any]]:
    """获取 ChatHandler 工具循环使用的完整工具列表

    按企业配置过滤：散客不加载 ERP 工具，与 ToolExecutor 行为对齐。

    Args:
        org_id: 企业 ID（None=散客，只返回通用工具）

    Returns:
        OpenAI function calling 格式的工具列表
    """
    tools: List[Dict[str, Any]] = []

    # ERP 工具仅企业用户加载（与 ToolExecutor org_id 过滤对齐）
    if org_id is not None:
        tools.extend(build_erp_tools())  # 远程 API + 本地查询

    # 爬虫工具
    tools.extend(build_crawler_tools())

    # 代码执行工具
    tools.extend(build_code_tools())

    # 通用工具（搜索、知识库、图片、视频 — 始终加载）
    tools.extend(_build_common_tools())

    return tools


# ============================================================
# 动态 Schema 注入（ToolSearch 模式）
# ============================================================

# 核心工具：每次请求都传给 LLM 的完整 schema
# 选择标准：benchmark 990 例中出现率 >0.4% 的工具全部直接加载
# 覆盖率：99.7%（1195/1198 次工具调用）
_CORE_TOOLS: Set[str] = {
    # 本地 ERP 查询（11个，覆盖日常业务，毫秒级响应）
    "local_product_identify",   # 编码识别 30.7%
    "local_global_stats",       # 全局统计 16.9%
    "local_order_query",        # 订单查询 14.5%
    "local_stock_query",        # 库存查询 14.1%
    "local_purchase_query",     # 采购查询 12.2%
    "local_doc_query",          # 单据查询 10.7%
    "local_aftersale_query",    # 售后查询 10.5%
    "local_product_stats",      # 商品统计 4.3%
    "local_product_flow",       # 供应链流转 0.4%
    "local_platform_map_query", # 平台映射 0.4%
    "trigger_erp_sync",         # 手动同步（local 工具返回警告时需要）
    # 远程 ERP（1个，调拨/仓储无本地工具，日常操作 5.9%）
    "erp_warehouse_query",
    # 搜索入口（发现延迟加载的远程 ERP 工具）
    "erp_api_search",
    # 通用工具
    "search_knowledge",         # 知识库
    "web_search",               # 互联网搜索
    "generate_image",           # 图片生成
    "generate_video",           # 视频生成
}


def get_core_tools(org_id: str | None = None) -> List[Dict[str, Any]]:
    """获取核心工具列表（ToolSearch 模式下初始传给 LLM 的工具）"""
    all_tools = get_chat_tools(org_id)
    return [t for t in all_tools if t["function"]["name"] in _CORE_TOOLS]


def get_tools_by_names(
    names: Set[str], org_id: str | None = None,
) -> List[Dict[str, Any]]:
    """根据工具名获取完整 schema（用于动态注入已发现的工具）"""
    all_tools = get_chat_tools(org_id)
    return [t for t in all_tools if t["function"]["name"] in names]


# 从 erp_api_search 返回结果中提取工具名的正则
_TOOL_NAME_PATTERN = re.compile(
    r'\b(erp_\w+|local_\w+|social_crawler|code_execute|trigger_erp_sync)\b'
)


def extract_tool_names_from_result(
    result_text: str, org_id: str | None = None,
) -> Set[str]:
    """从 erp_api_search 返回结果中解析工具名

    提取格式如 "erp_trade_query:order_list" 或 "推荐 local_purchase_query" 中的工具名，
    并过滤只保留系统中实际存在的工具（排除核心工具，避免重复注入）。
    """
    raw = set(_TOOL_NAME_PATTERN.findall(result_text))
    # 用完整工具列表做白名单（传 org_id 确保包含 ERP 工具）
    valid = {t["function"]["name"] for t in get_chat_tools(org_id)} - _CORE_TOOLS
    return raw & valid
