"""文件处理编排器（L1 → L2 → L3 三层降级）。

负责调用 L1 确定性管道，检查结果质量，
失败时静默触发 L2 AI 修复，3 次后升级到 L3 告知用户。

设计文档：docs/document/TECH_文件处理系统.md §三（整体架构）
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from services.agent.file_meta import (
    FileMeta,
    format_file_view,
    read_file_meta,
)

_L2_MAX_RETRIES = 3


async def _audit_file_process(
    action: str,
    status: str,
    elapsed_ms: int,
    filename: str,
    extra: dict | None = None,
) -> None:
    """记录文件处理审计日志（fire-and-forget）。

    action: L1_clean / L2_fix / L3_escalate
    status: success / warning / fail
    """
    try:
        from services.agent.tool_audit import ToolAuditEntry, record_tool_audit
        from core.context import get_request_context

        ctx = get_request_context()
        if not ctx:
            return

        entry = ToolAuditEntry(
            task_id=getattr(ctx, "task_id", ""),
            conversation_id=getattr(ctx, "conversation_id", ""),
            user_id=getattr(ctx, "user_id", ""),
            org_id=getattr(ctx, "org_id", ""),
            tool_name="file_process",
            tool_call_id="",
            turn=0,
            args_hash="",
            result_length=0,
            elapsed_ms=elapsed_ms,
            status=status,
        )
        db = getattr(ctx, "db", None)
        if db:
            import asyncio
            asyncio.create_task(record_tool_audit(db, entry))
    except Exception as e:
        logger.debug(f"File process audit failed: {e}")


@dataclass
class FileProcessResult:
    """文件处理结果。"""
    success: bool
    file_view: str = ""            # AI context 注入的文件视图
    meta: FileMeta | None = None
    parquet_path: str = ""
    error: dict[str, Any] | None = None  # L3 错误信息（需要用户介入）
    processed_by: str = "L1"       # L1 | L2 | L3


@dataclass
class L2FixRequest:
    """传给 AI 沙盒的 L2 修复请求。"""
    source_file: str
    l1_error_type: str
    l1_details: str
    raw_sample: list[list[Any]] = field(default_factory=list)
    output_path: str = ""
    # 大文件分块失败场景
    failed_chunk: int | None = None
    chunk_row_range: tuple[int, int] | None = None


async def process_file(
    excel_path: str,
    staging_dir: str,
    sheet: str | None = None,
) -> FileProcessResult:
    """文件处理主入口：L1 → 检查 → L2 → L3。

    Args:
        excel_path: 原始文件绝对路径
        staging_dir: staging/{conv_id}/ 目录
        sheet: Sheet 名称（None=第一个，"*"=合并所有）

    Returns:
        FileProcessResult，包含文件视图或错误信息
    """
    start = time.monotonic()

    # ── L1：确定性管道 ──
    try:
        from services.agent.data_query_cache import ensure_parquet_cache
        cache_path, sheet_names = await ensure_parquet_cache(
            excel_path, sheet, staging_dir,
        )
    except Exception as e:
        logger.warning(f"L1 failed with exception: {e}")
        return _build_l3_result(
            source_file=excel_path,
            error_type="l1_exception",
            details=str(e),
        )

    # ── 检查 L1 结果 ──
    meta = read_file_meta(cache_path)
    if meta is None:
        # 旧格式 meta 或写入失败，视为 pass（向后兼容）
        return FileProcessResult(
            success=True,
            parquet_path=cache_path,
            processed_by="L1",
        )

    # warning + confidence 高 → 成功（少量缺失值但可用）
    # warning + confidence 低 → 触发 L2（数据质量可能有问题）
    _warning_needs_l2 = (
        meta.status == "warning"
        and meta.confidence < 0.7
    )
    if meta.status in ("pass", "warning") and not _warning_needs_l2:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        logger.info(f"File process L1 {meta.status} | {Path(excel_path).name} | {elapsed_ms}ms")
        await _audit_file_process("L1_clean", meta.status, elapsed_ms, Path(excel_path).name)
        return FileProcessResult(
            success=True,
            file_view=format_file_view(meta),
            meta=meta,
            parquet_path=cache_path,
            processed_by="L1",
        )

    # ── L1 失败/警告 → 记录埋点 + 构造 L2 修复请求 ──
    elapsed_ms = int((time.monotonic() - start) * 1000)
    await _audit_file_process(
        "L1_clean", meta.status, elapsed_ms, Path(excel_path).name,
        extra={"issues_count": len(meta.issues)},
    )
    l2_request = _build_l2_request(excel_path, meta, cache_path, staging_dir)

    # ── L2 重试循环（当前只构造请求，实际执行由工具循环驱动）──
    # L2 的实际执行需要 AI 在沙盒中写代码，不能在此函数中同步完成。
    # 返回 L2 请求信息，由上层（tool_loop_executor）决定是否触发 code_execute。
    return FileProcessResult(
        success=False,
        file_view=format_file_view(meta),
        meta=meta,
        parquet_path=cache_path,
        error=_build_l2_error_for_ai(l2_request, meta),
        processed_by="L1",
    )


def check_l2_result(cache_path: str) -> FileProcessResult:
    """L2 修复后检查结果（AI 沙盒执行完后调用）。"""
    meta = read_file_meta(cache_path)
    if meta is None:
        return FileProcessResult(success=False, processed_by="L2")

    if meta.status in ("pass", "warning"):
        return FileProcessResult(
            success=True,
            file_view=format_file_view(meta),
            meta=meta,
            parquet_path=cache_path,
            processed_by="L2",
        )

    return FileProcessResult(
        success=False,
        meta=meta,
        parquet_path=cache_path,
        processed_by="L2",
    )


def build_l3_message(meta: FileMeta, retry_count: int) -> str:
    """构造 L3 告知用户的自然语言消息。"""
    issues = meta.issues or []
    parts = []
    for issue in issues[:3]:
        loc = issue.get("location", {})
        suggestion = issue.get("suggestion", "")
        if loc.get("row"):
            parts.append(f"• Row {loc['row']} {loc.get('col', '')}列: {suggestion}")
        else:
            parts.append(f"• {suggestion}")

    msg = "这个文件的部分数据无法自动处理：\n"
    msg += "\n".join(parts) if parts else "• 未知问题"
    msg += "\n\n请提供更多信息帮助我理解文件结构，或者尝试修复原始文件后重新上传。"
    return msg


def _build_l2_request(
    excel_path: str,
    meta: FileMeta,
    cache_path: str,
    staging_dir: str,
) -> L2FixRequest:
    """从 L1 失败结果构造 L2 修复请求。"""
    issues = meta.issues or []
    error_types = [i.get("type", "unknown") for i in issues if i.get("severity") in ("error", "warning")]
    details = "; ".join(i.get("suggestion", "") for i in issues[:5])

    # 提取原始数据样本（从 meta.sample 中获取）
    raw_sample = []
    for row in (meta.sample or {}).get("head", []):
        raw_sample.append(list(row.values()))

    return L2FixRequest(
        source_file=excel_path,
        l1_error_type=error_types[0] if error_types else "unknown",
        l1_details=details,
        raw_sample=raw_sample,
        output_path=cache_path,
    )


def _build_l2_error_for_ai(request: L2FixRequest, meta: FileMeta) -> dict[str, Any]:
    """构造传给 AI 的 L2 修复上下文（AI 据此决定怎么修复）。"""
    return {
        "status": "needs_fix",
        "source_file": request.source_file,
        "l1_error_type": request.l1_error_type,
        "l1_details": request.l1_details,
        "raw_sample": request.raw_sample,
        "output_path": request.output_path,
        "retry_count": 0,
        "max_retries": _L2_MAX_RETRIES,
        "instructions": (
            "L1 自动处理失败。请根据错误信息和原始数据样本，"
            "在沙盒中写代码修复数据，输出到指定路径。"
            "参考 SKILLS_DIR/file-fix.md 的输出规范。"
        ),
    }


def _build_l3_result(
    source_file: str,
    error_type: str,
    details: str,
) -> FileProcessResult:
    """构造 L3 结果（L1 异常时直接跳到 L3）。"""
    return FileProcessResult(
        success=False,
        error={
            "status": "failed",
            "error_type": error_type,
            "details": details,
            "suggestions": [
                "请检查文件格式是否正确",
                "尝试用 Excel 打开并另存为 xlsx 格式",
            ],
        },
        processed_by="L3",
    )
