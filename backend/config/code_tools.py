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


# 行业标准:工具描述极简,不教业务流程
# AI 看 attachments 的 status 自主决策(raw 调 file_analyze,analyzed 用 parquet)
_DESCRIPTION = (
    "Python 沙盒 (有状态,变量跨调用保留)。沙盒 cwd=/workspace,所有路径用相对字符串。\n"
    "预装: pandas/duckdb/matplotlib/plotly/altair/openpyxl/pdfplumber/python-docx 等\n"
    "\n"
    "路径协议(全部相对):\n"
    "  读用户上传: pd.read_excel('上传/2026-06/x.xlsx')  ← attachments 给 path 字段\n"
    "  读 parquet: pd.read_parquet('staging/x.parquet')  ← attachments 给 parquet 字段\n"
    "  读 ERP 结果: pd.read_parquet('staging/erp_xxx.parquet')\n"
    "  写产物给用户: df.to_excel('下载/x.xlsx') 然后 emit_file('下载/x.xlsx')\n"
    "  写缓存: df.to_parquet('staging/x.parquet')        ← 跨调用复用,24h 自动清\n"
    "\n"
    "【产物输出协议 — 当你想给用户看的内容时必须调用】\n"
    "  emit_chart(option, title='')   ECharts 图表(option 完整 echarts 配置 dict)\n"
    "  emit_file(path, label=None)    文件下载卡片(写文件后调,没 emit 等于丢)\n"
    "  emit_image(path)               静态图片(PNG/JPG)\n"
    "  emit_table(df, title='')       交互式表格(传 DataFrame 或 list[dict])\n"
    "规则:\n"
    "  1. 不要让用户读 print,要 emit_xxx 让前端渲染卡片\n"
    "  2. df.to_excel/csv 后必须 emit_file 否则用户看不到下载\n"
    "  3. matplotlib plt.show() 自动 emit_image 不需要显式调\n"
    "  4. plotly fig.show() / altair Chart 自动 emit_chart 不需要显式调\n"
    "\n"
    "DuckDB SQL 方言: 中文列名用双引号; ts::DATE 不是 DATE(); 拼接 || 不是 +;\n"
    "  日期: DATE_TRUNC('month', ts); 类型: TIMESTAMP/BIGINT/DOUBLE/VARCHAR\n"
    "大数据(>10万行): SQL 聚合后 .df(),禁止 SELECT * .df() 全量加载\n"
    "导出 Excel: 用 engine='xlsxwriter',自动处理 NaN/Timestamp\n"
    "禁止: OUTPUT_DIR/STAGING_DIR/WORKSPACE_DIR 变量(已删);get_file()(已删);sys/subprocess\n"
    "代码语法全英文半角: 逗号 , 括号 () 分号 ; 冒号 :"
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
    "## code_execute 使用协议\n"
    "- code_execute 是计算沙盒,只能处理已获取的数据,不能查询数据\n"
    "- 数据获取必须先通过工具层完成(file_search / fetch_all_pages)\n"
    "- staging parquet 数据用 duckdb.sql(\"SELECT ... FROM 'staging/文件名.parquet'\")\n"
    "- 生成文件写到 '下载/文件名.xlsx',平台自动检测上传,不需要手动上传\n"
    "- 图表用 ECharts JSON 配置输出:\n"
    "  import json\n"
    "  option = {\"title\": {\"text\": \"标题\"}, \"xAxis\": {...}, \"series\": [{...}]}\n"
    "  with open('staging/图表名.echart.json', 'w') as f:\n"
    "      json.dump(option, f, ensure_ascii=False)\n"
    "  (中转数据,平台读完即删,不占用户下载目录)\n"
    "  ECharts option 规范: https://echarts.apache.org/en/option.html\n"
    "  不要用 plt / matplotlib,平台已替换为前端交互式图表\n"
    "- 典型流程: file_search 定位数据 → code_execute 查询计算 → "
    "df.to_excel('下载/报表.xlsx')\n"
    "- 顶层可直接 await,用 print() 输出文字\n\n"
    "## 图表选择参考(自动选择,不需要用户指定)\n"
    "根据数据特征自动选择最合适的图表:\n"
    "- 时间 + 数值 → line\n"
    "- 时间 + 多组数值 → multi-line(按类别分色)\n"
    "- 分类 + 数值 → bar(长标签用横向 bar)\n"
    "- 比例数据(≤6类)→ pie/donut\n"
    "- 两个数值变量 → scatter\n"
    "- 分布分析 → histogram / boxplot\n"
    "- 两个分类 + 数值 → heatmap / grouped bar\n"
    "- 层级分类 → treemap\n"
    "- 转化漏斗 → funnel\n"
    "- 多维评分 → radar\n\n"
    "禁止项:\n"
    "- 饼图不超过 6 个分类,超过改用 bar 并按值排序\n"
    "- 不用 3D 图表\n"
    "- 不用双 Y 轴,改用两个独立图表\n"
    "- 散点图 >5000 点改用 heatmap\n"
    "- 柱状图 Y 轴必须从 0 开始\n"
    "- 分类无自然顺序时按值降序排列\n\n"
    "## 数据查询\n"
    "- staging 数据用 duckdb.sql(\"SELECT ... FROM 'staging/文件名.parquet'\")\n"
    "- 中文列名用双引号包裹\n"
    "- 代码语法必须全英文半角标点: 逗号 , 括号 () 分号 ; 冒号 :(中文标点 ,();: 会导致 SQL 解析失败)\n"
    "- 分析大数据用 SQL 聚合筛选,不要 SELECT * 全量取出\n"
    "- 导出文件给用户用 code_execute: df.to_excel('下载/报表.xlsx')\n\n"
    "## 大批量操作\n"
    "- 单次 code_execute 限时 120 秒,超时会被终止\n"
    "- 处理大量文件或数据时: 先统计总量 → 分批处理 → 累积结果\n"
    "- 有状态沙盒,变量跨调用保留,可在多次调用间累积中间结果\n\n"
    "## fetch_all_pages 使用协议\n"
    "- 全量翻页工具,包装任意 erp_* 远程查询工具,自动翻页拉全部数据\n"
    "- 仅用于本地数据库没有的数据(如物流轨迹)\n"
    "- 结果自动存 staging 文件(Parquet),返回相对路径如 'staging/erp_xxx.parquet'\n"
    "- 使用前需先通过 erp_* 工具的两步协议确认参数格式\n\n"
)
