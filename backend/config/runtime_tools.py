"""通用交付运行时的确定性工具定义。"""

from __future__ import annotations

from typing import Any


def build_data_compute_tool() -> dict[str, Any]:
    """构建只消费可信 DATA_RESULT 的过滤与聚合工具。"""
    return {
        "type": "function",
        "function": {
            "name": "data_compute",
            "description": (
                "对当前运行时中的可信数据结果做确定性过滤和聚合。"
                "追问排除平台、切换指标、求和、计数或重新计算时必须使用；"
                "不得手工心算工具结果。"
            ),
            "parameters": {
                "type": "object",
                "required": ["artifact_id", "metrics"],
                "properties": {
                    "artifact_id": {
                        "type": "string",
                        "description": "运行时提供的完整数据证据 ID。",
                    },
                    "filters": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["field", "operator", "value"],
                            "properties": {
                                "field": {"type": "string"},
                                "operator": {
                                    "type": "string",
                                    "enum": ["eq", "ne", "in", "not_in"],
                                },
                                "value": {},
                            },
                        },
                    },
                    "group_by": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "metrics": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "required": ["operation", "alias"],
                            "properties": {
                                "field": {"type": "string"},
                                "operation": {
                                    "type": "string",
                                    "enum": ["sum", "count"],
                                },
                                "alias": {"type": "string"},
                            },
                        },
                    },
                },
            },
        },
    }
