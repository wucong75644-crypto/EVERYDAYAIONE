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
    "file_edit",
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
    "file_write": {
        "required": ["path", "content"],
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
            "mode": {"type": "string"},
        },
    },
    "file_edit": {
        "required": ["path", "old_string", "new_string"],
        "properties": {
            "path": {"type": "string"},
            "old_string": {"type": "string"},
            "new_string": {"type": "string"},
            "replace_all": {"type": "boolean"},
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
    """构建文件操作工具定义（5个 INFO 工具）"""
    return [
        {
            "type": "function",
            "function": {
                "name": "file_read",
                "description": (
                    "读取 workspace 内的文件内容。\n\n"
                    "使用说明:\n"
                    "- path 为文件名或相对路径（如 'readme.txt'、'子目录/data.csv'）\n"
                    "- 文本文件：默认读取整个文件（最多 2000 行），大于 256KB 用 offset/limit 分页\n"
                    "- PDF 文件：自动提取文本，用 pages 参数指定页范围（如 '3' 或 '1-5'）。"
                    "≤10 页自动全读，>10 页必须指定 pages，每次最多 20 页\n"
                    "- 图片文件（png/jpg/gif/webp）：自动识别并返回图片供视觉分析\n"
                    "- Excel/Parquet 等二进制文件请用 code_execute 处理\n"
                    "- 文本文件返回 cat -n 格式，行号从 1 开始"
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
                "name": "file_write",
                "description": (
                    "在 workspace 内创建或写入文件。\n\n"
                    "使用说明:\n"
                    "- 覆盖已有文件前，必须先用 file_read 读取确认内容\n"
                    "- 修改已有文件优先用 file_edit（精确替换），而不是 file_write 重写整个文件\n"
                    "- mode: overwrite(覆盖,默认) / append(追加) / create_only(仅新建)"
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
                "name": "file_edit",
                "description": (
                    "精确替换 workspace 内文本文件的内容。\n\n"
                    "使用说明:\n"
                    "- old_string 必须与文件中的文本完全一致（包括缩进和空格）\n"
                    "- 替换前必须先用 file_read 读取文件确认内容\n"
                    "- old_string 必须在文件中唯一。如有多处匹配，设置 replace_all=true\n"
                    "- 修改已有文件优先用 file_edit，不要用 file_write 重写整个文件\n"
                    "- 二进制文件不支持编辑"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "文件名或相对路径",
                        },
                        "old_string": {
                            "type": "string",
                            "description": "要替换的原始文本（必须与文件内容完全一致）",
                        },
                        "new_string": {
                            "type": "string",
                            "description": "替换后的文本",
                        },
                        "replace_all": {
                            "type": "boolean",
                            "description": "替换所有匹配项（默认 false，仅替换唯一匹配）",
                        },
                    },
                    "required": ["path", "old_string", "new_string"],
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
    "- 读取文本文件 → file_read('readme.txt')\n"
    "- 读取 PDF 文件 → file_read('合同.pdf') 或 file_read('合同.pdf', pages='3-5')\n"
    "- 查看图片内容 → file_read('截图.png')（自动进行视觉分析）\n"
    "- 处理 Excel/Parquet 等二进制文件 → code_execute"
    "（沙盒工作目录即 workspace，直接 pd.read_excel('报表.xlsx')）\n"
    "- 复杂数据分析（统计/筛选/聚合/大文件处理）→ code_execute\n"
    "- 写入/创建/保存文件 → file_write\n"
    "- 查看目录/列出文件 → file_list\n"
    "- 搜索/查找文件 → file_search\n"
    "- file_list 和 file_search 返回的结果已包含文件元信息（行列数/类型/读取命令），直接使用\n"
    "- 文件操作完毕后，调 route_to_chat 汇总结果回复用户\n\n"
)
