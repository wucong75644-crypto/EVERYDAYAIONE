"""
文件操作工具定义

为 Agent Loop 提供 file_read/file_write/file_list/file_search/file_info 工具定义。
由 agent_tools.py 导入合并。
"""

from typing import Any, Dict, List, Set


# 文件工具名集合（INFO 类型：结果回传大脑）
FILE_INFO_TOOLS: Set[str] = {
    "file_read",
    "file_write",
    "file_list",
    "file_search",
    "file_info",
}

# 工具 Schema（参数验证）
FILE_TOOL_SCHEMAS: Dict[str, Dict[str, Any]] = {
    "file_read": {
        "required": ["path"],
        "properties": {
            "path": {"type": "string"},
            "offset": {"type": "integer"},
            "limit": {"type": "integer"},
        },
    },
    "file_write": {
        "required": ["path", "content"],
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
            "mode": {"type": "string"},
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
    "file_info": {
        "required": ["path"],
        "properties": {
            "path": {"type": "string"},
        },
    },
}


def build_file_tools() -> List[Dict[str, Any]]:
    """构建文件操作工具定义（5个 INFO 工具）"""
    return [
        {
            "type": "function",
            "function": {
                "name": "file_read",
                "description": (
                    "读取 workspace 内的文件内容。\n\n"
                    "使用说明:\n"
                    "- 默认读取整个文件（最多 2000 行）。"
                    "大于 256KB 的文件会返回错误，请使用 offset 和 limit 分页读取\n"
                    "- 已知需要读取文件的某个部分时，只读取该部分。这对大文件很重要\n"
                    "- 返回格式为 cat -n 格式，行号从 1 开始\n"
                    "- 二进制文件（图片/Excel/Parquet等）请用 code_execute 处理\n"
                    "- 读取空文件会收到空内容警告"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "文件相对路径（相对于 workspace 根目录）",
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
                    },
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "file_write",
                "description": (
                    "在 workspace 内创建或写入文件。\n"
                    "mode: overwrite(覆盖,默认) / append(追加) / create_only(仅新建)"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "文件相对路径",
                        },
                        "content": {
                            "type": "string",
                            "description": "要写入的内容",
                        },
                        "mode": {
                            "type": "string",
                            "enum": ["overwrite", "append", "create_only"],
                            "description": "写入模式（默认 overwrite）",
                        },
                    },
                    "required": ["path", "content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "file_list",
                "description": (
                    "列出 workspace 内目录的内容（文件和子目录）。\n"
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
        {
            "type": "function",
            "function": {
                "name": "file_info",
                "description": (
                    "获取文件或目录的元信息（大小、类型、修改时间、权限等）"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "文件或目录的相对路径",
                        },
                    },
                    "required": ["path"],
                },
            },
        },
    ]


# 路由提示词片段
FILE_ROUTING_PROMPT = (
    "## 文件操作规则\n"
    "- 用户要求读取/查看文本文件 → file_read"
    "（大于256KB的文件会提示分页读取）\n"
    "- 处理 Excel/图片/Parquet 等二进制文件 → code_execute"
    "（沙盒内用 WORKSPACE_DIR 定位文件）\n"
    "- 复杂数据分析（统计/筛选/聚合/大文件处理）→ code_execute\n"
    "- 用户要求写入/创建/保存文件 → file_write\n"
    "- 用户要求查看目录/列出文件 → file_list\n"
    "- 用户要求搜索/查找文件 → file_search\n"
    "- 用户要求查看文件信息/属性 → file_info\n"
    "- 文件操作完毕后，调 route_to_chat 汇总结果回复用户\n\n"
)
