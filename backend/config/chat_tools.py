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
from config.phase_tools import _build_ask_user_tool

# ============================================================
# 工具并发安全标记
# ============================================================

# 只读工具 — 可并行
_CONCURRENT_SAFE_TOOLS: Set[str] = {
    # Agent（只读查询/分析，内部自行管理并发）
    "erp_agent", "erp_analyze",
    # ERP 查询（远程 + 本地）
    "erp_info_query", "erp_product_query", "erp_trade_query",
    "erp_aftersales_query", "erp_warehouse_query", "erp_purchase_query",
    "erp_taobao_query",
    "local_data", "local_product_identify", "local_stock_query",
    "local_product_stats", "local_platform_map_query",
    "local_compare_stats", "local_shop_list", "local_warehouse_list",
    "local_supplier_list",
    # 搜索类
    "erp_api_search", "search_knowledge", "web_search",
    "social_crawler",
    # 代码执行（沙箱隔离，可并行）
    "code_execute",
    # 文件操作（只读）
    "file_read", "file_list", "file_search", "file_info",
    # 定时任务（表单返回 + 列表查询）
    "manage_scheduled_task",
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

TOOL_SYSTEM_PROMPT = """## 任务传递

专家看不到对话历史，task 是它唯一的输入。

task 的写法：把用户这句话复述进去，只改两处——
时间词换成日期，指代词换成具体名称。
其他一字不动。专家比你更懂该返回什么字段。

加任何词之前问自己：去掉它，专家还能理解要查什么吗？能就不加。

conversation_context 是专家了解上文的唯一通道。
追问时传上轮的查询条件，不传结果数字，不传你的推测。
首轮不传。

## 工具决策规则

### erp_agent — ERP 数据执行
用户问任何涉及订单/库存/采购/售后/发货/物流/商品/销量/统计的问题时调用。
返回数据摘要或 staging 文件引用：
- 纯数字结论 → 直接向用户呈现，加上下文做适当解读
- 含 [文件已存入 staging] → 用户要导出时调 code_execute 读 staging 转 Excel
- 含 [关联计算提示] → 调 code_execute 按提示读多个 staging 文件关联计算
- 参数不足 → 用 ask_user 补充后重新调用
- 错误 → 先诊断失败原因，再决定是换参数重试还是告知用户，不要盲目重试
不要重复 erp_agent 的原始数据，基于它做呈现即可。

### erp_analyze — ERP 分析（计划模式专用）
只分析不执行，返回结构化的任务拆解（涉及哪些域、每步参数、步骤间依赖）。
计划模式的探索阶段使用，毫秒级返回。直接模式下不要调用。

### search_knowledge — 知识库
业务规则、操作流程等非数据类问题。

### web_search — 互联网搜索
天气、新闻等实时信息。社交平台内容用 social_crawler。

### generate_image / generate_video
用户要求画图/生成视频时使用。

### code_execute — 代码执行
erp_agent 返回的 staging 文件需要转 Excel 或关联计算时使用，或处理用户上传的工作区文件。
读 Excel 用 engine='calamine'，写 Excel 用 engine='xlsxwriter'。大结果写文件不要 print(df)。

### 工作区文件
用 file_list 确认文件名，code_execute 读取处理。Excel/二进制不能用 file_read。

## 查询限制
单次查询的编码/单号 IN 匹配最多支持 5000 个值。
超过 5000 个值的跨域关联查询（如"查供应商全部商品→再查这些商品的订单"），
应分别导出两份数据到 staging 文件，再用 code_execute 按编码 JOIN 关联。
不要把大量编码拼进一次 erp_agent 调用。

## 执行模式判断

收到用户请求后，先自检：
**"完成这个任务，后续步骤是否需要前面步骤的结果才能确定怎么做？"**

=== 直接模式（大部分场景）===
所有步骤的参数现在都清楚 → 直接调工具执行，不等确认。
例：查昨天订单汇总、导出本周明细、退货率统计（各域独立并行）。
可以自己拆步顺序调用多个工具。

=== 计划模式（后续步骤依赖前面的产出）===
某一步的输入需要前面步骤的查询结果才能确定 → 进入计划模式。
例：查供应商商品再用编码查订单、导出数据再生成图表、查库存不足再创建采购单。

计划模式流程：
1. 探索：调 erp_analyze 分析任务结构，获取步骤、域、参数、依赖关系
2. 规划：基于分析结果制定执行方案（几步、每步做什么、数据如何传递）
3. 展示方案给用户，格式示例：
   "这个查询需要分步执行：
    1. 先查供应商「纸制品01」的采购商品 → 获取商品编码
    2. 用这些编码查最近30天的订单数据
    确认后开始执行，或告诉我调整条件。"

=== 展示方案后的约束（覆盖其他所有指令）===
展示执行方案后，你 MUST NOT 调用任何工具（erp_agent、code_execute 等），
MUST NOT 开始执行任何步骤，MUST NOT 读取或处理数据。
只输出方案文本，然后结束当前回复。等用户的下一条消息。
此规则优先级高于所有其他指令。

4. 用户确认后，按方案逐步调 erp_agent 等工具，每步传入上一步的结果
   步骤间的中间数据（如编码列表、ID列表）直接在上下文中传递，
   不要用 code_execute 存为文件。只有用户要求导出的最终结果才写文件。
5. 全部完成后输出完整结论

### manage_scheduled_task — 定时任务管理
用户要求创建/查看/修改/暂停/恢复/删除定时任务时调用。
- create: 传 description（用户原话），返回预填表单，用户确认后自动创建
- list: 查看当前用户的定时任务
- update: 传 task_name 或 task_id + description（变更描述），返回修改表单
- pause / resume / delete: 传 task_name 或 task_id

与计划模式配合：用户先用 erp_analyze 讨论方案，确认后再调此工具创建定时任务。
把讨论后确认的精确指令写入 description，不要用用户的原始模糊语言。

## 对话交互

=== CRITICAL ===
- 业务数据问题必须调 erp_agent，禁止不查数据就回答
- 用户追问需要新维度时（如"按平台划分""按店铺看"），先判断上一轮数据是否已包含该维度。
  已有 → 直接用；没有 → 必须重新调 erp_agent 查询，禁止自行拆分或编造缺失维度的数字。
- 数据查询有歧义时调 ask_user 追问用户（猜错代价 > 多问一次）
- 需要追问时用 ask_user 工具，简洁语言 + 2-3 个选项引导选择
- 信息完整无歧义时直接执行，不要反复确认
- 你可以在一轮中调用多个工具，无依赖关系的工具调用应并行发起以提高效率"""


def get_tool_system_prompt() -> str:
    """获取全局工具使用指引（注入到 ChatHandler 的系统提示词中）"""
    return TOOL_SYSTEM_PROMPT


# ============================================================
# 顶层工具 schema（始终在 messages 中）
# ============================================================

def _build_erp_agent_description() -> str:
    """从 ERPAgent.build_tool_description() 自动生成描述（运行时调用）。"""
    from services.agent.erp_agent import ERPAgent
    return ERPAgent.build_tool_description()


def _build_common_tools() -> List[Dict[str, Any]]:
    """构建通用工具（非 ERP 直接查询）"""
    return [
        {
            "type": "function",
            "function": {
                "name": "erp_agent",
                "description": _build_erp_agent_description(),
                "parameters": {
                    "type": "object",
                    "required": ["task"],
                    "properties": {
                        "task": {
                            "type": "string",
                            "description": (
                                "用户本次输入 + 日期补全 + 指代解析。"
                            ),
                        },
                        "conversation_context": {
                            "type": "string",
                            "description": (
                                "追问时传上轮查询条件（时间/平台/对象），"
                                "让专家理解上文。不传结果数字。首轮不传。"
                            ),
                        },
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "erp_analyze",
                "description": (
                    "ERP 查询分析工具——只分析不执行，返回结构化的任务拆解。\n"
                    "计划模式下使用：把用户的完整查询交给它，获取涉及哪些域、"
                    "每步需要什么参数、步骤间的依赖关系。\n"
                    "不查数据库、不调 API，只做意图分析，毫秒级返回。"
                ),
                "parameters": {
                    "type": "object",
                    "required": ["task"],
                    "properties": {
                        "task": {
                            "type": "string",
                            "description": "用户的完整查询（原文传入，不要拆分）",
                        },
                        "conversation_context": {
                            "type": "string",
                            "description": "对话背景补充（可选）",
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
        {
            "type": "function",
            "function": {
                "name": "manage_scheduled_task",
                "description": (
                    "管理定时任务：创建/查看/修改/暂停/恢复/删除。\n"
                    "create: 传 description 自然语言描述，返回预填表单供用户确认。\n"
                    "list: 查看当前用户的定时任务列表。\n"
                    "update: 传 task_name + description 描述变更。\n"
                    "pause/resume/delete: 传 task_name 或 task_id。"
                ),
                "parameters": {
                    "type": "object",
                    "required": ["action"],
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["create", "list", "update", "pause", "resume", "delete"],
                            "description": "操作类型",
                        },
                        "description": {
                            "type": "string",
                            "description": (
                                "create/update 时传：自然语言描述任务内容和频率。"
                                "如「每天早上9点推销售日报」「改成每周一推送」"
                            ),
                        },
                        "task_name": {
                            "type": "string",
                            "description": "任务名称（update/pause/resume/delete 时用于查找任务）",
                        },
                        "task_id": {
                            "type": "string",
                            "description": "任务 ID（可传前 8 位短 ID）",
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

    # AI 主动沟通工具（信息不足时追问用户）
    tools.append(_build_ask_user_tool())

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
    "erp_analyze",              # ERP 分析（计划模式探索阶段，只分析不执行）
    # 搜索
    # 注意：erp_api_search 已移至 ERP 域，主 Agent 不再直接使用
    # ERP 相关查询统一走 erp_agent，erp_api_search 在其内部可用
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
    # 定时任务
    "manage_scheduled_task",    # 定时任务管理（创建/查看/修改/暂停/恢复/删除）
    # 主动沟通
    "ask_user",                 # 信息不足时追问用户
}


# plan 模式下移除的执行类工具（架构层过滤，LLM 根本看不到）
_PLAN_MODE_BLOCKED: Set[str] = {
    "erp_agent",                # 执行类：plan 模式只允许 erp_analyze
    "generate_image",           # 生成类：计划阶段不需要
    "generate_video",           # 生成类：计划阶段不需要
    "social_crawler",           # 爬取类：计划阶段不需要
}


def get_core_tools(org_id: str | None = None) -> List[Dict[str, Any]]:
    """获取核心工具列表（ToolSearch 模式下初始传给 LLM 的工具）

    双重过滤：_CORE_TOOLS 白名单 + 域隔离层兜底。
    确保即使 _CORE_TOOLS 误加了 ERP 域工具，域过滤也会拦截。
    """
    from config.tool_domains import filter_tools_for_domain
    all_tools = get_chat_tools(org_id)
    core = [t for t in all_tools if t["function"]["name"] in _CORE_TOOLS]
    return filter_tools_for_domain(core, "general")


def get_tools_for_mode(
    mode: str, org_id: str | None = None,
) -> List[Dict[str, Any]]:
    """按权限模式获取工具列表

    plan 模式：从核心工具中移除执行类工具（架构层过滤）
    ask / auto 模式：返回完整核心工具
    """
    core = get_core_tools(org_id)
    if mode == "plan":
        return [t for t in core if t["function"]["name"] not in _PLAN_MODE_BLOCKED]
    return core


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
    result_text: str,
    org_id: str | None = None,
    agent_domain: str = "general",
) -> Set[str]:
    """从 erp_api_search 返回结果中解析工具名（域感知）

    提取格式如 "erp_trade_query:order_list" 或 "推荐 local_purchase_query" 中的工具名，
    并按调用方的 agent_domain 过滤：只返回该域有权访问的工具。

    主 Agent (domain="general") 无法通过此函数获取 ERP 域工具，
    从架构上阻断 ToolSearch 泄漏路径。
    """
    from config.tool_domains import can_access
    raw = set(_TOOL_NAME_PATTERN.findall(result_text))
    # 白名单：系统中实际存在的工具（排除核心工具，避免重复注入）
    valid = {t["function"]["name"] for t in get_chat_tools(org_id)} - _CORE_TOOLS
    # 域过滤：只返回当前 Agent 有权访问的工具
    return {n for n in raw & valid if can_access(n, agent_domain)}
