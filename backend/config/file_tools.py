"""
文件操作工具定义

对齐 Claude 模式：file_search 定位文件并转 staging，AI 在 code_execute 自主探索。
file_read 仅保留图片视觉能力。restore_file 恢复文件。
"""

from typing import Any, Dict, List, Set


# 文件工具名集合（INFO 类型：结果回传大脑）
FILE_INFO_TOOLS: Set[str] = {
    "file_search",
    "file_read",
    "file_delete",
    "restore_file",
}

# 工具 Schema（参数验证）
FILE_TOOL_SCHEMAS: Dict[str, Dict[str, Any]] = {
    "file_search": {
        "required": [],
        "properties": {
            "path": {"type": "string"},
            "keyword": {"type": "string"},
            "file_pattern": {"type": "string"},
        },
    },
    "file_read": {
        "required": ["path"],
        "properties": {
            "path": {"type": "string"},
        },
    },
    "restore_file": {
        "required": ["filename"],
        "properties": {
            "filename": {"type": "string"},
        },
    },
    "file_delete": {
        "required": ["files"],
        "properties": {
            "files": {
                "type": "array",
                "items": {"type": "string"},
                "description": "要删除的文件名或相对路径列表",
            },
        },
    },
}


def build_file_tools() -> List[Dict[str, Any]]:
    """构建文件操作工具定义（file_search / file_read / restore_file）"""
    return [
        {
            "type": "function",
            "function": {
                "name": "file_search",
                "description": (
                    "搜索和准备工作区文件。定位文件 → 大 Excel/CSV 自动转 Parquet 到 staging → 返回路径。\n\n"
                    "Usage:\n"
                    "- 无参数：列出工作区根目录所有文件\n"
                    "- path：列出指定目录或准备指定文件（大数据文件自动转 Parquet）\n"
                    "- keyword：按文件名关键词搜索\n"
                    "- file_pattern：按通配符过滤（如 *.csv）\n\n"
                    "返回文件列表和 staging 路径，以及可直接复制到 code_execute 中执行的 duckdb.sql() 查询语句。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": (
                                "目录相对路径（列目录）或文件名/相对路径（准备单个文件）。"
                                "默认列出根目录。"
                            ),
                        },
                        "keyword": {
                            "type": "string",
                            "description": "搜索关键词（按文件名匹配）",
                        },
                        "file_pattern": {
                            "type": "string",
                            "description": "文件名通配符（如 *.csv、report*）",
                        },
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "file_read",
                "description": (
                    "读取图片文件，返回给视觉模型分析。\n\n"
                    "仅用于图片文件（png/jpg/gif/webp/bmp/svg）。\n"
                    "其他文件类型（Excel/CSV/PDF/DOCX/文本）在 code_execute 中直接读取：\n"
                    "- Excel: openpyxl.load_workbook(read_only=True)\n"
                    "- PDF: pdfplumber.open()\n"
                    "- DOCX: docx.Document()\n"
                    "- 文本: open()"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "图片文件名或相对路径",
                        },
                    },
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "file_delete",
                "description": (
                    "删除工作区文件。传入文件名或相对路径列表。\n\n"
                    "路径从 file_search 返回的结果中获取，无需手动拼写。\n"
                    "执行前会弹窗让用户确认，用户拒绝则不删除。\n"
                    "删除后 30 天内可从 CDN 恢复。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "files": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "要删除的文件名或相对路径列表（如 ['下载/报表.xlsx']）",
                        },
                    },
                    "required": ["files"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "restore_file",
                "description": (
                    "恢复已删除的文件。\n\n"
                    "当用户说「撤销删除」「恢复文件」「找回文件」时使用。\n"
                    "file_delete 删除文件后 30 天内，可从 OSS 下载回 workspace。\n"
                    "超过 30 天后文件被永久清理，无法恢复。"
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


# 路由提示词片段（ERP Agent 用）
FILE_ROUTING_PROMPT = (
    "## 文件操作规则\n"
    "- file_search 定位文件：无参数列目录，有 path/keyword 搜索文件\n"
    "- 数据文件（Excel/CSV）自动转 Parquet 到 staging，返回路径\n"
    "- code_execute 中读 manifest 获取路径：\n"
    "  import json\n"
    "  with open(STAGING_DIR + '/_manifest.json') as f:\n"
    "      manifest = json.load(f)\n"
    "  用 duckdb.sql() 查询 Parquet 数据\n"
    "- 图片文件用 file_read 返回给视觉模型\n"
    "- 撤销/恢复原文件 → restore_file\n\n"
)
