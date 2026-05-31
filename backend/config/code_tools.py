"""
代码执行沙盒工具定义

为 Agent Loop 提供 code_execute 工具定义。
由 agent_tools.py / chat_tools.py / phase_tools.py 导入。

架构隔离：
  build_code_tools()                    → ERP Agent 用（只有 STAGING_DIR + OUTPUT_DIR）
  build_code_tools(include_workspace=True) → 主 Agent 用（加 WORKSPACE_DIR + 图表/文档能力）
"""

from typing import Any, Dict, List, Set


# 代码执行工具名集合（INFO 类型：结果回传大脑）
CODE_INFO_TOOLS: Set[str] = {
    "code_execute",
}

# 工具 Schema（参数验证）
CODE_TOOL_SCHEMAS: Dict[str, Dict[str, Any]] = {
    "code_execute": {
        "required": ["code", "description"],
        "properties": {
            "code": {"type": "string"},
            "description": {"type": "string"},
        },
    },
}


# ============================================================
# 工具描述（两个版本，架构隔离）
# ============================================================

# ERP Agent 版（只有 STAGING_DIR + OUTPUT_DIR，不知道 WORKSPACE_DIR 的存在）
_DESCRIPTION_BASE = (
    "Python 沙盒，每次执行不保留变量。\n"
    "可用库: pd, duckdb, plt, Path, math, json, datetime, Decimal, Counter, io, "
    "os(受限: listdir/walk/stat/path), shutil(受限: copy/move)\n"
    "环境变量: STAGING_DIR, OUTPUT_DIR（自动上传）\n"
    "get_file('文件名') 是预定义函数，按文件名获取绝对路径（自动纠错）。\n"
    "示例：path = get_file('销售报表.xlsx'); duckdb.sql(f\"SELECT * FROM read_parquet('{path}')\")\n"
    "写 Excel 用 engine='xlsxwriter'。\n"
    "生成文件写到 OUTPUT_DIR，用 print() 输出文本。禁止 sys/subprocess。\n"
    "NEVER用中文标点写代码：逗号用 , 不用 ，；括号用 () 不用 （）；分号用 ; 不用 ；"
)

# 主 Agent 版（加 WORKSPACE_DIR + 完整文件探索能力）
_DESCRIPTION_WORKSPACE = (
    "有状态 Python 沙盒，变量跨调用保留。执行超时 120 秒。\n"
    "资源上限：内存 4GB（DuckDB 内部 3GB + Python/pandas 1GB），超出会 OOM kill。\n"
    "预装：duckdb(磁盘模式), openpyxl, pdfplumber, python-docx, pandas。\n"
    "get_file('文件名') 是预定义函数，所有文件引用都用它获取绝对路径（自动纠错）。\n"
    "数据文件已由 file_analyze 转为 Parquet，必须用 duckdb 读取，禁止 pd.read_excel。\n"
    "示例：path = get_file('销售报表.xlsx'); df = duckdb.sql(f\"SELECT * FROM read_parquet('{path}')\").df()\n"
    "大数据处理（>10万行）：用 SQL 聚合后 .df()，不要 SELECT * .df() 全量加载。\n"
    "  ✓ duckdb.sql(f\"SELECT 店铺, SUM(金额) FROM read_parquet('{p}') GROUP BY 店铺\").df()\n"
    "  ✗ duckdb.sql(f\"SELECT * FROM read_parquet('{p}')\").df()  ← 50万行 OOM\n"
    "OUTPUT_DIR 存输出文件，自动上传。\n"
    "数据文件（Excel/CSV）先调 file_analyze，再用 get_file + duckdb 查询。\n"
    "PDF 用 pdfplumber，DOCX 用 python-docx。\n"
    "图表用 ECharts JSON（.echart.json）。写 Excel 用 xlsxwriter。\n"
    "print() 输出摘要，不要输出完整数据。禁止 sys/subprocess。\n"
    "删除文件用 file_delete 工具，不要在沙盒内调 os.remove（已禁用）。\n"
    "NEVER用中文标点写代码：逗号用 , 不用 ，；括号用 () 不用 （）；分号用 ; 不用 ；"
)


def build_code_tools(
    include_workspace: bool = False,
) -> List[Dict[str, Any]]:
    """构建代码执行工具定义（1个 INFO 工具）

    架构隔离：
      include_workspace=False → ERP Agent（只知道 STAGING_DIR + OUTPUT_DIR）
      include_workspace=True  → 主 Agent（加 WORKSPACE_DIR + 图表/文档能力）
    """
    description = _DESCRIPTION_WORKSPACE if include_workspace else _DESCRIPTION_BASE

    return [
        {
            "type": "function",
            "function": {
                "name": "code_execute",
                "description": description,
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
                                "代码功能描述（一句话，如「统计各店铺今日成交额」），"
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
# 路由提示词（ERP Agent 用，不含 WORKSPACE_DIR）
# ============================================================

CODE_ROUTING_PROMPT = (
    "## code_execute 使用协议\n"
    "- code_execute 是计算沙盒，只能处理已获取的数据，不能查询数据\n"
    "- 数据获取必须先通过工具层完成（file_search / fetch_all_pages）\n"
    "- staging 数据用 duckdb.sql(\"SELECT ... FROM read_parquet(STAGING_DIR + '/文件名')\") 查询\n"
    "- 生成文件写到 OUTPUT_DIR 目录，平台自动检测上传，不需要手动上传\n"
    "- 图表用 ECharts JSON 配置输出：\n"
    "  import json\n"
    "  option = {\"title\": {\"text\": \"标题\"}, \"xAxis\": {...}, \"series\": [{...}]}\n"
    "  with open(OUTPUT_DIR + '/图表名.echart.json', 'w') as f:\n"
    "      json.dump(option, f, ensure_ascii=False)\n"
    "  ECharts option 规范: https://echarts.apache.org/en/option.html\n"
    "  不要用 plt / matplotlib，平台已替换为前端交互式图表\n"
    "- 典型流程：file_search 定位数据 → code_execute 查询计算 → "
    "df.to_excel(OUTPUT_DIR + '/报表.xlsx')\n"
    "- 顶层可直接 await，用 print() 输出文字\n\n"
    "## 图表选择参考（自动选择，不需要用户指定）\n"
    "根据数据特征自动选择最合适的图表：\n"
    "- 时间 + 数值 → line\n"
    "- 时间 + 多组数值 → multi-line（按类别分色）\n"
    "- 分类 + 数值 → bar（长标签用横向 bar）\n"
    "- 比例数据（≤6类）→ pie/donut\n"
    "- 两个数值变量 → scatter\n"
    "- 分布分析 → histogram / boxplot\n"
    "- 两个分类 + 数值 → heatmap / grouped bar\n"
    "- 层级分类 → treemap\n"
    "- 转化漏斗 → funnel\n"
    "- 多维评分 → radar\n\n"
    "禁止项：\n"
    "- 饼图不超过 6 个分类，超过改用 bar 并按值排序\n"
    "- 不用 3D 图表\n"
    "- 不用双 Y 轴，改用两个独立图表\n"
    "- 散点图 >5000 点改用 heatmap\n"
    "- 柱状图 Y 轴必须从 0 开始\n"
    "- 分类无自然顺序时按值降序排列\n\n"
    "## 数据查询\n"
    "- staging 数据用 duckdb.sql(\"SELECT ... FROM read_parquet(STAGING_DIR + '/文件名')\")\n"
    "- 中文列名用双引号包裹\n"
    "- 代码语法必须全英文半角标点：逗号 , 括号 () 分号 ; 冒号 :（中文标点 ，（）；：会导致 SQL 解析失败）\n"
    "- 分析大数据用 SQL 聚合筛选，不要 SELECT * 全量取出\n"
    "- 导出文件给用户用 code_execute：df.to_excel(OUTPUT_DIR + '/报表.xlsx')\n\n"
    "## 大批量操作\n"
    "- 单次 code_execute 限时 120 秒，超时会被终止\n"
    "- 处理大量文件或数据时：先统计总量 → 分批处理 → 累积结果\n"
    "- 有状态沙盒，变量跨调用保留，可在多次调用间累积中间结果\n\n"
    "## fetch_all_pages 使用协议\n"
    "- 全量翻页工具，包装任意 erp_* 远程查询工具，自动翻页拉全部数据\n"
    "- 仅用于本地数据库没有的数据（如物流轨迹）\n"
    "- 结果自动存 staging 文件（Parquet），返回文件路径\n"
    "- 使用前需先通过 erp_* 工具的两步协议确认参数格式\n\n"
)
