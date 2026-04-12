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
    "纯计算沙盒。对 staging 数据做计算、转换、导出文件。\n"
    "⚠ 沙盒内不能查询数据！数据必须先通过工具获取"
    "（local_* / erp_* / fetch_all_pages），"
    "大数据会自动存到 staging 文件。\n\n"
    "沙盒内可用：\n"
    "- STAGING_DIR: staging 数据目录，用 pd.read_parquet() 读取\n"
    "- OUTPUT_DIR: 输出目录，文件写到这里自动上传并返回下载链接\n"
    "- 标准库: math, json, datetime, Decimal, Counter, "
    "pandas(pd), plt(matplotlib), io, Path\n\n"
    "典型流程（导出 Excel）：\n"
    "  df = pd.read_parquet(STAGING_DIR + '/xxx.parquet')\n"
    "  df.to_excel(OUTPUT_DIR + '/报表.xlsx', index=False)\n"
    "  # 平台自动检测并上传，返回下载链接\n\n"
    "注意：\n"
    "- staging 数据是 Parquet 格式，用 pd.read_parquet() 读取\n"
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
    "- WORKSPACE_DIR: 用户工作区（只读），"
    "用户上传的 Excel/CSV 等文件在这里，"
    "用 pd.read_excel(WORKSPACE_DIR + '/文件名.xlsx') 读取\n"
    "- STAGING_DIR: staging 数据目录，"
    "用 pd.read_parquet(STAGING_DIR + '/xxx.parquet') 读取\n"
    "- OUTPUT_DIR: 输出目录，"
    "文件写到这里自动上传并返回下载链接\n"
    "- pd(pandas), plt(matplotlib.pyplot), Path, math, json, "
    "datetime, Decimal, Counter, io\n\n"
    "文件生成能力：\n"
    "- Excel: df.to_excel(OUTPUT_DIR + '/报表.xlsx', index=False)\n"
    "- 图表: plt.savefig(OUTPUT_DIR + '/图表.png', dpi=150, "
    "bbox_inches='tight'); plt.close()\n"
    "- CSV: df.to_csv(OUTPUT_DIR + '/数据.csv', index=False)\n"
    "- PDF: from reportlab.platypus import SimpleDocTemplate\n"
    "- Word: from docx import Document\n"
    "- PPT: from pptx import Presentation\n"
    "- 文本: Path(OUTPUT_DIR + '/结果.txt').write_text(content)\n\n"
    "注意：\n"
    "- 先用 file_list 确认工作区文件名，再用 WORKSPACE_DIR 读取\n"
    "- 图表用 plt.savefig() 保存到 OUTPUT_DIR，不要用 plt.show()\n"
    "- 禁止 import os/sys，写文件用 Path().write_text() 不要用 open()"
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
