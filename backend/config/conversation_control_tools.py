"""Conversation control Function Calling schema."""

from __future__ import annotations

from typing import Any, Dict, List


CONVERSATION_CONTROL_TOOL_NAME = "conversation_control"


def build_conversation_control_tools() -> List[Dict[str, Any]]:
    """Return the stable tool schema used by the control router."""
    return [
        {
            "type": "function",
            "function": {
                "name": CONVERSATION_CONTROL_TOOL_NAME,
                "description": (
                    "当用户明确要求暂停、恢复或最终取消当前对话任务时调用。"
                    "普通业务问题不要调用。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["pause", "resume", "cancel", "none"],
                            "description": (
                                "pause=保留进度暂停，resume=恢复已暂停任务，"
                                "cancel=最终取消，none=普通消息。"
                            ),
                        },
                        "reason": {
                            "type": "string",
                            "description": "用一句话概括用户的控制意图。",
                        },
                    },
                    "required": ["action"],
                    "additionalProperties": False,
                },
            },
        }
    ]

