"""为模型构造稳定且不泄露系统事实的恢复Observation。"""

from __future__ import annotations

import json

from services.agent.runtime.validation.types import ValidatedToolResult


def build_recovery_observation(result: ValidatedToolResult) -> str:
    payload = {
        "type": "tool_validation_error",
        "tool": result.effective_tool_name,
        "error_code": result.error_code or "TOOL_ERROR",
        "message": result.error_message or "工具执行未成功",
        "retryable": result.retryable,
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
