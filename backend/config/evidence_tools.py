"""按需开放的跨 Turn Evidence 检索工具定义。"""

from typing import Any


def build_evidence_tools() -> list[dict[str, Any]]:
    """构建只读 Evidence Search/Get 工具 Schema。"""
    return [
        {
            "type": "function",
            "function": {
                "name": "evidence_search",
                "description": (
                    "搜索当前会话在本轮固定 revision 之前产生的可信数据证据目录。"
                    "连续追问所需数据不在自动注入的最近证据中时使用。"
                    "只返回 artifact_id、来源、字段、范围和行数，不返回完整数据。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "按来源、字段、查询范围或 artifact_id 搜索",
                        },
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 10,
                            "default": 5,
                        },
                        "before_revision": {
                            "type": "integer",
                            "minimum": 1,
                            "description": (
                                "继续搜索更老证据时，传上次返回的 "
                                "next_before_revision"
                            ),
                        },
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "evidence_get",
                "description": (
                    "读取当前会话固定 revision 内的一个可信 Evidence。"
                    "优先读取 model_view；确需已保存的原始行时 selector=rows。"
                    "用户要求最新数据时不要使用旧 Evidence，应重新查询数据源。"
                ),
                "parameters": {
                    "type": "object",
                    "required": ["artifact_id"],
                    "properties": {
                        "artifact_id": {
                            "type": "string",
                            "description": "evidence_search 返回的精确 artifact_id",
                        },
                        "selector": {
                            "type": "string",
                            "enum": ["model_view", "rows"],
                            "default": "model_view",
                        },
                        "max_tokens": {
                            "type": "integer",
                            "minimum": 256,
                            "maximum": 4000,
                            "default": 2000,
                        },
                    },
                },
            },
        },
    ]
