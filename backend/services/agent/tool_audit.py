"""
工具调用结构化审计日志

每次工具执行后 fire-and-forget 写入 tool_audit_log 表，
失败只 warning 不阻塞主流程。

用途：
- 按 task_id 查完整调用链
- 按 tool_name 统计调用频次/耗时/错误率
- 按 org_id + 时间段查企业使用情况
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict

from loguru import logger


@dataclass
class ToolAuditEntry:
    """单次工具调用的审计记录"""
    task_id: str
    conversation_id: str
    user_id: str
    org_id: str
    tool_name: str
    tool_call_id: str
    turn: int
    args_hash: str          # MD5(sorted args JSON)
    result_length: int
    elapsed_ms: int
    status: str             # success / timeout / error
    is_cached: bool = False
    is_truncated: bool = False
    # v6: 可观测性扩展
    prompt_tokens: int = 0
    completion_tokens: int = 0
    trace_id: str = ""
    # Correlated structured log only; governed MCP facts use explicit columns.
    execution: dict[str, Any] = field(default_factory=dict)
    # Present only for a platform-reviewed MCP invocation; values are fixed
    # identifiers and a result digest, never arguments, results, or credentials.
    connector_id: str | None = None
    capability: str | None = None
    remote_tool_name: str | None = None
    invocation_status: str | None = None
    replay_requirement: str | None = None
    replayed: bool | None = None
    result_sha256: str | None = None
    error_code: str | None = None


def mcp_audit_columns(fields: dict[str, Any]) -> dict[str, Any]:
    """Project only the current fixed allowlist facts to durable audit columns."""
    facts = (
        fields.get("connector_id"), fields.get("capability"),
        fields.get("remote_tool_name"), fields.get("replay_requirement"),
    )
    if facts != ("test-readonly", "test.sample.read", "lookup_sample", "record_required"):
        return {}
    invocation_status = fields.get("invocation_status")
    replayed = fields.get("replayed")
    digest = fields.get("result_sha256")
    error_code = fields.get("error_code")
    safe_errors = {
        "MCP_AUTH_FAILED", "MCP_CONNECTOR_DISABLED", "MCP_CREDENTIAL_UNAVAILABLE",
        "MCP_FEATURE_DISABLED", "MCP_HEALTH_FAILED", "MCP_REMOTE_ERROR",
        "MCP_PROTOCOL_ERROR", "MCP_RESULT_INVALID", "MCP_SCHEMA_NOT_REVIEWED",
        "MCP_TIMEOUT", "MCP_TOOL_ERROR", "MCP_UNAVAILABLE", "MCP_CANCELLED",
    }
    if (invocation_status not in {"not_started", "succeeded", "failed", "uncertain"}
            or type(replayed) is not bool
            or (digest is not None and (
                not isinstance(digest, str) or len(digest) != 64
                or any(char not in "0123456789abcdef" for char in digest)
            ))
            or (error_code is not None and error_code not in safe_errors)):
        return {}
    return {
        "connector_id": facts[0],
        "capability": facts[1],
        "remote_tool_name": facts[2],
        "replay_requirement": facts[3],
        "invocation_status": invocation_status,
        "replayed": replayed,
        "result_sha256": digest,
        "error_code": error_code,
    }


def build_args_hash(args: Dict[str, Any]) -> str:
    """生成参数摘要（MD5 hash，不存明文）"""
    sorted_json = json.dumps(args, sort_keys=True, ensure_ascii=False)
    return hashlib.md5(sorted_json.encode()).hexdigest()[:12]


async def record_tool_audit(db: Any, entry: ToolAuditEntry) -> None:
    """写入审计记录（fire-and-forget，失败只 warning）

    调用方应通过 asyncio.create_task() 调用本函数，确保不阻塞工具返回。
    DB SDK 是同步调用，用 asyncio.to_thread 避免阻塞事件循环。
    """
    import asyncio

    try:
        row = asdict(entry)
        execution = row.pop("execution")
        if execution:
            logger.bind(tool_execution=execution, tool_call_id=entry.tool_call_id,
                        task_id=entry.task_id, trace_id=entry.trace_id).info(
                "Tool audit execution | tool={} | call={} | facts={}",
                entry.tool_name, entry.tool_call_id, json.dumps(execution, ensure_ascii=False),
            )
        await asyncio.to_thread(
            lambda: db.table("tool_audit_log").insert(
                row, returning=False,
            ).execute()
        )
    except Exception as e:
        logger.warning(
            f"Tool audit write failed | tool={entry.tool_name} | "
            f"task={entry.task_id} | error={e}"
        )
