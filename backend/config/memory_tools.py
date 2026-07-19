"""通用 Curated Memory Search/Get 工具 Schema。"""

from typing import Any


def build_memory_tools() -> list[dict[str, Any]]:
    """构建仅在个人上下文获准时暴露的只读记忆工具。"""
    return [
        {
            "type": "function",
            "function": {
                "name": "memory_search",
                "description": "搜索当前用户可访问的长期记忆，返回稳定 memory_ref。",
                "parameters": {
                    "type": "object",
                    "required": ["query"],
                    "properties": {
                        "query": {"type": "string", "minLength": 1},
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 6,
                            "default": 3,
                        },
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "memory_get",
                "description": "按 memory_search 返回的稳定 ref 读取完整记忆与来源。",
                "parameters": {
                    "type": "object",
                    "required": ["memory_ref"],
                    "properties": {
                        "memory_ref": {
                            "type": "string",
                            "description": "memory_search 返回的精确 memory_ref",
                        },
                    },
                },
            },
        },
    ]
