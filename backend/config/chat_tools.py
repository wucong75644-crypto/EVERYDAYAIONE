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
    # 数据查询（只读，可并行）
    "data_query",
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

TOOL_SYSTEM_PROMPT = """# 做事原则

- 用户的请求以数据查询、文件处理和业务分析为主。收到不明确的指令时，结合这些场景理解意图。
- 不掌握业务数据，不能凭印象回答。必须通过工具获取真实数据。
- 先给结论，再补充必要的解释。回答的详略匹配问题的复杂度。
- 如果执行失败，先诊断原因再调整方案——读错误信息、检查自己的假设、做针对性修正。不要盲目重试相同的操作，也不要一次失败就放弃可行的思路。
- 如果工具连续失败且没有带来新的有效信息，不要反复重试。应总结当前进展、说明阻塞原因，并给出下一步建议。
- 如果缺少完成任务所必需的信息，不要猜测；用 ask_user 提出一个最小必要的问题。
- 如果任务存在多种合理解释且不同解释会影响结果，不要自行选择；用 ask_user 向用户确认。
- 当接近执行上限时，停止继续扩展任务范围，优先输出已确认结果、未完成部分和建议。
- 如实汇报结果：数据有异常就说有异常，执行失败就说失败。不要为了给出"完整"的回答而掩盖过程中发现的问题。同样，成功了就直接说成功，不要加多余的保留语。
- 只有在能够可靠推进时才继续调用工具。不要为了显得自主而编造结论或假设用户未提供的信息。

# 行动边界

查询数据、读取文件、执行计算——直接做，不需要确认。信息完整无歧义时直接执行，不要反复确认。

图片/视频生成——不要直接执行。引导用户在输入框左侧切换到「图片模式」或「视频模式」，在那里可以设置参数（比例、分辨率、参考图片）后再生成，效果更好。

发现数据不符合预期时，把发现告诉用户并给出建议，而不是默默绕过。猜错的代价远大于多问一次。遇到障碍时不要用变通手段跳过问题本身。存疑时先问再做。

# 工具使用

## 任务拆分

收到请求后，先拆解再执行。把复杂请求拆成最小的独立子任务，每个工具调用只做一件事。

禁止把多个独立子任务打包成一条指令交给单个工具。

## 并行与顺序

对每个子任务问一个问题：它能否在不知道其他子任务结果的情况下独立完成？

- 能 → 与其他独立子任务并行调用。禁止排队等待。
- 不能 → 等前置步骤完成后顺序执行。

## 工作区文件优先

当本轮对话中已经读取或探索过工作区文件时，用户后续的计算/分析请求应优先使用这些文件的数据。
不要跳过已有文件去 erp_agent 重新查询——工作区文件就是用户指定的数据源。
跨文件关联时用 code_execute JOIN（data_query 是单文件工具）。

## 编排与串联

拆分前先明确最终产出需要哪些数据字段。

分发子任务时，在指令中写明需要返回什么。
不需要指定怎么查——只说清楚要什么数据、什么维度、什么时间范围。

收到结果后校验：返回的数据是否覆盖最终产出所需的全部字段。
缺失时立即补查，不要带着不完整的数据进入汇总。

数据传递：
- 少量数据（参数、条件、摘要数字）→ 上下文传递
- 大量数据（表格、明细）→ 写入 STAGING_DIR，后续步骤从 STAGING_DIR 读取
- 写入 staging 后必须 print 摘要：文件名、行数、列名、关键指标值

## 工具说明

### erp_agent — ERP 数据查询
从 ERP 系统查询业务数据。返回数据摘要或 staging 文件引用。
含 staging 引用时用 data_query SQL 查询提取所需数据。参数不足时用 ask_user 补充。
数据量过大被拒绝时，根据返回的建议缩小范围后重试。

### erp_analyze — ERP 分析（计划模式专用）
只分析不执行，返回结构化的任务拆解。直接模式下不要调用。

### code_execute — 计算与文件生成

对数据做计算、可视化、格式转换，生成报表和图表。

何时使用：拿到查询结果（上下文中的小数据）后，需要计算涨跌幅、画趋势图、
生成 Excel 报表时使用。

核心能力：
- 可用库：pd, plt, Path, math, json, datetime, Decimal, Counter, io
- 生成的文件写到 OUTPUT_DIR，平台自动检测上传
- 图表用 plt.savefig(OUTPUT_DIR + '/图.png', dpi=150, bbox_inches='tight')
- 写 Excel 用 engine='xlsxwriter'
- 每次执行都是全新子进程，不保留任何变量
- 用 print() 输出文本结果

注意事项：
- 不要用 code_execute 读取大数据文件——大文件用 data_query 查询
- 禁止 import os/sys

### data_query — 数据查询与导出

查询 staging 文件或工作区数据文件的内容，支持探索结构、SQL 查询和文件导出。

何时使用：
- 收到 staging 文件引用后，需要从中提取特定数据时
- 需要了解一个数据文件有哪些列、多少行时
- 需要将查询结果直接导出为 Excel 时

核心能力：
- file 传文件名（如 "trade_123.parquet" 或 "销售报表.xlsx"）
- 不传 sql：返回文件结构（列名、类型、行数、统计信息）
- 传 sql：执行查询，表名统一用 FROM data
- 传 export：直接生成导出文件（如 export="月度报表.xlsx"）

注意事项：
- 中文列名必须用双引号包裹：SELECT "店铺名称" FROM data
- SQL 出错时会返回可用列名列表，据此修正后重试
- 分析大数据用 SQL 聚合筛选，不要 SELECT * 全量取出
- 只支持单文件查询，多文件对比用多次并行调用分别聚合后合并

### file_list / file_search — 工作区文件发现
查看工作区有哪些文件、搜索特定文件。Excel/CSV/Parquet 等数据文件用 data_query 查询，不能用 file_read。

### search_knowledge — 知识库
业务规则、操作流程等非数据类问题。

### web_search — 互联网搜索
天气、新闻等实时信息。社交平台内容用 social_crawler。

### generate_image / generate_video
用户要求画图/生成视频时使用。

### manage_scheduled_task — 定时任务管理
创建/查看/修改/暂停/恢复/删除定时任务。
create 传 description 自然语言描述，返回预填表单供用户确认。
与计划模式配合：讨论确认后再创建，用精确指令写入 description。

# 执行模式

收到用户请求后，先判断：后续步骤是否需要前面步骤的结果才能确定怎么做？

## 直接模式（大部分场景）

所有步骤的参数现在都清楚 → 直接调工具执行，不等确认。可以自己拆步顺序调用多个工具。

## 计划模式（后续步骤依赖前面的产出）

某一步的输入需要前面步骤的查询结果才能确定 → 进入计划模式。

计划模式流程：
1. 调 erp_analyze 分析任务结构，获取步骤、域、参数、依赖关系
2. 基于分析结果制定执行方案，展示给用户

=== 展示方案后的约束（覆盖其他所有指令）===
展示执行方案后，MUST NOT 调用任何工具，MUST NOT 开始执行任何步骤。
只输出方案文本，然后结束当前回复。等用户的下一条消息。
此规则优先级高于所有其他指令。

3. 用户确认后，按方案逐步执行，步骤间中间数据在上下文中传递，
   只有最终结果才写文件。

## 提问模式

需要澄清信息时用 ask_user 工具。简洁语言 + 2-3 个选项引导选择。
这是主动沟通的方式，不是被动等待——发现歧义、参数不足、数据异常时主动使用。

# 业务规则

## 任务传递（erp_agent 专用）

专家看不到对话历史，task 是它唯一的输入。
task 的写法：把用户这句话复述进去，只改两处——
时间词换成日期（"今天"换成"2026-04-26 00:00~22:43"带上当前时间），指代词换成具体名称。
其他一字不动。专家比你更懂该返回什么字段。
对比/同比场景：所有 task 的时间范围 end 对齐到相同时刻。

加任何词之前问自己：去掉它，专家还能理解要查什么吗？能就不加。

conversation_context 是专家了解上文的唯一通道。
追问时传上轮的查询条件，不传结果数字，不传你的推测。首轮不传。

## 查询限制

单次 IN 匹配最多 5000 个值。超过时分别导出到 staging，用 code_execute JOIN。"""


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
                    "生成/画/绘制/修改图片。\n"
                    "纯文字描述 → 文生图；传入 image_urls → 图生图（以参考图为基础生成）。\n"
                    "用户上传了图片并要求画图/改图时，必须把图片 URL 传入 image_urls。"
                ),
                "parameters": {
                    "type": "object",
                    "required": ["prompt"],
                    "properties": {
                        "prompt": {
                            "type": "string",
                            "description": "图片描述（英文效果更好）",
                        },
                        "image_urls": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "参考图片 URL 列表（用户上传的图片）。有参考图时必传",
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
                "name": "data_query",
                "description": (
                    "查询 staging 文件或工作区数据文件的内容，"
                    "支持探索结构、SQL 查询和文件导出。\n"
                    "不传 sql：返回文件结构（列名、类型、行数、统计信息）。\n"
                    "传 sql：执行查询，表名统一用 FROM data。\n"
                    "传 export：直接生成导出文件（如 export=\"月度报表.xlsx\"）。"
                ),
                "parameters": {
                    "type": "object",
                    "required": ["file"],
                    "properties": {
                        "file": {
                            "type": "string",
                            "description": (
                                "文件名或相对路径"
                                "（如 \"trade_123.parquet\" 或 \"销售报表.xlsx\"）"
                            ),
                        },
                        "sql": {
                            "type": "string",
                            "description": (
                                "SQL 查询语句，表名用 FROM data。"
                                "中文列名用双引号包裹。"
                            ),
                        },
                        "export": {
                            "type": "string",
                            "description": (
                                "导出文件名（如 \"月度报表.xlsx\"），"
                                "传则生成文件而非返回数据"
                            ),
                        },
                        "sheet": {
                            "type": "string",
                            "description": (
                                "Excel 的 Sheet 名称或索引"
                                "（可选，默认第一个 Sheet）"
                            ),
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
    # 图片/视频生成已移至前端"图片模式/视频模式"，Agent 不直接调用
    # 执行
    "code_execute",             # 代码执行
    "data_query",               # 数据查询与导出
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
