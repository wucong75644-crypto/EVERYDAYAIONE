"""分批导出（> 30,000 行按时间切片 DuckDB 导出 + 合并）

当预估行数超过 BATCH_THRESHOLD 且 mode=export 时，
将时间范围均匀切片，每批走无排序 DuckDB 导出（0.7s/批），
最后 DuckDB 本地合并排序 + 写最终 parquet。

设计文档：docs/document/TECH_查询预检防御层.md v2.0 §2.5
"""

from __future__ import annotations

import math
import time as _time
import uuid as _uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from services.kuaimai.erp_query_preflight import BATCH_THRESHOLD


def compute_time_slices(
    start_iso: str, end_iso: str, estimated_rows: int,
) -> list[tuple[str, str]]:
    """将时间范围均匀切分为 N 个子区间，每片 ≤ BATCH_THRESHOLD 行。

    Args:
        start_iso: 开始时间（ISO 格式）
        end_iso: 结束时间（ISO 格式）
        estimated_rows: EXPLAIN 估算总行数

    Returns:
        [(slice_start_iso, slice_end_iso), ...] 时间片列表
    """
    start = _parse_iso(start_iso)
    end = _parse_iso(end_iso)
    total_seconds = (end - start).total_seconds()
    if total_seconds <= 0:
        return [(start_iso, end_iso)]

    num_slices = max(1, math.ceil(estimated_rows / BATCH_THRESHOLD))
    # 上限 100 片（防止极端情况）
    num_slices = min(num_slices, 100)
    slice_seconds = total_seconds / num_slices

    slices: list[tuple[str, str]] = []
    for i in range(num_slices):
        s = start + timedelta(seconds=slice_seconds * i)
        e = start + timedelta(seconds=slice_seconds * (i + 1))
        if i == num_slices - 1:
            e = end  # 最后一片对齐结束时间，防止浮点误差
        slices.append((s.isoformat(), e.isoformat()))

    return slices


async def batch_export(
    engine_self: Any,
    doc_type: str,
    filters: list,
    tr: Any,
    extra_fields: list[str] | None,
    limit: int,
    user_id: str | None,
    conversation_id: str | None,
    request_ctx: Any,
    estimated_rows: int,
    include_invalid: bool = False,
    push_thinking: Any = None,
    sort_by: str | None = None,
    sort_dir: str = "desc",
) -> Any:
    """分批导出：时间切片 + 无排序分批 DuckDB + 合并 + 最终排序。

    Args:
        engine_self: UnifiedQueryEngine 实例（用于访问 db/org_id 和 _export 方法）
        其余参数与 _export 一致

    Returns:
        ToolOutput（与单次 _export 格式完全一致）
    """
    import asyncio as _asyncio

    from services.agent.tool_output import (
        ColumnMeta,
        _FORMAT_MIME,
        FileRef,
        OutputFormat,
        OutputStatus,
        ToolOutput,
    )
    from services.kuaimai.erp_duckdb_helpers import (
        build_export_where,
        build_pii_select,
        resolve_export_path,
    )
    from services.kuaimai.erp_unified_schema import (
        COLUMN_WHITELIST,
        DOC_TYPE_CN,
        EXPORT_COLUMN_NAMES,
        EXPORT_MAX,
        TimeRange,
        _FIELD_LABEL_CN,
        build_column_metas_cn,
        merge_export_fields,
    )
    from services.kuaimai.erp_unified_filters import need_archive as _need_archive
    from services.kuaimai.erp_local_helpers import check_sync_health
    from utils.time_context import format_time_header

    type_name = DOC_TYPE_CN.get(doc_type, doc_type)
    start_total = _time.monotonic()

    safe_fields = merge_export_fields(doc_type, extra_fields)
    if tr.time_col and tr.time_col not in safe_fields and tr.time_col in EXPORT_COLUMN_NAMES:
        safe_fields.append(tr.time_col)
    if not safe_fields:
        return ToolOutput(
            summary="传入的 fields 无有效字段",
            source="erp", status=OutputStatus.ERROR,
            error_message="no valid export fields",
        )

    # staging 路径
    staging_dir, rel_path, staging_path, filename = resolve_export_path(
        doc_type, user_id, engine_self.org_id, conversation_id,
    )
    max_rows = min(limit, EXPORT_MAX)

    # 构建 SELECT SQL（不含 ORDER BY，分批无排序拉取）
    select_sql = build_pii_select(safe_fields, cn_header=True)
    if doc_type == "order":
        try:
            from services.kuaimai.order_classifier import OrderClassifier
            classifier = OrderClassifier.for_org(engine_self.db, engine_self.org_id)
            case_sql = classifier.to_case_sql()
            select_sql += f', {case_sql} AS "订单分类"'
        except Exception as e:
            logger.warning(f"分批导出分类标签生成失败，跳过 | error={e}")

    # 计算时间切片
    slices = compute_time_slices(tr.start_iso, tr.end_iso, estimated_rows)
    num_slices = len(slices)

    if push_thinking:
        await push_thinking(f"数据量较大（预估 {estimated_rows:,} 行），分 {num_slices} 批导出...")

    # 逐批导出到临时 parquet 文件（无排序，每批 ≤ BATCH_THRESHOLD）
    batch_dir = staging_dir / f"_batch_{_uuid.uuid4().hex[:8]}"
    batch_dir.mkdir(parents=True, exist_ok=True)
    batch_files: list[Path] = []

    from services.kuaimai.erp_export_subprocess import subprocess_export

    for i, (s_start, s_end) in enumerate(slices):
        # 构建每批的 TimeRange（替换 start/end）
        batch_tr = TimeRange(
            start_iso=s_start, end_iso=s_end,
            time_col=tr.time_col,
            date_range=tr.date_range, label=tr.label,
        )
        where_sql = build_export_where(doc_type, filters, batch_tr, engine_self.org_id)
        need_archive = _need_archive(batch_tr)

        if need_archive:
            inner = (
                f"SELECT {select_sql} FROM pg.public.erp_document_items "
                f"WHERE {where_sql} "
                f"UNION ALL "
                f"SELECT {select_sql} FROM pg.public.erp_document_items_archive "
                f"WHERE {where_sql}"
            )
        else:
            inner = (
                f"SELECT {select_sql} FROM pg.public.erp_document_items "
                f"WHERE {where_sql}"
            )
        # 无排序！分批只拉数据，排序在合并时做
        batch_query = f"SELECT * FROM ({inner}) sub"

        batch_path = batch_dir / f"batch_{i:03d}.parquet"
        try:
            # 单批超时 30s（无排序 ≤30K 行实测 <2s，30s 留充足余量）
            result = await _asyncio.wait_for(
                subprocess_export(
                    batch_query, str(batch_path), push_thinking=None,
                ),
                timeout=30.0,
            )
            batch_count = result["row_count"]
            if batch_count > 0:
                batch_files.append(batch_path)
            else:
                batch_path.unlink(missing_ok=True)

            if push_thinking:
                await push_thinking(
                    f"已完成第 {i + 1}/{num_slices} 批（{batch_count:,} 行）"
                )
        except _asyncio.TimeoutError:
            logger.warning(f"batch export slice {i} timed out (30s)")
            batch_path.unlink(missing_ok=True)
        except Exception as e:
            logger.warning(f"batch export slice {i} failed | error={e}")
            batch_path.unlink(missing_ok=True)

    # 全部批次失败
    if not batch_files:
        _cleanup_batch_dir(batch_dir)
        time_header = format_time_header(
            ctx=request_ctx, range_=tr.date_range, kind="导出窗口",
        )
        health = check_sync_health(engine_self.db, [doc_type], org_id=engine_self.org_id)
        body = f"{type_name}无数据或导出失败\n{health}".strip()
        summary = f"{time_header}\n\n{body}" if time_header else body
        return ToolOutput(
            summary=summary, source="erp",
            status=OutputStatus.EMPTY,
            metadata={"doc_type": doc_type, "time_range": tr.label},
        )

    # 合并 + 排序（DuckDB 读本地 parquet，比远程 PG 快得多）
    if push_thinking:
        await push_thinking("正在合并排序...")

    from core.duckdb_engine import get_duckdb_engine
    duckdb_engine = get_duckdb_engine()

    if sort_by and sort_by in COLUMN_WHITELIST:
        order_col = _FIELD_LABEL_CN.get(sort_by, sort_by)
        order_dir = sort_dir.upper()
    else:
        order_col = _FIELD_LABEL_CN.get(tr.time_col, tr.time_col)
        order_dir = "DESC"

    batch_glob = str(batch_dir / "batch_*.parquet")
    merge_query = (
        f'SELECT * FROM read_parquet(\'{batch_glob}\') '
        f'ORDER BY "{order_col}" {order_dir} LIMIT {max_rows}'
    )

    try:
        merge_result = await _asyncio.to_thread(
            duckdb_engine.export_to_parquet,
            merge_query, str(staging_path), timeout=60.0,
        )
        row_count = merge_result["row_count"]
        size_kb = merge_result["size_kb"]
    except Exception as e:
        logger.error(f"batch merge failed | error={e}", exc_info=True)
        _cleanup_batch_dir(batch_dir)
        return ToolOutput(
            summary=f"分批导出合并失败: {e}",
            source="erp", status=OutputStatus.ERROR,
            error_message=str(e),
        )

    # 清理分片文件
    _cleanup_batch_dir(batch_dir)

    elapsed = _time.monotonic() - start_total
    time_header = format_time_header(
        ctx=request_ctx, range_=tr.date_range, kind="导出窗口",
    )

    if row_count == 0:
        staging_path.unlink(missing_ok=True)
        health = check_sync_health(engine_self.db, [doc_type], org_id=engine_self.org_id)
        body = f"{type_name}无数据\n{health}".strip()
        summary = f"{time_header}\n\n{body}" if time_header else body
        return ToolOutput(
            summary=summary, source="erp",
            status=OutputStatus.EMPTY,
            metadata={"doc_type": doc_type, "time_range": tr.label},
        )

    # profile
    from services.agent.data_profile import build_profile_from_duckdb
    _profile_raw = await _asyncio.to_thread(
        duckdb_engine.profile_parquet, staging_path,
    )
    profile_text, _export_stats = build_profile_from_duckdb(
        _profile_raw, filename=filename,
        file_size_kb=size_kb, elapsed=elapsed,
    )
    body = profile_text
    if row_count >= max_rows:
        body += (
            f"\n\n⚠️ 已达导出上限 {max_rows:,} 行，实际数据可能更多。"
            f"请缩小时间范围重新导出。"
        )
    summary = f"{time_header}\n\n{body}" if time_header else body

    export_columns = build_column_metas_cn(safe_fields)
    if doc_type == "order":
        export_columns.append(ColumnMeta("订单分类", "text", "订单分类"))

    file_ref = FileRef(
        path=str(staging_path),
        filename=filename,
        format="parquet",
        row_count=row_count,
        size_bytes=int(size_kb * 1024),
        columns=export_columns,
        preview=profile_text,
        created_at=_time.time(),
        id=_uuid.uuid4().hex,
        mime_type=_FORMAT_MIME.get("parquet", ""),
        created_by="erp_batch_export",
    )

    logger.info(
        f"batch_export done | rows={row_count:,} | size={size_kb:.0f}KB | "
        f"batches={num_slices} | elapsed={elapsed:.1f}s | doc_type={doc_type}"
    )

    return ToolOutput(
        summary=summary,
        format=OutputFormat.FILE_REF,
        source="erp",
        file_ref=file_ref,
        metadata={
            "doc_type": doc_type,
            "time_range": tr.label,
            "time_column": tr.time_col,
            "stats": _export_stats,
        },
    )


def _cleanup_batch_dir(batch_dir: Path) -> None:
    """清理分片临时目录"""
    try:
        for f in batch_dir.iterdir():
            f.unlink(missing_ok=True)
        batch_dir.rmdir()
    except Exception as e:
        logger.warning(f"batch dir cleanup failed | dir={batch_dir} | error={e}")


def _parse_iso(iso_str: str) -> datetime:
    """解析 ISO 时间字符串（Python 3.11+ fromisoformat 支持全格式）"""
    return datetime.fromisoformat(iso_str)
