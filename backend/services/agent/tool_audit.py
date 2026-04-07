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
from dataclasses import asdict, dataclass
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
        await asyncio.to_thread(
            lambda: db.table("tool_audit_log").insert(row).execute()
        )
    except Exception as e:
        logger.warning(
            f"Tool audit write failed | tool={entry.tool_name} | "
            f"task={entry.task_id} | error={e}"
        )
