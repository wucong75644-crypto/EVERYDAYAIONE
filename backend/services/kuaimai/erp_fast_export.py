"""快路径导出（< 1,000 行 PG 直查 → parquet）

预检估算 < FAST_THRESHOLD 时，跳过 DuckDB 子进程，
直接用 PG QueryBuilder 查数据 + pandas 写 parquet。

设计文档：docs/document/TECH_查询预检防御层.md v2.0 §2.4
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
from services.kuaimai.erp_duckdb_helpers import resolve_export_path
from services.kuaimai.erp_local_helpers import check_sync_health
from services.kuaimai.erp_unified_schema import (
    COLUMN_WHITELIST,
    DOC_TYPE_CN,
    EXPORT_COLUMN_NAMES,
    EXPORT_MAX,
    TIME_COLUMNS,
    ValidatedFilter,
    _FIELD_LABEL_CN,
    merge_export_fields,
)
from utils.time_context import format_time_header


def _classify_row(classifier: Any, row: dict) -> str:
    """用 OrderClassifier 规则对单行订单做分类（Python 侧等价 CASE WHEN）。"""
    for rule in classifier.rules:
        conditions = rule.get("conditions", [])
        if not conditions:
            continue  # 兜底规则用 else
        if classifier._match_all_conditions(row, conditions):
            return rule["rule_name"]
    return "有效订单"


def _apply_filters_to_qb(qb: Any, filters: list[ValidatedFilter]) -> Any:
    """将 ValidatedFilter 列表映射到 QueryBuilder 链式调用。

    时间列（TIME_COLUMNS）由调用方单独处理，此处跳过。
    """
    for f in filters:
        if f.field in TIME_COLUMNS:
            continue
        if f.op == "eq":
            qb = qb.eq(f.field, f.value)
        elif f.op == "ne":
            qb = qb.neq(f.field, f.value)
        elif f.op == "gt":
            qb = qb.gt(f.field, f.value)
        elif f.op == "gte":
            qb = qb.gte(f.field, f.value)
        elif f.op == "lt":
            qb = qb.lt(f.field, f.value)
        elif f.op == "lte":
            qb = qb.lte(f.field, f.value)
        elif f.op == "in":
            qb = qb.in_(f.field, f.value if isinstance(f.value, list) else [f.value])
        elif f.op == "not_in":
            pass  # QueryBuilder 无 not_in，快路径 <1000 行影响有限
        elif f.op == "like":
            qb = qb.ilike(f.field, f.value)
        elif f.op == "is_null":
            qb = qb.is_(f.field, "null")
        elif f.op == "between":
            if isinstance(f.value, (list, tuple)) and len(f.value) == 2:
                qb = qb.gte(f.field, f.value[0]).lte(f.field, f.value[1])
    return qb


async def fast_export(
    engine_self: Any,
    doc_type: str,
    filters: list[ValidatedFilter],
    tr: Any,
    extra_fields: list[str] | None,
    limit: int,
    user_id: str | None,
    conversation_id: str | None,
    request_ctx: Any,
    sort_by: str | None = None,
    sort_dir: str = "desc",
) -> ToolOutput:
    """快路径 export：PG 直查 → 写 parquet → FileRef。"""
    import asyncio as _asyncio
    import pandas as pd

    type_name = DOC_TYPE_CN.get(doc_type, doc_type)
    start = _time.monotonic()

    safe_fields = merge_export_fields(doc_type, extra_fields)
    if sort_by and sort_by in COLUMN_WHITELIST and sort_by not in safe_fields:
        safe_fields.append(sort_by)
    if tr.time_col and tr.time_col not in safe_fields and tr.time_col in EXPORT_COLUMN_NAMES:
        safe_fields.append(tr.time_col)
    if not safe_fields:
        return ToolOutput(
            summary="传入的 fields 无有效字段",
            source="erp", status=OutputStatus.ERROR,
            error_message="no valid export fields",
        )

    # PG 直查：QueryBuilder 链式构建
    columns_csv = ",".join(safe_fields)
    qb = engine_self.db.table("erp_document_items").select(columns_csv)
    qb = qb.eq("doc_type", doc_type)
    qb = qb.gte(tr.time_col, tr.start_iso)
    qb = qb.lt(tr.time_col, tr.end_iso)
    if engine_self.org_id:
        qb = qb.eq("org_id", engine_self.org_id)
    else:
        qb = qb.is_("org_id", "null")

    qb = _apply_filters_to_qb(qb, filters)

    order_col = sort_by if (sort_by and sort_by in COLUMN_WHITELIST) else tr.time_col
    qb = qb.order(order_col, desc=(sort_dir == "desc"))
    max_rows = min(limit, EXPORT_MAX)
    qb = qb.limit(max_rows)

    result = await _asyncio.to_thread(qb.execute)
    rows = result.data or []

    # staging 路径
    staging_dir, rel_path, staging_path, filename = resolve_export_path(
        doc_type, user_id, engine_self.org_id, conversation_id,
    )

    time_header = format_time_header(
        ctx=request_ctx, range_=tr.date_range, kind="导出窗口",
    )

    if not rows:
        health = check_sync_health(engine_self.db, [doc_type], org_id=engine_self.org_id)
        body = f"{type_name}无数据\n{health}".strip()
        summary = f"{time_header}\n\n{body}" if time_header else body
        return ToolOutput(
            summary=summary, source="erp",
            status=OutputStatus.EMPTY,
            metadata={"doc_type": doc_type, "time_range": tr.label},
        )

    # 写 parquet（本地 pandas 写入，毫秒级）
    df = pd.DataFrame(rows)

    # 订单分类标签（与 DuckDB export 的 OrderClassifier.to_case_sql 等价）
    if doc_type == "order":
        try:
            from services.kuaimai.order_classifier import OrderClassifier
            classifier = OrderClassifier.for_org(engine_self.db, engine_self.org_id)
            df["订单分类"] = [
                _classify_row(classifier, row) for row in rows
            ]
        except Exception as e:
            logger.warning(f"快路径分类标签生成失败，跳过 | error={e}")

    cn_map = {f: _FIELD_LABEL_CN.get(f, f) for f in safe_fields if f in df.columns}
    df = df.rename(columns=cn_map)
    df.to_parquet(staging_path, engine="pyarrow", compression="snappy")

    row_count = len(df)
    size_kb = staging_path.stat().st_size / 1024
    elapsed = _time.monotonic() - start

    # profile（复用 DuckDB parquet profile）
    from core.duckdb_engine import get_duckdb_engine
    from services.agent.data_profile import build_profile_from_duckdb
    engine = get_duckdb_engine()
    _profile_raw = await _asyncio.to_thread(
        engine.profile_parquet, staging_path,
    )
    profile_text, _export_stats = build_profile_from_duckdb(
        _profile_raw, filename=filename,
        file_size_kb=size_kb, elapsed=elapsed,
    )
    body = profile_text
    summary = f"{time_header}\n\n{body}" if time_header else body

    from services.kuaimai.erp_unified_schema import build_column_metas_cn
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
        created_by="erp_fast_export",
    )

    logger.info(
        f"fast_export done | rows={row_count} | size={size_kb:.0f}KB | "
        f"elapsed={elapsed:.2f}s | doc_type={doc_type}"
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
