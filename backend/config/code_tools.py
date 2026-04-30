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
    "在沙盒子进程中执行 Python 代码并返回输出。\n\n"
    "沙盒内不能查询数据，大数据用 data_query 查询提取所需子集。\n"
    "每次执行都是全新子进程，不保留任何变量。\n\n"
    "可用库: pd, plt, Path, math, json, datetime, Decimal, Counter, io\n"
    "环境变量: STAGING_DIR（staging 数据）、OUTPUT_DIR（输出目录，自动上传）\n"
    "data_query 暂存的小数据集可用 pd.read_parquet() 读取。\n"
    "写 Excel 用 engine='xlsxwriter'。\n"
    "生成的文件写到 OUTPUT_DIR，用 print() 输出文本。\n"
    "禁止 import os/sys。"
)

# 主 Agent 版（加 WORKSPACE_DIR + 完整文件生成能力）
_DESCRIPTION_WORKSPACE = (
    "在沙盒子进程中执行 Python 代码并返回输出。\n\n"
    "工作目录为用户工作区，直接用文件元信息中的路径读取文件。\n"
    "每次执行都是全新子进程，不保留任何变量。\n\n"
    "可用库: pd, plt, Path, math, json, datetime, Decimal, Counter, io\n"
    "环境变量: STAGING_DIR（staging 数据）、OUTPUT_DIR（输出目录，自动上传）\n"
    "读 Excel 用 engine='calamine'，写 Excel 用 engine='xlsxwriter'。\n"
    "大数据文件用 data_query 查询提取所需子集。"
    "data_query 暂存的小数据集可用 pd.read_parquet() 读取。\n"
    "生成的文件写到 OUTPUT_DIR，用 print() 输出文本。\n"
    "禁止 import os/sys。"
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
    "- 数据获取必须先通过工具层完成（local_db_export / fetch_all_pages / data_query），"
    "大数据用 data_query SQL 查询提取所需子集\n"
    "- data_query 暂存的小数据集可在沙盒内用 pd.read_parquet(STAGING_DIR + '/文件名') 读取\n"
    "- 生成文件写到 OUTPUT_DIR 目录，平台自动检测上传，不需要手动上传\n"
    "- 图表用 plt.savefig(OUTPUT_DIR + '/图.png', dpi=150, bbox_inches='tight');"
    " plt.close() 释放内存\n"
    "- 典型流程：local_db_export → data_query SQL 提取 → code_execute 计算 → "
    "df.to_excel(OUTPUT_DIR + '/报表.xlsx')\n"
    "- 顶层可直接 await，用 print() 输出文字\n\n"
    "## data_query 使用协议\n"
    "- 查询 staging 文件或工作区数据文件，file 传文件名，sql 中表名用 FROM data\n"
    "- 不传 sql 返回文件结构信息（列名、类型、统计）\n"
    "- 传 export 直接生成导出文件到 OUTPUT_DIR\n"
    "- 中文列名用双引号包裹\n"
    "- 分析大数据用 SQL 聚合筛选，不要 SELECT * 全量取出\n\n"
    "## fetch_all_pages 使用协议\n"
    "- 全量翻页工具，包装任意 erp_* 远程查询工具，自动翻页拉全部数据\n"
    "- 仅用于本地数据库没有的数据（如物流轨迹），本地有的数据用 local_db_export\n"
    "- 结果自动存 staging 文件（Parquet），返回文件路径\n"
    "- 使用前需先通过 erp_* 工具的两步协议确认参数格式\n\n"
)
