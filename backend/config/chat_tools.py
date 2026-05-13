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

from config.common_tools import build_common_tools
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
    # data_query 已合并到 file_read
    # 代码执行（沙箱隔离，可并行）
    "code_execute",
    # 文件操作（只读）
    "file_read", "file_search",
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
    "image_agent": SafetyLevel.CONFIRM,
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
- 工具返回的标识符（列名、文件名、字段名、编码等）必须原样复制，禁止修改任何字符。不要加减空格、不要纠正拼写、不要替换符号。

# 行动边界

收到用户请求后，先判断是否需要确认方案：

直接执行——同时满足以下全部条件时，不需要确认：
- 做法只有一种，不需要你做判断
- 用户的指令已经足够具体，不存在理解偏差
- 执行结果不符合预期时，重新做的成本很低

先确认再执行——以下任一条件成立时，先展示方案再动手：
- 存在多种合理做法，你需要替用户选择
- 用户的描述留有理解空间，不同理解会导致不同结果
- 任务涉及多个步骤，中间步骤的方向取决于你的判断
- 执行成本较高（耗时长、消耗积分多），做错了重来代价大

确认方式：用 2-3 句话说明你的理解和打算怎么做，然后停下来等用户回复。
MUST NOT 在确认前调用任何执行类工具。
不确定该不该确认时，确认——对齐成本远低于返工成本。

图片/视频生成——不要直接执行。引导用户在输入框左侧切换到「图片模式」或「视频模式」，在那里可以设置参数（比例、分辨率、参考图片）后再生成，效果更好。

发现数据不符合预期时，区分两种情况：
- 数据质量问题（格式不一致、前后空格、大小写、编码差异等）→ 先自主诊断和修正，修正后汇报结果
- 业务歧义（不确定用哪个字段、不确定数据口径、多种合理解释）→ 停下来用 ask_user 问用户
遇到障碍时不要用变通手段跳过问题本身。

# 工具使用

## 任务拆分

收到请求后，先拆解再执行。把复杂请求拆成最小的独立子任务，每个工具调用只做一件事。

禁止把多个独立子任务打包成一条指令交给单个工具。

## 并行与顺序

对每个子任务问一个问题：它能否在不知道其他子任务结果的情况下独立完成？

- 能 → 与其他独立子任务并行调用。禁止排队等待。
- 不能 → 等前置步骤完成后顺序执行。

## 数据来源判断

用户的请求涉及数据分析时，先判断所需数据的来源：
- 本轮对话中已有数据（工作区文件、已查询结果、staging 缓存）能满足需求 → 直接使用
- 已有数据明显不包含所需内容（如文件是利润表但用户问物流轨迹）→ 从外部获取
- 不确定已有数据是否满足 → 用 ask_user 向用户确认，不要自行决定

用户上传了文件或提及工作区文件后说"帮我分析"，指的是分析这些文件的数据。
file_search 定位文件后自动转 Parquet 存入 STAGING_DIR 并返回可直接使用的查询语句，在 code_execute 中复制执行即可。

## 编排与串联

拆分前先明确最终产出需要哪些数据字段。
分发子任务时写明需要返回什么，不需要指定怎么查。
收到结果后校验：数据是否覆盖最终产出所需的全部字段。缺失时立即补查。
不要在回复中贴大量数据行，数据量大时导出文件。

## 工具说明

### erp_agent — ERP 数据查询专员
从 ERP 系统查询订单/库存/采购/售后/商品/物流等全量业务数据。
支持统计聚合（summary）和明细导出（export），数据自动存 staging 文件。
支持并行调用（多个独立查询可同时发起）。

返回两种形式：
- summary：直接返回统计数字（总量/金额/分组明细），内联到回复
- export：数据存 staging parquet + profile 摘要（行数/字段/前3行预览）
  含 staging 引用时在 code_execute 中用 duckdb.sql() 查询

错误处理：
- 无数据：转述返回的建议（扩大时间范围/检查平台名）
- 数据量过大被拒绝：按返回的建议缩小范围后重试
- 参数不足：用 ask_user 向用户补充关键信息

### erp_analyze — ERP 分析（计划模式专用）
只分析不执行，返回结构化的任务拆解。直接模式下不要调用。

### code_execute — Python 计算环境

有状态沙盒，变量跨调用保留。执行超时 120 秒。
预装 duckdb(磁盘模式)、openpyxl、pdfplumber、python-docx、pandas。
三个目录：WORKSPACE_DIR（用户文件）、STAGING_DIR（数据缓存）、OUTPUT_DIR（输出，自动上传）。
file_search 会把数据文件转为 Parquet 存 STAGING_DIR，并返回可直接复制的 duckdb.sql() 查询语句。
DuckDB SQL 中列名用双引号包裹。
图表用 ECharts JSON（.echart.json），不要用 matplotlib。
print() 输出摘要统计，不要输出完整数据。无网络。禁止 sys/subprocess。
删除文件：先 ask_user 确认 → 用户同意后调 code_execute 并在 confirm_delete 参数传入文件路径（如 "下载/a.xlsx"），代码执行成功后自动删除。沙盒内 os.remove 被禁用。

### file_search — 工作区文件发现与准备
搜索、列目录、定位文件。数据文件（Excel/CSV）自动转 Parquet 存 staging。
返回文件列表和 staging 路径，后续在 code_execute 中用 duckdb 查询。

### file_read — 图片视觉分析
仅用于图片文件（png/jpg/gif/webp/bmp/svg），返回图片给视觉模型分析。
其他文件类型在 code_execute 中直接读取（openpyxl/pdfplumber/docx/open）。

### restore_file — 恢复文件
恢复 workspace 文件到修改前的版本。备份有效期 24 小时。

### search_knowledge — 知识库搜索
查找企业内部业务规则、SOP、操作流程、培训文档、历史经验。
基于语义检索，传自然语言问题比关键词效果更好。
不查数据（数据用 erp_agent），不查实时信息（用 web_search）。

### web_search — 互联网搜索
获取实时公开信息：天气、新闻、政策法规、行业资讯、技术文档。
企业内部数据用 erp_agent，社交平台帖子用 social_crawler。

### generate_image — 通用图片生成
非电商场景的图片生成：插画、概念图、logo、创意图、头像等。
纯文字→文生图，有参考图→图生图（必须传 image_urls）。
电商商品图（白底主图、场景图）→ 用 image_agent，效果更专业。

### generate_video — 视频生成
根据文字描述异步生成短视频，返回 task_id，完成后自动推送。
生成通常需要 1-3 分钟。不支持视频编辑/剪辑。

### manage_scheduled_task — 定时任务管理
创建/查看/修改/暂停/恢复/删除定时任务。
create 传 description 描述任务内容和频率，返回表单供用户确认。

# 执行模式

收到用户请求后，先判断：后续步骤是否需要前面步骤的结果才能确定怎么做？

## 直接模式

经过行动边界判断后确定可以直接执行 → 调工具执行。可以自己拆步顺序调用多个工具。

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

单次 IN 匹配最多 5000 个值。超过时分别导出到 staging，用 code_execute JOIN。

## 电商图片生成（image_agent）

为电商平台生成专业商品图片：白底主图、场景氛围图、详情页卖点图、SKU 展示图等。
底层调用 AI 图片模型（文生图/图生图），自动应用四层提示词（角色→品类→平台→风格），
输出符合各平台规范的商品图片。每次调用生成 1 张图片，返回 CDN 图片 URL，前端自动展示。

### 调用时机
- 消息中包含 image_task_meta 时：按 images[] 数组逐项调用
- 每次传入 images[i].description 作为 task 参数
- 每张生成后简短确认（如"白底主图已完成"），立即继续下一张
- 最后一张完成后输出整体总结

### 参数说明
- task（必传）：单张图的完整描述。格式：`图片类型 尺寸：主体+背景+光线+构图`
  示例：`白底主图 800×800：运动鞋居中，纯白背景，柔光箱45度布光，自然底部投影`
  示例：`场景图 750×950：咖啡杯置于原木桌面，背景虚化书房场景，暖色侧逆光`
- platform（可选）：目标电商平台，决定尺寸裁切规范。
  可选值：taobao / tmall / jd / pdd / douyin / xiaohongshu，默认 taobao
- 不需要传 image_urls — 系统自动注入用户上传的图片
- 不需要传 style — 系统自动从会话读取全局风格指令

### 返回格式
- 成功：`{"image_url": "https://..."}` — 前端自动渲染，不要重复描述图片内容
- 失败：裂开占位符 + 重试按钮 — 用户可点击重新生成，无需你额外处理

### 错误处理
- 生成超时/模型失败：内置自动重试，无需额外处理
- 积分不足：返回文字提示（不生成占位符），你只需转述提示即可
- 参数格式错误：返回具体错误信息，根据提示修正 task 后重试

### 不要用于（分界规则）
- 非电商场景的画图（画一只猫、生成 logo、插画、概念图）→ 用 generate_image
- 查询商品数据、订单、库存等 ERP 信息 → 用 erp_agent
- 修改已有图片的局部内容（抠图、换背景）→ 当前不支持，告知用户"""


def get_tool_system_prompt() -> str:
    """获取全局工具使用指引（注入到 ChatHandler 的系统提示词中）"""
    return TOOL_SYSTEM_PROMPT


# 通用工具 schema：已拆分到 common_tools.py


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
    tools.extend(build_common_tools())

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
    "image_agent",              # 电商图片生成（单张，电商图模式下使用）
    # 执行
    "code_execute",             # 代码执行
    # 文件操作
    "file_search",              # 文件搜索+准备（数据文件自动转 Parquet）
    "file_read",                # 图片视觉分析
    "restore_file",             # 恢复文件到修改前版本
    # 定时任务
    "manage_scheduled_task",    # 定时任务管理（创建/查看/修改/暂停/恢复/删除）
    # 主动沟通
    "ask_user",                 # 信息不足时追问用户
}


# plan 模式下移除的执行类工具（架构层过滤，LLM 根本看不到）
_PLAN_MODE_BLOCKED: Set[str] = {
    "erp_agent",                # 执行类：plan 模式只允许 erp_analyze
    "image_agent",              # 生成类：计划阶段不执行
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
