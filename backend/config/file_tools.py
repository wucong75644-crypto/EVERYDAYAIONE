"""
文件操作工具定义

对齐 Claude 模式：file_search 定位文件并转 staging，AI 在 code_execute 自主探索。
file_search 命中图片时直接返回多模态（FileReadResult type=image）。
restore_file 恢复文件。
"""

from typing import Any, Dict, List, Set


# 文件工具名集合（INFO 类型：结果回传大脑）
FILE_INFO_TOOLS: Set[str] = {
    "file_search",
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
            "scope": {"type": "string", "enum": ["current", "workspace"]},
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
            "scope": {"type": "string", "enum": ["current", "workspace"]},
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
    """构建文件操作工具定义（file_search / file_analyze / file_delete / restore_file）"""
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
                        "scope": {
                            "type": "string",
                            "enum": ["current", "workspace"],
                            "description": (
                                "默认 current，仅检索当前任务附件；只有用户明确要求"
                                "搜索整个工作区时才使用 workspace。"
                            ),
                        },
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "file_analyze",
                "description": (
                    "读取数据文件（.xlsx/.xls/.csv/.tsv）的完整结构，自动转为 Parquet 缓存。\n"
                    "自动处理多级表头、合并单元格、表头偏移、特殊行检测，"
                    "比手动 openpyxl 读取更准确。\n\n"
                    "When to use:\n"
                    "- attachments 中 status=raw 的 .xlsx/.xls/.csv/.tsv 文件首次治理\n"
                    "- 需要 Parquet 路径供 code_execute 中 duckdb/pandas 查询\n\n"
                    "When NOT to use:\n"
                    "- 已 status=analyzed 的文件 — 直接用 <parquet> 字段 pd.read_parquet，禁止重复治理\n"
                    "- 图片文件（.png/.jpg/.jpeg/.gif/.webp/.bmp）— 已通过视觉通道注入\n"
                    "- PDF/Word/PPT/文本文件 — 用 code_execute + 对应库读取\n\n"
                    "Returns: 列名、数据类型、行数、样本数据、Parquet 缓存相对路径。\n"
                    "支持扩展名: .xlsx .xls .csv .tsv（其他不支持）"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_id": {
                            "type": "string",
                            "pattern": "^fid_[a-z0-9]{8}$",
                            "description": "文件 ID（fid_xxx），从 <attachments> 的 <id> 字段 copy。优先使用 file_id。",
                        },
                        "path": {
                            "type": "string",
                            "description": "（兼容老协议）文件名或相对路径。仅在没有 file_id 时使用。",
                        },
                        "scope": {
                            "type": "string",
                            "enum": ["current", "workspace"],
                            "description": (
                                "默认 current；只有用户明确指定工作区历史文件时"
                                "才使用 workspace。"
                            ),
                        },
                    },
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
                        "file_ids": {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "pattern": "^fid_[a-z0-9]{8}$",
                            },
                            "description": "要删除的 file_id 列表（如 ['fid_a3f2b1c9']）。从 <attachments> 的 <id> 或 file_search 返回的 [fid_xxx] 获取。",
                        },
                        "files": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "（兼容老协议）文件名或相对路径列表。仅在没有 file_ids 时使用。",
                        },
                    },
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
    "- file_search 定位文件: 无参数列目录,有 path/keyword 搜索文件\n"
    "- 数据文件(Excel/CSV)用 file_analyze 治理 → 生成 staging/x.parquet\n"
    "- code_execute 直接读 parquet:\n"
    "  df = pd.read_parquet('staging/x.parquet')\n"
    "  或 duckdb.sql(\"SELECT * FROM 'staging/x.parquet'\").df()\n"
    "- 图片: file_search 命中单张图直接返回多模态(视觉模型自动可见,无需额外工具)\n"
    "- 撤销/恢复原文件 → restore_file\n\n"
)
