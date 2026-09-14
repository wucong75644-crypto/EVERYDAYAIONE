"""
文件操作工具定义

file_search 仅定位文件；file_analyze 单独治理表格并转 staging。
file_search 命中图片时直接返回多模态（FileReadResult type=image）。
restore_file 恢复文件。
"""

from typing import Any, Dict, List, Set

from config.file_call_contract import FILE_ANALYZE_SELECTOR_GUIDANCE

FILE_INFO_TOOLS: Set[str] = {
    "file_search",
    "file_analyze",
    "file_delete",
    "restore_file",
}

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
        "required": [],
        "properties": {
            "filename": {"type": "string"},
            "record_id": {"type": "integer"},
        },
    },
    "file_analyze": {
        "required": [],
        "properties": {
            "resource_ref": {"type": "string"},
            "file_id": {"type": "string"},
            "path": {"type": "string"},
            "scope": {"type": "string", "enum": ["current", "workspace"]},
        },
    },
    "file_delete": {
        "required": [],
        "properties": {
            "resource_refs": {"type": "array", "items": {"type": "string"}},
            "file_ids": {"type": "array", "items": {"type": "string"}},
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
                    "在指定范围定位文件或发现候选，返回 resource_ref 和兼容 file_id；不转换文件。\n"
                    "- 用户给出完整文件名或完整相对路径：通过 path 原样复制，不增删空格或改写标点。\n"
                    "- 只知道关键词：使用 keyword；按类型查找使用 file_pattern（如 *.csv）。\n"
                    "- 列目录：使用 path 指定目录，根目录为 .。\n"
                    "普通聊天默认检索获准工作区，其中包含本轮和历史聊天上传文件。只有用户明确限定本轮附件时才用 scope=current。"
                    "多个候选先选择完整路径；找到后将 resource_ref 原样传给分析或删除工具，"
                    "已有附件 ID 则直接使用 ID，不再拼写文件名。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": (
                                "目录、完整文件名或完整相对路径。完整文件名可定位子目录中的文件；原样复制，不增删空格。"
                                "完整路径必须准确；同名文件需选择完整路径。"
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
                                "可省略。普通聊天默认 workspace，检索获准工作区及其中的聊天历史附件；"
                                "current 仅本轮附件，只在用户明确限定时使用。定时/预检任务仍受原授权资源范围约束。"
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
                    "支持扩展名: .xlsx .xls .csv .tsv（其他不支持）\n"
                    + FILE_ANALYZE_SELECTOR_GUIDANCE
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "resource_ref": {
                            "type": "string", "description": "file_search 返回的资源引用，原样复制；可自动确定位置，系统仍检查当前动作权限。文件变化后需重新搜索。",
                        },
                        "file_id": {
                            "type": "string",
                            "pattern": "^fid_[a-z0-9]{8}$",
                            "description": "文件 ID（fid_xxx），从 <attachments> 的 <id> 字段 copy。新搜索结果优先使用 resource_ref。",
                        },
                        "path": {
                            "type": "string",
                            "description": "（兼容老协议）文件名或相对路径。与其他选择器同时提供时必须指向同一文件。",
                        },
                        "scope": {
                            "type": "string",
                            "enum": ["current", "workspace"],
                            "description": (
                                "省略时根据已验证资源引用或本轮明确浏览范围定位，无绑定时为 current；显式 current 不扩大。用户指定工作区文件时"
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
                        "resource_refs": {
                            "type": "array", "items": {"type": "string"},
                            "description": "file_search 返回的 resource_ref 列表，优先使用；不得自行编造。",
                        },
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
                            "description": "（兼容老协议）文件名或相对路径列表。与 file_ids 同时提供时合并；不存在或歧义时整批不执行。",
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
                        "record_id": {
                            "type": "integer", "description": "删除记录 ID；同名有多条记录时从候选中选择。",
                        },
                        "filename": {
                            "type": "string",
                            "description": "要恢复的文件名（如 '销售报表.xlsx'）",
                        },
                    },
                    "required": [],
                },
            },
        },
    ]
