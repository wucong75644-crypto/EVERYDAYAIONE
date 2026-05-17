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
    "file_analyze",
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
    "file_analyze": {
        "required": ["path"],
        "properties": {
            "path": {"type": "string"},
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
                    "将图片文件返回给视觉模型分析。\n\n"
                    "使用场景：\n"
                    "- 用户上传图片并询问图片内容\n"
                    "- 需要 OCR、读取截图、识别图表内容\n\n"
                    "支持格式：png/jpg/gif/webp/bmp/svg。仅支持图片格式。"
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
                "name": "file_analyze",
                "description": (
                    "读取 Excel/CSV 文件的完整结构，自动转为 Parquet 缓存。\n"
                    "自动处理多级表头、合并单元格、表头偏移、特殊行检测，"
                    "比手动 openpyxl 读取更准确。\n\n"
                    "使用场景：\n"
                    "- 用户上传或提及了 Excel/CSV 文件\n"
                    "- 需要了解数据文件的结构再做进一步分析\n"
                    "- 需要获取 Parquet 路径供 code_execute 中 duckdb 查询\n\n"
                    "所有 Excel/CSV 文件的首次读取都通过此工具。\n"
                    "返回：列名、数据类型、行数、样本数据、Parquet 缓存路径。\n"
                    "支持：.xlsx .xls .csv .tsv"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "文件名或相对路径（从 file_search 结果或用户附件路径获取）",
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
