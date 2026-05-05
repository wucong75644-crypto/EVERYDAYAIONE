"""
文件操作工具定义

为 Agent Loop 提供 file_read/file_list/file_search 工具定义。
由 agent_tools.py 导入合并。
"""

from typing import Any, Dict, List, Set


# 文件工具名集合（INFO 类型：结果回传大脑）
FILE_INFO_TOOLS: Set[str] = {
    "file_read",
    "file_list",
    "file_search",
}

# 工具 Schema（参数验证）
FILE_TOOL_SCHEMAS: Dict[str, Dict[str, Any]] = {
    "file_read": {
        "required": ["path"],
        "properties": {
            "path": {"type": "string"},
            "offset": {"type": "integer"},
            "limit": {"type": "integer"},
            "pages": {"type": "string"},
        },
    },
    "file_list": {
        "required": [],
        "properties": {
            "path": {"type": "string"},
            "show_hidden": {"type": "boolean"},
        },
    },
    "file_search": {
        "required": ["keyword"],
        "properties": {
            "keyword": {"type": "string"},
            "path": {"type": "string"},
            "search_content": {"type": "boolean"},
            "file_pattern": {"type": "string"},
        },
    },
}


def build_file_tools() -> List[Dict[str, Any]]:
    """构建文件操作工具定义（file_read/file_list/file_search）"""
    return [
        {
            "type": "function",
            "function": {
                "name": "file_read",
                "description": (
                    "读取 workspace 内的 PDF、图片或纯文本文件。\n\n"
                    "适用:\n"
                    "- PDF 文件：自动提取文本，用 pages 参数指定页范围（如 '3' 或 '1-5'）。"
                    "≤10 页自动全读，>10 页必须指定 pages，每次最多 20 页\n"
                    "- 图片文件（png/jpg/gif/webp）：自动识别并返回图片供视觉分析\n"
                    "- 纯文本文件（txt/md/json/py 等）：返回内容，最多 2000 行\n\n"
                    "不适用:\n"
                    "- Excel/CSV/Parquet 数据文件 → 用 data_query\n"
                    "- 需要计算/处理的场景 → 用 code_execute"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "文件名或相对路径",
                        },
                        "offset": {
                            "type": "integer",
                            "description": (
                                "起始行号（1-based，默认1即文件开头）。"
                                "仅在文件过大无法一次读取时使用"
                            ),
                        },
                        "limit": {
                            "type": "integer",
                            "description": (
                                "读取行数上限。"
                                "仅在文件过大无法一次读取时使用"
                            ),
                        },
                        "pages": {
                            "type": "string",
                            "description": (
                                "PDF 页码范围（如 '3'、'1-5'、'3,7,10'）。"
                                "仅用于 PDF 文件"
                            ),
                        },
                    },
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "file_list",
                "description": (
                    "列出 workspace 内目录的内容（文件和子目录）。\n"
                    "返回文件名列表，后续直接用文件名引用。\n"
                    "默认列出 workspace 根目录。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "目录相对路径（默认'.'即根目录）",
                        },
                        "show_hidden": {
                            "type": "boolean",
                            "description": "是否显示隐藏文件（默认false）",
                        },
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "file_search",
                "description": (
                    "在 workspace 内搜索文件。\n"
                    "默认按文件名搜索，设置 search_content=true 可搜索文件内容。\n"
                    "可用 file_pattern 过滤文件类型（如 *.csv）。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "keyword": {
                            "type": "string",
                            "description": "搜索关键词",
                        },
                        "path": {
                            "type": "string",
                            "description": "搜索起始目录（默认'.'）",
                        },
                        "search_content": {
                            "type": "boolean",
                            "description": "是否同时搜索文件内容（默认false）",
                        },
                        "file_pattern": {
                            "type": "string",
                            "description": "文件名匹配模式（如 *.csv、report*）",
                        },
                    },
                    "required": ["keyword"],
                },
            },
        },
    ]


# 路由提示词片段
FILE_ROUTING_PROMPT = (
    "## 文件操作规则\n"
    "- 所有文件操作直接用文件名或相对路径（如 '利润表.xlsx'、'子目录/data.csv'）\n"
    "- 查看目录/列出文件 → file_list\n"
    "- 搜索/查找文件 → file_search\n"
    "- 读取/分析 Excel/CSV 数据文件 → data_query\n"
    "- 读取 PDF/图片/纯文本 → file_read\n"
    "- 计算分析/生成文件 → code_execute\n"
    "- file_list 和 file_search 返回的结果已包含文件元信息（行列数/类型/读取命令），直接使用\n"
    "- 文件操作完毕后，调 route_to_chat 汇总结果回复用户\n\n"
)
