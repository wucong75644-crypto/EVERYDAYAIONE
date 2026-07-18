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
    "预装 pandas/duckdb/openpyxl/pdfplumber/python-docx 等。\n"
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
    "- 用户要数据图表(柱形图/折线图/饼图等) — 必须调,用 emit_chart 输出 ECharts option\n"
    "- 用户要流程图/时序图/状态图/类图/系统关系图/甘特图 — 必须调,用 emit_diagram 输出 Mermaid DSL\n"
    "- 用户要导出 Excel/CSV/PDF — 必须调,写文件后用 emit_file 出下载卡片\n"
    "- 用户要看数据表格 — 必须调,用 emit_table 渲染\n"
    "- 计算/统计/聚合/排序/筛选 — 必须调,用 SQL 或 pandas 算\n"
    "\n"
    "WHEN NOT TO USE\n"
    "- 用户只是闲聊或问概念解释,不需要计算或产出\n"
    "- 用户要求获取本地没有的远程数据(用 erp_agent / web_search / file_search)\n"
    "\n"
    "GRAPHICS — 职责边界\n"
    "数值、趋势、统计、分类比较和占比 → emit_chart(option, title='') 输出 ECharts。\n"
    "流程、状态、调用关系、时序、类和系统关系 → emit_diagram(source, title='', format='mermaid')。\n"
    "普通文字能清楚表达时不生成图形；同一内容禁止同时生成 chart 和 diagram。\n"
    "禁止用 Mermaid 做业务统计图，禁止用 ECharts 做时序图、状态图或类图。\n"
    "Plotly/Vega-Lite 仅供历史消息读取，新消息不得生成。\n"
    "\n"
    "数据图表范本(直接抄,改字段):\n"
    "  emit_chart({\n"
    "    'xAxis': {'type': 'category', 'data': labels},\n"
    "    'yAxis': {'type': 'value'},\n"
    "    'series': [{'type': 'bar', 'data': values}],\n"
    "  }, title='2026-06-20 各平台订单数')\n"
    "\n"
    "关系图范本:\n"
    "  emit_diagram('flowchart TD\\nA[接单] --> B[审核]', title='订单处理流程')\n"
    "\n"
    "主题/字体/尺寸前端已注入,不要在 layout 里自定义 colorway/template/font/width/height。\n"
    "\n"
    "FILE / TABLE / IMAGE — 其他产物渲染\n"
    "  A. 写到 '下载/' 目录的任何文件 → 自动检测 + 上传 OSS + 前端下载卡片/预览\n"
    "     例: df.to_excel('下载/x.xlsx')  # 不需显式调 emit\n"
    "  B. cell 末尾的表达式 → 自动 display 富表示(Jupyter 风格)\n"
    "     例: df.head()  # 表格自动渲染\n"
    "  C. 显式调 emit_* (推荐: 可自定义 title/label):\n"
    "     emit_chart(option: dict, title: str = '') -> None       # ECharts 数据图表\n"
    "     emit_diagram(source: str, title: str = '', format: str = 'mermaid') -> None\n"
    "     emit_file(path: str, label: str | None = None) -> None  # 下载卡片\n"
    "     emit_table(df, title: str = '') -> None                 # 交互表格\n"
    "     emit_image(path: str, alt: str = '') -> None            # 静态图片(如 matplotlib 出的 PNG)\n"
    "  错误: 直接对 emit_xxx 发起 function_call (会报 Unknown sync tool)\n"
    "\n"
    "PATHS — 全部相对字符串\n"
    "- 读用户上传: pd.read_excel('上传/2026-06/x.xlsx')    attachments 给 path 字段\n"
    "- 读 parquet: pd.read_parquet('staging/x.parquet')    attachments 给 parquet 字段\n"
    "- 读 ERP 结果: pd.read_parquet('staging/erp_xxx.parquet')\n"
    "- 写产物: df.to_excel('下载/x.xlsx') + emit_file('下载/x.xlsx')\n"
    "- 写缓存: df.to_parquet('staging/x.parquet')           跨调用复用,24h 自动清\n"
    "⚠ attachments 的 path/parquet 字段是字面字符串,**完整 copy 不要修改任何字符**\n"
    "  (尤其中英文混排的文件名:'4月销售-按订单' 不要美化为 '4 月销售 - 按订单',\n"
    "   文件名带空格/连字符也保留原样,否则路径不存在报 IOException)\n"
    "\n"
    "VERIFY BEFORE ACCESS\n"
    "  merge/groupby/pivot/rename 后,**必须先 print(df.columns.tolist())**\n"
    "  再访问列名 —— 这些操作可能改名(如 merge 同名列加 _x/_y 后缀)。\n"
    "\n"
    "CLEAN DATA\n"
    "  ragged 列(金额+%混合)已在 file_analyze 阶段预清洗为 {列名}_num float 列。\n"
    "  schema 里看到 X_num 时直接用,无需任何转换:df['X_num'].sum() 像普通 float 列。\n"
    "  日期混乱列同样预清洗为 {列名}_date datetime 列。\n"
    "  极少数 AI 漏标场景需现场清洗时,用 pandas 标准方法:\n"
    "    pd.to_numeric(df['x'].str.rstrip('%').str.replace(',',''), errors='coerce')\n"
    "\n"
    "CAVEATS\n"
    "- DuckDB 方言: 中文列名双引号; 转日期 ts::DATE; 拼接 ||; DATE_TRUNC('month', ts)\n"
    "- Excel 导出: engine='xlsxwriter',自动处理 NaN/Timestamp\n"
    "- 代码语法全英文半角(中文 ,();: 会让 SQL 解析失败)\n"
    "- 大数据(>10 万行): 用 SQL `GROUP BY ... LIMIT` 聚合后 `.df()` 拿小结果,\n"
    "  禁止 `SELECT * FROM 'big.parquet'` 全量加载(会爆 4GB cgroup OOM)\n"
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
    "- 典型流程: 取数据 → code_execute 算 → emit_chart/emit_diagram/emit_file/emit_table 给用户看\n"
    "- 完整 API/路径/CAVEATS 见 code_execute 工具 description,不重复约定\n"
    "\n"
    "## 图形选择\n"
    "数值/趋势/统计/比较/占比 → emit_chart(ECharts option)。\n"
    "流程/状态/调用关系/时序/类图/系统关系/甘特图 → emit_diagram(Mermaid source)。\n"
    "普通文字足够清楚时不生成图形；同一内容禁止重复生成两种图形。\n"
    "新消息禁止生成 Plotly/Vega-Lite，它们只保留历史读取兼容。\n"
    "Y 轴必须从 0 开始,无序分类按值降序,不用 3D / 双 Y 轴。\n"
    "\n"
    "## fetch_all_pages\n"
    "- 包装 erp_* 远程查询自动翻页,只用于本地 DB 没有的数据(如物流轨迹)\n"
    "- 结果自动落 staging/erp_xxx.parquet,在 code_execute 里 duckdb 直接读\n"
    "- 用前先按 erp_* 工具的两步协议确认参数格式\n"
)
