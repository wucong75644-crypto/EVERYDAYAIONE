"""
新表 ORM 查询（summary + export）。

从 erp_unified_query.py 拆出，保持主引擎文件可控。
设计文档: docs/document/TECH_ERP多表统一查询.md §4.2
"""
from __future__ import annotations

import time as _time
import uuid as _uuid
from typing import Any, Optional

from loguru import logger

from services.agent.tool_output import (
    ColumnMeta,
    _FORMAT_MIME,
    FileRef,
    OutputFormat,
    OutputStatus,
    ToolOutput,
)
from services.kuaimai.erp_unified_schema import (
    DEFAULT_DETAIL_FIELDS,
    DOC_TYPE_CN,
    EXPORT_MAX,
    REQUIRED_FIELDS,
    TIME_COLUMNS,
    TimeRange,
    ValidatedFilter,
    _FIELD_LABEL_CN,
    get_column_whitelist,
)
from utils.time_context import RequestContext, format_time_header


async def summary_orm(
    db: Any, org_id: str | None,
    table: str, doc_type: str,
    filters: list[ValidatedFilter],
    tr: TimeRange | None,
    sort_by: str | None = None, sort_dir: str = "desc",
    limit: int = 20,
    request_ctx: Optional[RequestContext] = None,
) -> ToolOutput:
    """新表 summary：ORM + count="exact" 聚合。"""
    from services.kuaimai.erp_unified_filters import apply_orm_filters
    from services.kuaimai.erp_multi_table_schema import FIELD_LABEL_CN

    type_name = DOC_TYPE_CN.get(doc_type, doc_type)
    try:
        q = db.table(table).select("*", count="exact")
        if org_id:
            q = q.eq("org_id", org_id)
        else:
            q = q.is_("org_id", "null")

        if tr:
            q = q.gte(tr.time_col, tr.start_iso).lt(tr.time_col, tr.end_iso)

        non_time = [f for f in filters if f.field not in TIME_COLUMNS]
        q = apply_orm_filters(q, non_time)

        if sort_by:
            q = q.order(sort_by, desc=(sort_dir == "desc"))
        q = q.limit(limit)
        resp = q.execute()
    except Exception as e:
        logger.error(f"ORM summary failed | table={table} error={e}", exc_info=True)
        return ToolOutput(
            summary=f"查询失败: {e}", source="erp",
            status=OutputStatus.ERROR, error_message=str(e),
        )

    count = getattr(resp, "count", None) or len(resp.data or [])
    rows = resp.data or []

    if count == 0:
        return ToolOutput(
            summary=f"{type_name}查询：无匹配记录",
            source="erp", status=OutputStatus.EMPTY,
            metadata={"doc_type": doc_type},
        )

    time_label = tr.label if tr else ""
    summary_text = f"{time_label} {type_name}查询：共 {count} 条记录".strip()
    if rows:
        preview_lines = []
        required = REQUIRED_FIELDS.get(doc_type, [])
        for i, row in enumerate(rows[:5], 1):
            parts = []
            for f in required:
                v = row.get(f)
                if v is not None:
                    label = FIELD_LABEL_CN.get(f, _FIELD_LABEL_CN.get(f, f))
                    parts.append(f"{label}={v}")
            preview_lines.append(f"  {i}. {' | '.join(parts)}")
        if preview_lines:
            summary_text += "\n" + "\n".join(preview_lines)
        if count > 5:
            summary_text += f"\n  ...共{count}条，以上展示前5条"

    summary_cols = [ColumnMeta("count", "integer", "记录数")]
    return ToolOutput(
        summary=summary_text, source="erp",
        format=OutputFormat.TABLE,
        columns=summary_cols,
        data=[{"count": count}],
        metadata={"doc_type": doc_type, "time_range": time_label},
    )


async def export_orm(
    db: Any, org_id: str | None,
    table: str, doc_type: str,
    filters: list[ValidatedFilter],
    tr: TimeRange | None,
    sort_by: str | None = None, sort_dir: str = "desc",
    limit: int = 5000,
    extra_fields: list[str] | None = None,
    user_id: str | None = None,
    conversation_id: str | None = None,
    request_ctx: Optional[RequestContext] = None,
    push_thinking: Any = None,
) -> ToolOutput:
    """新表 export：ORM 分页查询 → Parquet staging。"""
    from services.kuaimai.erp_unified_filters import apply_orm_filters
    from services.kuaimai.erp_multi_table_schema import FIELD_LABEL_CN
    from services.kuaimai.erp_duckdb_helpers import resolve_export_path

    type_name = DOC_TYPE_CN.get(doc_type, doc_type)

    required = REQUIRED_FIELDS.get(doc_type, [])
    defaults = DEFAULT_DETAIL_FIELDS.get(doc_type, [])
    fields: list[str] = []
    seen: set[str] = set()
    for f in (*required, *defaults, *(extra_fields or [])):
        if f not in seen:
            fields.append(f)
            seen.add(f)

    col_wl = get_column_whitelist(doc_type)
    safe_fields = [f for f in fields if f in col_wl]
    if not safe_fields:
        return ToolOutput(
            summary="传入的 fields 无有效字段",
            source="erp", status=OutputStatus.ERROR,
            error_message="no valid fields",
        )

    max_rows = min(limit, EXPORT_MAX)

    try:
        select_str = ", ".join(safe_fields)
        q = db.table(table).select(select_str, count="exact")
        if org_id:
            q = q.eq("org_id", org_id)
        else:
            q = q.is_("org_id", "null")
        if tr:
            q = q.gte(tr.time_col, tr.start_iso).lt(tr.time_col, tr.end_iso)

        non_time = [f for f in filters if f.field not in TIME_COLUMNS]
        q = apply_orm_filters(q, non_time)

        if sort_by and sort_by in col_wl:
            q = q.order(sort_by, desc=(sort_dir == "desc"))
        q = q.limit(max_rows)
        resp = q.execute()
    except Exception as e:
        logger.error(f"ORM export failed | table={table} error={e}", exc_info=True)
        return ToolOutput(
            summary=f"导出失败: {e}", source="erp",
            status=OutputStatus.ERROR, error_message=str(e),
        )

    rows = resp.data or []
    if not rows:
        return ToolOutput(
            summary=f"{type_name}查询：无匹配记录",
            source="erp", status=OutputStatus.EMPTY,
            metadata={"doc_type": doc_type},
        )

    import pandas as pd
    staging_dir, rel_path, staging_path, filename = resolve_export_path(
        doc_type, user_id, org_id, conversation_id,
    )

    df = pd.DataFrame(rows)
    cn_map = {f: FIELD_LABEL_CN.get(f, _FIELD_LABEL_CN.get(f, f)) for f in safe_fields if f in df.columns}
    df = df.rename(columns=cn_map)
    df.to_parquet(staging_path, index=False)

    size_kb = staging_path.stat().st_size / 1024
    row_count = len(df)

    import asyncio as _asyncio
    from core.duckdb_engine import get_duckdb_engine
    from services.agent.data_profile import build_profile_from_duckdb
    engine = get_duckdb_engine()
    _profile_raw = await _asyncio.to_thread(engine.profile_parquet, staging_path)
    profile_text, _export_stats = build_profile_from_duckdb(
        _profile_raw, filename=filename,
        file_size_kb=size_kb, elapsed=0,
    )

    time_label = tr.label if tr else ""
    time_header = ""
    if tr and request_ctx:
        time_header = format_time_header(
            ctx=request_ctx, range_=tr.date_range, kind="导出窗口",
        )

    body = profile_text
    if row_count >= max_rows:
        body += f"\n\n⚠️ 已达导出上限 {max_rows:,} 行，实际数据可能更多。"
    summary = f"{time_header}\n\n{body}".strip() if time_header else body

    export_columns = [
        ColumnMeta(
            cn_map.get(f, f),
            col_wl[f].col_type if f in col_wl else "text",
            cn_map.get(f, f),
        )
        for f in safe_fields if f in col_wl
    ]

    file_ref = FileRef(
        path=str(staging_path), filename=filename,
        format="parquet", row_count=row_count,
        size_bytes=int(size_kb * 1024), columns=export_columns,
        preview=profile_text,
        created_at=_time.time(), id=_uuid.uuid4().hex,
        mime_type=_FORMAT_MIME.get("parquet", ""),
        created_by="erp_export_orm",
    )

    return ToolOutput(
        summary=summary, format=OutputFormat.FILE_REF,
        source="erp", file_ref=file_ref,
        metadata={
            "doc_type": doc_type, "time_range": time_label,
            "stats": _export_stats,
        },
    )
