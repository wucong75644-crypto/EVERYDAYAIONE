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
    "纯计算沙盒。对已获取的数据做计算、转换、导出文件。\n"
    "⚠ 沙盒内不能查询数据！数据必须先通过工具获取"
    "（local_* / erp_* / fetch_all_pages），"
    "大数据会自动存到 staging 文件并分配句柄（F1, F2...）。\n\n"
    "沙盒内可用：\n"
    "- FILES: 文件句柄字典，FILES['F1'] 返回文件绝对路径（所有已获取的数据文件）\n"
    "- STAGING_DIR: staging 数据目录（兼容旧代码）\n"
    "- OUTPUT_DIR: 输出目录，文件写到这里自动上传并返回下载链接\n"
    "- 标准库: math, json, datetime, Decimal, Counter, "
    "pandas(pd), plt(matplotlib), io, Path\n\n"
    "典型流程（导出 Excel）：\n"
    "  df = pd.read_parquet(FILES['F1'])  # 用句柄读取数据\n"
    "  df.to_excel(OUTPUT_DIR + '/报表.xlsx', engine='xlsxwriter', index=False)\n"
    "  # 平台自动检测并上传，返回下载链接\n\n"
    "注意：\n"
    "- 用 FILES 句柄引用数据文件，禁止自己拼接路径\n"
    "- 写 Excel 必须加 engine='xlsxwriter'（快 5 倍）\n"
    "- 生成文件写到 OUTPUT_DIR，平台自动上传\n"
    "- 图表用 plt.savefig(OUTPUT_DIR + '/图.png', dpi=150, bbox_inches='tight');"
    " plt.close()\n"
    "- 顶层可直接 await，用 print() 输出文字\n"
    "- 禁止 import os/sys，写文件用 Path().write_text() 不要用 open()"
)

# 主 Agent 版（加 WORKSPACE_DIR + 完整文件生成能力）
_DESCRIPTION_WORKSPACE = (
    "计算沙盒。处理工作区文件、staging 数据，做计算、分析、生成文件。\n"
    "⚠ 沙盒内不能查询 ERP 数据！ERP 数据由 erp_agent 处理。\n\n"
    "沙盒内可用变量：\n"
    "- FILES: 文件句柄字典，FILES['F1'] 返回文件绝对路径"
    "（包含工作区文件 + 工具产出的数据文件）\n"
    "- WORKSPACE_DIR: 用户工作区（只读，兼容旧代码）\n"
    "- STAGING_DIR: staging 数据目录（兼容旧代码）\n"
    "- OUTPUT_DIR: 输出目录，文件写到这里自动上传并返回下载链接\n"
    "- pd(pandas), plt(matplotlib.pyplot), Path, math, json, "
    "datetime, Decimal, Counter, io\n\n"
    "读写文件（必须用高性能引擎）：\n"
    "- 读 Excel: pd.read_excel(FILES['F1'], engine='calamine')  ← 必须加\n"
    "- 写 Excel: df.to_excel(file, engine='xlsxwriter', index=False)\n"
    "- 读 Parquet: pd.read_parquet(FILES['F2'])  ← 工具产出的数据\n"
    "- 写 CSV: df.to_csv(file, index=False)\n"
    "- 图表: plt.savefig(file, dpi=150, bbox_inches='tight'); plt.close()\n"
    "- PDF: from reportlab.platypus import SimpleDocTemplate\n"
    "- Word: from docx import Document\n"
    "- PPT: from pptx import Presentation\n"
    "- 文本: Path(file).write_text(content)\n\n"
    "数据分析工作流：\n"
    "1. 先调 file_list 获取文件列表（每个文件有 F1, F2... 句柄）\n"
    "2. 用句柄读表头: pd.read_excel(FILES['F1'], engine='calamine', nrows=5)\n"
    "3. 检查数据质量（空值/异常值/重复），如有问题先告知用户\n"
    "4. 确认计算方案后，一个 code_execute 完成全部: 读取→计算→输出\n"
    "5. 大结果写文件，只 print 确认信息（如 '已生成报表，共20条汇总'）\n"
    "6. 数据量大(>50MB)时优先输出 CSV（打开更快）\n\n"
    "⚠ FILES 字典（统一文件句柄）：\n"
    "- 所有文件统一编号 F1, F2...（工作区文件 + 工具产出数据）\n"
    "- 沙盒内用 FILES['F1'] 获取文件绝对路径\n"
    "- 禁止自己拼接路径 — 必须用 FILES 句柄\n\n"
    "⚠ 文件输出规则：\n"
    "- 所有生成的文件必须写到 OUTPUT_DIR，如 df.to_excel(OUTPUT_DIR + '/报表.xlsx', engine='xlsxwriter', index=False)\n"
    "- 禁止创建 output/ 等自定义目录 — 只用 OUTPUT_DIR\n\n"
    "⚠ 禁止事项：\n"
    "- 禁止自己拼接读取路径 — 必须用 FILES 句柄引用文件\n"
    "- 禁止 print(df) / print(df.to_string()) — 用 df.shape, df.describe(), df.head()\n"
    "- 禁止反复打开文件探索 — 读一次表头，想好方案，一步到位\n"
    "- 禁止 import os/sys — 写文件用 Path().write_text() 不要用 open()"
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
    "- 数据获取必须先通过工具层完成（local_db_export / fetch_all_pages），"
    "数据存为 Parquet 格式到 staging 目录\n"
    "- 沙盒内用 STAGING_DIR 变量定位数据，用 pd.read_parquet() 读取（零解析问题）\n"
    "- 生成文件写到 OUTPUT_DIR 目录，平台自动检测上传，不需要手动上传\n"
    "- 图表用 plt.savefig(OUTPUT_DIR + '/图.png', dpi=150, bbox_inches='tight');"
    " plt.close() 释放内存\n"
    "- 典型流程：local_db_export → code_execute 读 Parquet → pandas 计算 → "
    "df.to_excel(OUTPUT_DIR + '/报表.xlsx')\n"
    "- 顶层可直接 await，用 print() 输出文字\n\n"
    "## fetch_all_pages 使用协议\n"
    "- 全量翻页工具，包装任意 erp_* 远程查询工具，自动翻页拉全部数据\n"
    "- 仅用于本地数据库没有的数据（如物流轨迹），本地有的数据用 local_db_export\n"
    "- 结果自动存 staging 文件（Parquet），返回文件路径\n"
    "- 使用前需先通过 erp_* 工具的两步协议确认参数格式\n\n"
)
