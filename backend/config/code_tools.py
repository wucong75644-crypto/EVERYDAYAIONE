"""
代码执行工具定义（行业标准:极简 system + 纯状态 attachments + tools 字段)

历史:之前用 OUTPUT_DIR/STAGING_DIR 变量层 + get_file 函数 + 业务流程教学
现状:相对路径直接写,LLM 看 tools + attachments 自主决策

适用范围:
  build_code_tools()                    → ERP Agent 用
  build_code_tools(include_workspace=True) → 主 Agent 用(同样描述,LLM 自己根据 attachments 决策)
"""
from typing import Any, Dict, List, Set


# 代码执行工具名集合(INFO 类型:结果回传大脑)
CODE_INFO_TOOLS: Set[str] = {
    "code_execute",
}

# 工具 Schema(参数验证)
CODE_TOOL_SCHEMAS: Dict[str, Dict[str, Any]] = {
    "code_execute": {
        "required": ["code", "description"],
        "properties": {
            "code": {"type": "string"},
            "description": {"type": "string"},
        },
    },
}


# 提示词遵循 OpenAI Code Interpreter / AutoGen / E2B 行业措辞:
#   - "stateful Jupyter kernel" + "Variables persist across calls"
#   - 沙盒内函数用完整 Python 签名 + 显式否定 + 正反例(对位 OpenAI ace_tools 模式)
#   - 不堆陷阱清单(Anthropic 反 laundry list),用 1 条结构约束(verify-before-output)
# 来源: https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
_DESCRIPTION = (
    "Python 计算与可视化沙盒。底层是 stateful Jupyter kernel,cwd=/workspace,执行超时 120 秒。\n"
    "预装 pandas/duckdb/matplotlib/plotly/altair/openpyxl/pdfplumber/python-docx 等。\n"
    "\n"
    "STATEFUL KERNEL — 必读\n"
    "变量、import、DataFrame 跨次 code_execute 调用保留。每次调用相当于在同一 notebook\n"
    "里新建一个 cell。已加载的数据**不要重复 pd.read_parquet** — 直接引用上一次的变量名。\n"
    "retry 时优先复用已有变量,只有 NameError 才重新加载。\n"
    "  错误模式(触发 MemoryError): 每次都 df = pd.read_parquet('staging/x.parquet')\n"
    "  正确模式:\n"
    "    Call 1: df = pd.read_parquet('staging/x.parquet')\n"
    "    Call 2+: 直接用 df,不再 read_parquet\n"
    "\n"
    "WHEN TO USE\n"
    "- 用户要图表/可视化(柱形图/折线图/饼图等) — 必须调,在 code 里用 emit_chart 输出\n"
    "- 用户要导出 Excel/CSV/PDF — 必须调,写文件后用 emit_file 出下载卡片\n"
    "- 用户要看数据表格 — 必须调,用 emit_table 渲染\n"
    "- 计算/统计/聚合/排序/筛选 — 必须调,用 SQL 或 pandas 算\n"
    "\n"
    "WHEN NOT TO USE\n"
    "- 用户只是闲聊或问概念解释,不需要计算或产出\n"
    "- 用户要求获取本地没有的远程数据(用 erp_agent / web_search / file_search)\n"
    "\n"
    "SANDBOX HELPERS — 仅可在 code 参数里作为 Python 函数调用\n"
    "  以下是沙盒内置的 Python 函数(不是顶层工具),禁止用 function_call 直接调用,\n"
    "  否则系统报 Unknown sync tool 错误。\n"
    "  emit_chart(option: dict, title: str = '') -> None        # ECharts 图表\n"
    "  emit_file(path: str, label: str | None = None) -> None   # 文件下载卡片\n"
    "  emit_image(path: str) -> None                             # 静态图片(PNG/JPG)\n"
    "  emit_table(df: 'pandas.DataFrame', title: str = '') -> None  # 交互表格\n"
    "  matplotlib plt.show() / plotly fig.show() / altair Chart 自动 emit\n"
    "  正确: code_execute(code=\"emit_file('下载/x.xlsx', label='月报')\")\n"
    "  错误: 直接对 emit_file 发起 function_call (会报 Unknown sync tool)\n"
    "\n"
    "PATHS — 全部相对字符串\n"
    "- 读用户上传: pd.read_excel('上传/2026-06/x.xlsx')    attachments 给 path 字段\n"
    "- 读 parquet: pd.read_parquet('staging/x.parquet')    attachments 给 parquet 字段\n"
    "- 读 ERP 结果: pd.read_parquet('staging/erp_xxx.parquet')\n"
    "- 写产物: df.to_excel('下载/x.xlsx') + emit_file('下载/x.xlsx')\n"
    "- 写缓存: df.to_parquet('staging/x.parquet')           跨调用复用,24h 自动清\n"
    "\n"
    "VERIFY BEFORE ACCESS\n"
    "  merge/groupby/pivot/rename 后,**必须先 print(df.columns.tolist())**\n"
    "  再访问列名 —— 这些操作可能改名(如 merge 同名列加 _x/_y 后缀)。\n"
    "\n"
    "CAVEATS\n"
    "- DuckDB 方言: 中文列名双引号; 转日期 ts::DATE; 拼接 ||; DATE_TRUNC('month', ts)\n"
    "- Excel 导出: engine='xlsxwriter',自动处理 NaN/Timestamp\n"
    "- 代码语法全英文半角(中文 ,();: 会让 SQL 解析失败)\n"
    "- 无网络 / 禁止 sys/subprocess / 删文件用 file_delete 工具"
)


def build_code_tools(
    include_workspace: bool = False,
) -> List[Dict[str, Any]]:
    """构建 code_execute 工具定义(行业标准 Function Calling 格式)。

    include_workspace 参数保留以兼容历史 API,新协议下两个版本描述相同。
    """
    return [
        {
            "type": "function",
            "function": {
                "name": "code_execute",
                "description": _DESCRIPTION,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "code": {
                            "type": "string",
                            "description": (
                                "Python 代码。顶层可直接 await 调用异步函数。"
                                "用 print() 输出最终结果。"
                            ),
                        },
                        "description": {
                            "type": "string",
                            "description": (
                                "代码功能描述(一句话,如「统计各店铺今日成交额」),"
                                "用于执行日志审计。"
                            ),
                        },
                    },
                    "required": ["code", "description"],
                },
            },
        },
    ]


# ============================================================
# 路由提示词(ERP Agent 用)
# ============================================================

CODE_ROUTING_PROMPT = (
    "## 工作流\n"
    "- code_execute 只算数据,不取数据(取数据用 file_search / fetch_all_pages / erp_*)\n"
    "- 典型流程: 取数据 → code_execute 算 → emit_chart/emit_file/emit_table 给用户看\n"
    "- 完整 API/路径/CAVEATS 见 code_execute 工具 description,不重复约定\n"
    "\n"
    "## 图表选择(数据特征自动定型,不问用户)\n"
    "- 时间+数值 → line  | 时间+多组 → multi-line  | 分类+数值 → bar(长标签横向)\n"
    "- 占比≤6类 → pie/donut(超 6 类改 bar)  | 两数值 → scatter(>5000 点改 heatmap)\n"
    "- 分布 → histogram/boxplot  | 两分类+值 → heatmap/grouped bar  | 层级 → treemap\n"
    "- 漏斗 → funnel  | 多维评分 → radar\n"
    "- 不用 3D / 双 Y 轴(改两个独立图)/ Y 轴必须从 0 开始 / 无序分类按值降序\n"
    "\n"
    "## fetch_all_pages\n"
    "- 包装 erp_* 远程查询自动翻页,只用于本地 DB 没有的数据(如物流轨迹)\n"
    "- 结果自动落 staging/erp_xxx.parquet,在 code_execute 里 duckdb 直接读\n"
    "- 用前先按 erp_* 工具的两步协议确认参数格式\n"
)
