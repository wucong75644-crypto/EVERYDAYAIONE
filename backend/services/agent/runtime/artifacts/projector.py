"""把 ArtifactDraft 投影为当前模型可消费的有界 ToolResult。"""

from __future__ import annotations

import json
from typing import Any

from services.agent.agent_result import AgentResult

from .types import ArtifactDraft


def project_tool_result(result: Any, draft: ArtifactDraft) -> Any:
    """小结果保持原协议；大结果返回预览、稳定引用和读取指令。"""
    if draft.model_view.get("truncated") is not True:
        return _legacy_projection(result)
    payload = {
        "artifact_id": draft.artifact_id,
        "tool_name": draft.tool_name,
        "artifact_type": draft.artifact_type,
        "status": draft.status,
        "byte_size": draft.byte_size,
        "content_hash": draft.content_hash,
        **draft.model_view,
        "read": {
            "tool": "artifact_read",
            "arguments": {
                "artifact_id": draft.artifact_id,
                "cursor": 0,
                "max_tokens": 4000,
            },
        },
        "instruction": (
            "当前仅为预览；若回答依赖被省略的细节，必须调用 "
            "artifact_read 分页读取，不能把预览当作完整结果。"
        ),
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _legacy_projection(result: Any) -> Any:
    from schemas.multimodal import FileReadResult

    if isinstance(result, AgentResult):
        return result.to_message_content()
    if isinstance(result, FileReadResult):
        return result.text
    if isinstance(result, str):
        return result
    return str(result)
