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
from config.file_tools import build_file_tools

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
    # 文件操作（只读）
    "file_read", "file_list", "file_search", "file_info",
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

1. **ERP 业务问题**：用户问任何涉及订单、库存、采购、售后、发货、物流、商品、销量、统计、导出、报表的问题时，必须调用 erp_agent 工具。erp_agent 内部自动处理编码识别、工具选择、数据导出、多步查询，支持口语化表达。

2. **知识库**：用户问业务规则、操作流程等非数据类问题时，用 search_knowledge。

3. **互联网搜索**：用户问天气、新闻等实时信息时，用 web_search。社交平台内容搜索不要用此工具。

4. **图片/视频生成**：用户要求画图/生成视频时，用 generate_image / generate_video。

5. **代码执行**：用户要求非 ERP 场景的数据分析、计算时，用 code_execute。ERP 数据导出/报表由 erp_agent 内部处理，不要直接调 code_execute。

6. **工作区文件处理**：用户上传了 Excel/CSV 等文件并要求分析时，用 file_list 确认文件名，然后用 code_execute 读取处理（沙盒内 WORKSPACE_DIR 变量指向用户工作区）。Excel/二进制文件不能用 file_read，必须用 code_execute。

7. **多工具并行**：你可以在一次回复中调用多个工具。没有依赖关系的工具并行调用。

## 对话交互规范（分层处理）

### 简单请求（查看、汇总、描述）
直接执行，展示结果。不需要确认。
示例："这个表有多少行？" → 直接 code_execute 读取并回答。

### 分析请求（计算、对比、趋势）
先用 1-3 句话说明分析思路和关键公式，然后立即执行，不等用户确认。
示例："我会按店铺汇总销售额，计算公式：店铺销售额 = Σ(订单金额)，按降序排列。"然后执行 code_execute。

### 复杂多步请求（≥3步的分析流程）
先列出步骤计划，等用户确认后一次性执行全部步骤。
示例："分析方案：1. 合并两份订单明细 2. 关联体积表计算总体积 3. 按运营人员汇总生成报表。需要调整吗？"用户确认后再执行。

### 模糊请求
选最可能的理解，说明假设，执行后在结尾提供替代选项。不要连问多个问题，最多问 1 个关键澄清。

### 禁止行为（CRITICAL）
- NEVER 不调工具就回答业务数据问题——必须调 erp_agent 查数据再回答
- NEVER 说"我无法查看"——用你的工具查
- NEVER 反复尝试不同方式读取 Excel——Excel/二进制文件直接用 code_execute + WORKSPACE_DIR"""


def get_tool_system_prompt() -> str:
    """获取全局工具使用指引（注入到 ChatHandler 的系统提示词中）"""
    return TOOL_SYSTEM_PROMPT


# ============================================================
# 顶层工具 schema（始终在 messages 中）
# ============================================================

def _build_common_tools() -> List[Dict[str, Any]]:
    """构建通用工具（非 ERP 直接查询）"""
    return [
        {
            "type": "function",
            "function": {
                "name": "erp_agent",
                "description": (
                    "ERP 数据查询专员。用户问任何涉及以下内容时必须调用此工具：\n"
                    "订单/库存/采购/售后/发货/物流/商品/销量/统计/仓储/价格/成本/对账/核对/盘点/调拨/补发/换货/退款/退货金额。\n"
                    "口语/错别字映射：'丁单'=订单，'酷存'=库存，'够不够卖'=库存查询，"
                    "'到了没'=采购到货，'退了'=售后，'爆单'=销量统计，"
                    "'那个谁退的'=退货查询，'查一下呗'=数据查询。\n"
                    "含操作性词汇也要先查数据：'对账'先查数据再对，'对不对'先查数据再核对，"
                    "'处理'先查状态再建议，'优先处理'先查订单列表，'多少钱/价格'先查成本价。\n"
                    "内部自动识别编码、选择最优查询工具、多步查询，返回完整数据结论。"
                ),
                "parameters": {
                    "type": "object",
                    "required": ["query"],
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "必须原样复制用户的消息文本，禁止改写/翻译/补充/添加日期。错误示例：用户说'昨天付款订单'你写'查询2026-04-04付款的订单列表'。正确做法：直接复制'昨天付款订单'。",
                        },
                    },
                },
            },
        },
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

    # 文件操作工具
    tools.extend(build_file_tools())

    # 代码执行工具（主 Agent 版，含 WORKSPACE_DIR + 图表/文档能力）
    tools.extend(build_code_tools(include_workspace=True))

    # 通用工具（搜索、知识库、图片、视频 — 始终加载）
    tools.extend(_build_common_tools())

    return tools


# ============================================================
# 动态 Schema 注入（ToolSearch 模式）
# ============================================================

# 核心工具：每次请求都传给 LLM 的完整 schema
# ERP Agent 模式：主 Agent 只持有 7 个工具（erp_agent 封装了 17 个 ERP 工具）
# 主 Agent 只做 7 选 1 路由，ERP 的准确率由 erp_agent 内部保证
_CORE_TOOLS: Set[str] = {
    # Agent（封装复杂多步工具）
    "erp_agent",                # ERP 独立 Agent（内含 17 个 ERP 工具）
    # 搜索
    "erp_api_search",           # ERP 工具搜索（辅助）
    "search_knowledge",         # 知识库
    "web_search",               # 互联网搜索
    "social_crawler",           # 社交平台爬虫（小红书/抖音/B站/微博/知乎）
    # 生成
    "generate_image",           # 图片生成
    "generate_video",           # 视频生成
    # 执行
    "code_execute",             # 代码执行
    # 文件操作
    "file_read",                # 文件读取
    "file_write",               # 文件写入
    "file_list",                # 目录列表
    "file_search",              # 文件搜索
    "file_info",                # 文件信息
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
