"""L5 事实偏离日志 — 复用 Phase 6 审计基础设施。

双写策略：
- DB (tool_audit_log)：metadata 统计，虚拟 "temporal_validator" 工具记录
  tool_name / status / args_hash / result_length，可做时序统计
- loguru 结构化日志：完整详情（claimed / actual / snippet / full_text_hash）
  供 grep 排查与事后溯源

不新建表、不改 schema。符合"复用现有 Phase 1-6 基础设施"的原始设计。

设计文档: docs/document/TECH_ERP时间准确性架构.md §14.2 / §17 N4
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any, Optional, Sequence

from loguru import logger

from services.agent.guardrails.temporal_validator import TemporalDeviation
from services.agent.tool_audit import ToolAuditEntry, record_tool_audit


def _snippet_hash(snippet: str) -> str:
    return hashlib.md5(snippet.encode("utf-8")).hexdigest()[:12]


def emit_deviation_records(
    *,
    db: Any,
    deviations: Sequence[TemporalDeviation],
    task_id: str,
    conversation_id: str,
    user_id: str,
    org_id: Optional[str],
    turn: int,
    patched: bool,
) -> None:
    """对每个偏离：写一条 tool_audit_log metadata + 一条 loguru 详情日志。

    全部 fire-and-forget（不阻塞主流程，失败只 warning）。

    Args:
        patched: 是否已自动修正（True=auto_patched, False=deviation_detected）
    """
    if not deviations:
        return

    status = "auto_patched" if patched else "deviation_detected"

    for idx, dev in enumerate(deviations):
        # 1. DB metadata（复用 Phase 6 tool_audit_log 表）
        entry = ToolAuditEntry(
            task_id=task_id or "",
            conversation_id=conversation_id or "",
            user_id=user_id or "",
            org_id=org_id or "",
            tool_name="temporal_validator",  # 虚拟工具名
            tool_call_id=f"l4_patch_{turn}_{idx}",
            turn=turn,
            args_hash=_snippet_hash(dev.snippet),
            result_length=len(dev.snippet),
            elapsed_ms=0,                     # L4 本身 <1ms，不记
            status=status,
            is_cached=False,
            is_truncated=False,
        )
        asyncio.create_task(record_tool_audit(db, entry))

        # 2. loguru 结构化详情日志（供 grep 排查）
        logger.bind(
            component="fact_deviation",
            deviation_type="weekday_mismatch",
            task_id=task_id,
            conversation_id=conversation_id,
            org_id=org_id,
            turn=turn,
            date=dev.date_str,
            claimed=dev.claimed_weekday,
            actual=dev.actual_weekday,
            snippet=dev.snippet,
            patched=patched,
        ).warning(
            f"[L5] fact deviation | {dev.date_str} "
            f"claimed={dev.claimed_weekday} actual={dev.actual_weekday} "
            f"| {'AUTO_PATCHED' if patched else 'DETECTED'}"
        )
