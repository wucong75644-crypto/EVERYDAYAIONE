"""PG COPY 流式导出 — 突破 DuckDB 30k 行限制。

本机 PG 直连，COPY TO STDOUT 流式读取，
pyarrow 分批写入 parquet，内存恒定 ~5 MB。

触发条件：limit > 30000（execute() 路由层判断）。
设计文档：docs/document/TECH_ERP查询架构重构.md §5.8
"""
from __future__ import annotations

import asyncio
import time as _time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Coroutine

import pyarrow as pa
import pyarrow.parquet as pq
import psycopg

from loguru import logger

from services.kuaimai.erp_unified_schema import (
    PLATFORM_CN, TimeRange, ValidatedFilter, TIME_COLUMNS, _FIELD_LABEL_CN,
)
from services.kuaimai.erp_duckdb_helpers import (
    _STATUS_CN, _ORDER_TYPE_CN, _AFTERSALE_TYPE_CN,
    _REFUND_STATUS_CN, _GOOD_STATUS_CN,
)

BATCH_SIZE = 10_000  # 每批 1 万行，内存 ≈ 5 MB


# ── PII 脱敏（Python 层，与 DuckDB SQL 版逻辑一致） ──────


_PII_FIELDS = frozenset({
    "receiver_name", "receiver_mobile", "receiver_phone", "receiver_address",
})


def _mask_pii_value(field: str, value: str | None) -> str | None:
    """单字段 PII 脱敏。"""
    if value is None:
        return None
    if field == "receiver_name" and len(value) >= 2:
        return value[0] + "*" * (len(value) - 1)
    if field in ("receiver_mobile", "receiver_phone") and len(value) >= 7:
        return value[:3] + "****" + value[-4:]
    if field == "receiver_address" and len(value) >= 6:
        return value[:6] + "****"
    return value


# ── 字段翻译（Python 层，复用 erp_duckdb_helpers 映射表） ──


_BOOL_FIELDS = frozenset({
    "is_cancel", "is_refund", "is_exception", "is_halt",
    "is_urgent", "is_scalping", "is_presell",
})


def _translate_row(row: dict) -> dict:
    """字段翻译——与 DuckDB SQL CASE 表达式逻辑一致。"""
    if "platform" in row and row["platform"]:
        row["platform"] = PLATFORM_CN.get(row["platform"], row["platform"])
    if "doc_status" in row and row["doc_status"]:
        row["doc_status"] = _STATUS_CN.get(row["doc_status"], row["doc_status"])
    if "order_status" in row and row["order_status"]:
        row["order_status"] = _STATUS_CN.get(row["order_status"], row["order_status"])
    if "order_type" in row and row["order_type"]:
        parts = str(row["order_type"]).split(",")
        translated = [_ORDER_TYPE_CN.get(p.strip()) for p in parts]
        row["order_type"] = "/".join(filter(None, translated)) or row["order_type"]
    if "aftersale_type" in row and row["aftersale_type"] is not None:
        row["aftersale_type"] = _AFTERSALE_TYPE_CN.get(
            str(row["aftersale_type"]), str(row["aftersale_type"]),
        )
    if "refund_status" in row and row["refund_status"] is not None:
        row["refund_status"] = _REFUND_STATUS_CN.get(
            str(row["refund_status"]), str(row["refund_status"]),
        )
    if "good_status" in row and row["good_status"] is not None:
        row["good_status"] = _GOOD_STATUS_CN.get(
            str(row["good_status"]), str(row["good_status"]),
        )
    for bf in _BOOL_FIELDS:
        if bf in row and row[bf] is not None:
            row[bf] = "是" if row[bf] in (1, True, "1") else "否"
    return row


# ── WHERE 构建（PG 原生 SQL，不走 DuckDB 时间戳转换） ──────


def _sql_escape(val: object) -> str:
    return str(val).replace("'", "''")


def _build_copy_where(
    doc_type: str,
    filters: list[ValidatedFilter],
    tr: TimeRange,
    org_id: str | None,
) -> str:
    """构建 WHERE 子句（PG 原生 SQL，值经过单引号转义）。"""
    clauses: list[str] = [
        f"doc_type = '{_sql_escape(doc_type)}'",
        f"{tr.time_col} >= '{_sql_escape(tr.start_iso)}'",
        f"{tr.time_col} < '{_sql_escape(tr.end_iso)}'",
    ]
    if org_id:
        clauses.append(f"org_id = '{_sql_escape(org_id)}'")
    else:
        clauses.append("org_id IS NULL")

    non_time = [f for f in filters if f.field not in TIME_COLUMNS]
    for f in non_time:
        v = _sql_escape(f.value)
        op_map = {
            "eq": f"{f.field} = '{v}'",
            "ne": f"{f.field} != '{v}'",
            "gt": f"{f.field} > '{v}'",
            "gte": f"{f.field} >= '{v}'",
            "lt": f"{f.field} < '{v}'",
            "lte": f"{f.field} <= '{v}'",
            "like": f"{f.field} ILIKE '{v}'",
        }
        if f.op in op_map:
            clauses.append(op_map[f.op])
        elif f.op == "in" and isinstance(f.value, list) and f.value:
            in_vals = ", ".join(f"'{_sql_escape(x)}'" for x in f.value)
            clauses.append(f"{f.field} IN ({in_vals})")
        elif f.op == "not_in" and isinstance(f.value, list) and f.value:
            not_in_vals = ", ".join(f"'{_sql_escape(x)}'" for x in f.value)
            clauses.append(f"{f.field} NOT IN ({not_in_vals})")
        elif f.op == "is_null":
            if f.value in (True, "true", 1):
                clauses.append(f"{f.field} IS NULL")
            else:
                clauses.append(f"{f.field} IS NOT NULL")
        elif f.op == "between" and isinstance(f.value, list) and len(f.value) == 2:
            clauses.append(
                f"{f.field} BETWEEN '{_sql_escape(f.value[0])}' AND '{_sql_escape(f.value[1])}'"
            )
    return " AND ".join(clauses)


def _need_archive(tr: TimeRange) -> bool:
    """判断是否需要查归档表（时间范围早于 90 天前）。"""
    try:
        start = datetime.fromisoformat(tr.start_iso.replace("Z", "+00:00"))
        cutoff = datetime.now(start.tzinfo) - timedelta(days=90)
        return start < cutoff
    except (ValueError, AttributeError):
        return False


# ── 核心导出函数 ──────────────────────────────────────


PushThinking = Callable[[str], Coroutine[Any, Any, None]]


async def copy_streaming_export(
    doc_type: str,
    filters: list[ValidatedFilter],
    tr: TimeRange,
    org_id: str | None,
    columns: list[str],
    sort_by: str | None = None,
    sort_dir: str = "desc",
    limit: int | None = None,
    push_thinking: PushThinking | None = None,
    user_id: str | None = None,
    conversation_id: str | None = None,
) -> dict:
    """PG COPY 流式导出——突破 DuckDB 30k 行限制。

    Returns:
        {"row_count": int, "size_kb": float, "path": str, "rel_path": str}
    """
    from core.config import get_settings

    settings = get_settings()
    t0 = asyncio.get_event_loop().time()

    # 1. 构建 SQL
    where = _build_copy_where(doc_type, filters, tr, org_id)
    select_cols = ", ".join(columns)

    if _need_archive(tr):
        sql = (
            f"SELECT {select_cols} FROM erp_document_items WHERE {where} "
            f"UNION ALL "
            f"SELECT {select_cols} FROM erp_document_items_archive WHERE {where}"
        )
    else:
        sql = f"SELECT {select_cols} FROM erp_document_items WHERE {where}"

    if sort_by and sort_by in columns:
        sql += f" ORDER BY {sort_by} {sort_dir}"
    if limit:
        sql += f" LIMIT {limit}"

    copy_sql = f"COPY ({sql}) TO STDOUT"

    # 2. 构建 pyarrow schema（所有列用 string，翻译后的值都是文本）
    cn_columns = [_FIELD_LABEL_CN.get(c, c) for c in columns]
    arrow_schema = pa.schema([(cn, pa.string()) for cn in cn_columns])

    # 3. 准备 staging 路径
    from services.kuaimai.erp_duckdb_helpers import resolve_export_path
    _, rel_path, staging_path, _ = resolve_export_path(
        doc_type, user_id, org_id, conversation_id,
    )

    # 4. 流式导出
    writer = pq.ParquetWriter(str(staging_path), arrow_schema, compression="snappy")
    total_rows = 0
    batch_rows: list[dict] = []

    if push_thinking:
        await push_thinking("正在连接数据库...")

    try:
        async with await psycopg.AsyncConnection.connect(
            settings.database_url,
            autocommit=True,
        ) as conn:
            async with conn.cursor() as cur:
                async with cur.copy(copy_sql) as copy:
                    async for row in copy.rows():
                        row_dict = dict(zip(columns, row))

                        # PII 脱敏
                        for pii_field in _PII_FIELDS:
                            if pii_field in row_dict:
                                row_dict[pii_field] = _mask_pii_value(
                                    pii_field,
                                    str(row_dict[pii_field]) if row_dict[pii_field] else None,
                                )

                        # 字段翻译
                        row_dict = _translate_row(row_dict)

                        # 列名翻译（英文 → 中文）
                        cn_row = {
                            _FIELD_LABEL_CN.get(k, k): (str(v) if v is not None else None)
                            for k, v in row_dict.items()
                        }
                        batch_rows.append(cn_row)

                        if len(batch_rows) >= BATCH_SIZE:
                            _write_batch(writer, batch_rows, arrow_schema)
                            total_rows += len(batch_rows)
                            batch_rows.clear()

                            if push_thinking:
                                elapsed = asyncio.get_event_loop().time() - t0
                                await push_thinking(
                                    f"正在导出... {total_rows:,} 行（{elapsed:.0f}s）"
                                )

                    # 最后一批
                    if batch_rows:
                        _write_batch(writer, batch_rows, arrow_schema)
                        total_rows += len(batch_rows)
    finally:
        writer.close()

    size_kb = staging_path.stat().st_size / 1024
    elapsed = asyncio.get_event_loop().time() - t0

    logger.info(
        f"COPY streaming export done | doc_type={doc_type} rows={total_rows:,} "
        f"size={size_kb:.0f}KB elapsed={elapsed:.1f}s"
    )

    if push_thinking:
        size_str = f"{size_kb/1024:.1f}MB" if size_kb > 1024 else f"{size_kb:.0f}KB"
        await push_thinking(f"导出完成：{total_rows:,} 行，{size_str}（{elapsed:.0f}s）")

    return {
        "row_count": total_rows,
        "size_kb": round(size_kb, 1),
        "path": str(staging_path),
        "rel_path": rel_path,
    }


def _write_batch(writer: pq.ParquetWriter, rows: list[dict], schema: pa.Schema) -> None:
    """将一批行写入 parquet。"""
    table = pa.Table.from_pylist(rows, schema=schema)
    writer.write_table(table)
