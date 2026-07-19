"""通用 Artifact Search/Get/Read 工具 Schema。"""

from typing import Any


def build_artifact_tools() -> list[dict[str, Any]]:
    """构建对所有工具结果生效的只读 Artifact 工具。"""
    common_id = {
        "type": "string",
        "description": "工具结果返回的精确 artifact_id",
    }
    return [
        {
            "type": "function",
            "function": {
                "name": "artifact_search",
                "description": "搜索当前执行中已经产生的完整工具结果目录。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 20,
                            "default": 5,
                        },
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "artifact_get",
                "description": "读取一个工具结果的元数据和有界模型视图。",
                "parameters": {
                    "type": "object",
                    "required": ["artifact_id"],
                    "properties": {"artifact_id": common_id},
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "artifact_read",
                "description": (
                    "分页读取完整工具结果。返回 next_cursor 时继续读取，"
                    "直到 complete=true。"
                ),
                "parameters": {
                    "type": "object",
                    "required": ["artifact_id"],
                    "properties": {
                        "artifact_id": common_id,
                        "cursor": {
                            "type": "integer",
                            "minimum": 0,
                            "default": 0,
                        },
                        "max_tokens": {
                            "type": "integer",
                            "minimum": 256,
                            "maximum": 16000,
                            "default": 4000,
                        },
                    },
                },
            },
        },
    ]
