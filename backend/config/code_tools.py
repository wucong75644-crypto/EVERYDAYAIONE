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


# Anthropic 官方推荐格式: WHAT / WHEN TO USE / WHEN NOT TO USE / OUTPUT / CAVEATS
# 来源: https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools
_DESCRIPTION = (
    "Python 计算与可视化沙盒(有状态,变量跨调用保留)。cwd=/workspace,执行超时 120 秒。\n"
    "预装 pandas/duckdb/matplotlib/plotly/altair/openpyxl/pdfplumber/python-docx 等。\n"
    "\n"
    "WHEN TO USE\n"
    "- 用户要图表/可视化(柱形图/折线图/饼图等) — 必须调,在脚本里用 emit_chart 输出\n"
    "- 用户要导出 Excel/CSV/PDF — 必须调,写文件后用 emit_file 出下载卡片\n"
    "- 用户要看数据表格 — 必须调,用 emit_table 渲染\n"
    "- 计算/统计/聚合/排序/筛选 — 必须调,用 SQL 或 pandas 算\n"
    "\n"
    "WHEN NOT TO USE\n"
    "- 用户只是闲聊或问概念解释,不需要计算或产出\n"
    "- 用户要求获取本地没有的远程数据(用 erp_agent / web_search / file_search)\n"
    "\n"
    "OUTPUT PROTOCOL — 想给用户看的内容必须调 emit_xxx,只 print 文字 = 用户看不到\n"
    "- emit_chart(option, title='')   ECharts 图表(option 完整 echarts 配置 dict)\n"
    "- emit_file(path, label=None)    文件下载卡片(写文件后调,没 emit = 丢)\n"
    "- emit_image(path)               静态图片(PNG/JPG)\n"
    "- emit_table(df, title='')       交互式表格(DataFrame 或 list[dict])\n"
    "- matplotlib plt.show() / plotly fig.show() / altair Chart 自动 emit,不用显式调\n"
    "\n"
    "PATHS (全部相对字符串)\n"
    "- 读用户上传: pd.read_excel('上传/2026-06/x.xlsx')    attachments 给 path 字段\n"
    "- 读 parquet: pd.read_parquet('staging/x.parquet')    attachments 给 parquet 字段\n"
    "- 读 ERP 结果: pd.read_parquet('staging/erp_xxx.parquet')\n"
    "- 写产物: df.to_excel('下载/x.xlsx') + emit_file('下载/x.xlsx')\n"
    "- 写缓存: df.to_parquet('staging/x.parquet')           跨调用复用,24h 自动清\n"
    "\n"
    "CAVEATS\n"
    "- DuckDB 方言: 中文列名双引号; 转日期 ts::DATE; 拼接 ||; DATE_TRUNC('month', ts)\n"
    "- 大数据(>10万行): SQL 聚合后 .df(),禁止 SELECT * .df() 全量加载\n"
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
