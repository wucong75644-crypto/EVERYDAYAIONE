"""
代码执行沙盒工具定义

为 Agent Loop 提供 code_execute 工具定义。
由 agent_tools.py 导入合并。
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


def build_code_tools() -> List[Dict[str, Any]]:
    """构建代码执行工具定义（1个 INFO 工具）"""
    return [
        {
            "type": "function",
            "function": {
                "name": "code_execute",
                "description": (
                    "纯计算沙盒。对 staging 数据做计算、转换、导出文件。\n"
                    "⚠ 沙盒内不能查询数据！数据必须先通过工具获取"
                    "（local_* / erp_* / fetch_all_pages），"
                    "大数据会自动存到 staging 文件。\n\n"
                    "沙盒内可用：\n"
                    "- STAGING_DIR: staging 数据目录，用 pd.read_parquet() 读取\n"
                    "- OUTPUT_DIR: 输出目录，文件写到这里自动上传并返回下载链接\n"
                    "- 标准库: math, json, datetime, Decimal, Counter, "
                    "pandas(pd), io, Path\n\n"
                    "典型流程（导出 Excel）：\n"
                    "  df = pd.read_parquet(STAGING_DIR + '/xxx.parquet')\n"
                    "  df.to_excel(OUTPUT_DIR + '/报表.xlsx', index=False)\n"
                    "  # 平台自动检测并上传，返回下载链接\n\n"
                    "注意：\n"
                    "- staging 数据是 Parquet 格式，用 pd.read_parquet() 读取\n"
                    "- 生成文件写到 OUTPUT_DIR，平台自动上传\n"
                    "- 顶层可直接 await，用 print() 输出文字\n"
                    "- 禁止 import os/sys 等系统模块"
                ),
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


# 路由提示词片段
CODE_ROUTING_PROMPT = (
    "## code_execute 使用协议\n"
    "- code_execute 是纯计算沙盒，只能处理已获取的数据，不能查询数据\n"
    "- 数据获取必须先通过工具层完成（local_db_export / fetch_all_pages），"
    "数据存为 Parquet 格式到 staging 目录\n"
    "- 沙盒内用 STAGING_DIR 变量定位数据，用 pd.read_parquet() 读取（零解析问题）\n"
    "- 生成文件写到 OUTPUT_DIR 目录，平台自动检测上传，不需要手动上传\n"
    "- 典型流程：local_db_export → code_execute 读 Parquet → pandas 计算 → "
    "df.to_excel(OUTPUT_DIR + '/报表.xlsx')\n"
    "- 顶层可直接 await，用 print() 输出文字\n\n"
    "## fetch_all_pages 使用协议\n"
    "- 全量翻页工具，包装任意 erp_* 远程查询工具，自动翻页拉全部数据\n"
    "- 仅用于本地数据库没有的数据（如物流轨迹），本地有的数据用 local_db_export\n"
    "- 结果自动存 staging 文件（Parquet），返回文件路径\n"
    "- 使用前需先通过 erp_* 工具的两步协议确认参数格式\n\n"
)
