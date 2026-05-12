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
    "restore_file",
}

# 工具 Schema（参数验证）
FILE_TOOL_SCHEMAS: Dict[str, Dict[str, Any]] = {
    "file_read": {
        "required": ["path"],
        "properties": {
            "path": {"type": "string"},
            "sql": {"type": "string"},
            "sheet": {"type": "string"},
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
    "restore_file": {
        "required": ["filename"],
        "properties": {
            "filename": {"type": "string"},
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
                    "读取 workspace 内的任何文件。所有格式自动识别，直接传文件名即可。\n\n"
                    "Usage:\n"
                    "- path 参数使用文件名或相对路径，优先使用 file_list 返回的路径\n"
                    "- Excel 文件：返回所有 Sheet 的预览（Sheet 名+行列数+前 3 行带单元格编号和公式）\n"
                    "- Excel 指定 sheet 参数时：返回该 Sheet 的完整内容，含公式对照表（公式 vs 计算值）\n"
                    "- Excel/CSV/Parquet 传 sql 参数时：执行 DuckDB SQL 查询，表名用 FROM data，中文列名用双引号\n"
                    "- Excel/CSV/Parquet 的读取结果自动存 staging，后续用 code_execute 从 staging 读取\n"
                    "- PDF 文件：自动提取文本和表格。≤10 页自动全读，>10 页 MUST 指定 pages 参数，每次最多 20 页\n"
                    "- DOCX 文件：返回结构化内容（[Heading 1]/[Normal] 标注 + 表格带行号）\n"
                    "- PPTX 文件：返回结构化内容（Slide 编号 + [Title]/[Text] 标注 + 表格带行号）\n"
                    "- 图片文件（png/jpg/gif/webp）：返回图片供视觉分析\n"
                    "- 纯文本文件（txt/md/json/py 等）：返回内容带行号，最多 2000 行\n"
                    "- 只能读文件，不能读目录。列目录用 file_list"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": (
                                "文件名或相对路径（如 '销售报表.xlsx' 或 '报表/data.csv'）。"
                                "优先使用 file_list 返回的路径"
                            ),
                        },
                        "sql": {
                            "type": "string",
                            "description": (
                                "SQL 查询语句（仅 Excel/CSV/Parquet），表名用 FROM data。"
                                "中文列名用双引号包裹。"
                            ),
                        },
                        "sheet": {
                            "type": "string",
                            "description": (
                                "Excel 的 Sheet 名称（可选，默认第一个 Sheet）。"
                                "传 '*' 合并所有同结构 Sheet。"
                            ),
                        },
                        "offset": {
                            "type": "integer",
                            "description": (
                                "起始行号（1-based，默认1即文件开头）。"
                                "仅文本文件过大时使用"
                            ),
                        },
                        "limit": {
                            "type": "integer",
                            "description": (
                                "读取行数上限。"
                                "仅文本文件过大时使用"
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
        {
            "type": "function",
            "function": {
                "name": "restore_file",
                "description": (
                    "恢复 workspace 文件到修改前的版本。\n\n"
                    "当用户说「撤销」「恢复原文件」「回退」时使用。\n"
                    "系统在每次 code_execute 修改 workspace 文件前自动备份，"
                    "此工具从备份中恢复原始文件。\n"
                    "备份有效期 24 小时，过期后无法恢复。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filename": {
                            "type": "string",
                            "description": "要恢复的文件名（如 '销售报表.xlsx'）",
                        },
                    },
                    "required": ["filename"],
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
    "- 读取任何文件（Excel/CSV/PDF/DOCX/图片/文本）→ file_read\n"
    "- Excel/CSV 数据查询 → file_read(path=..., sql=\"SELECT ... FROM data\")\n"
    "- 计算分析/生成文件 → code_execute\n"
    "- 撤销/恢复原文件/回退 → restore_file\n"
    "- file_list 和 file_search 返回的结果已包含文件元信息（行列数/类型/读取命令），直接使用\n"
    "- 文件操作完毕后，调 route_to_chat 汇总结果回复用户\n\n"
)
