"""
DuckDB 导出辅助函数 — SQL 构建、PII 脱敏、路径解析、预览。

从 erp_unified_query.py 拆出，保持引擎文件 < 500 行。
设计文档: docs/document/TECH_DuckDB导出引擎.md
"""

from __future__ import annotations

import time as _time
from datetime import datetime
from pathlib import Path

from services.kuaimai.erp_unified_schema import TIME_COLUMNS, TimeRange, ValidatedFilter


def _to_duckdb_timestamp(iso_str: str) -> str:
    """ISO 时间字符串 → DuckDB 兼容格式 YYYY-MM-DD HH:MM:SS。

    DuckDB 不接受带时区的 ISO 格式（如 2026-04-19T00:00+08:00），
    Supabase RPC 可以。此函数仅供 DuckDB SQL 使用。
    """
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, AttributeError):
        return iso_str


# ── PII 脱敏 SQL 表达式 ──────────────────────────────


_PII_SQL_MAP: dict[str, str] = {
    "receiver_name": (
        "CASE WHEN receiver_name IS NOT NULL AND length(receiver_name) >= 2 "
        "THEN substr(receiver_name, 1, 1) || repeat('*', length(receiver_name) - 1) "
        "ELSE receiver_name END AS receiver_name"
    ),
    "receiver_mobile": (
        "CASE WHEN receiver_mobile IS NOT NULL AND length(receiver_mobile) >= 7 "
        "THEN substr(receiver_mobile, 1, 3) || '****' || substr(receiver_mobile, -4) "
        "ELSE receiver_mobile END AS receiver_mobile"
    ),
    "receiver_phone": (
        "CASE WHEN receiver_phone IS NOT NULL AND length(receiver_phone) >= 7 "
        "THEN substr(receiver_phone, 1, 3) || '****' || substr(receiver_phone, -4) "
        "ELSE receiver_phone END AS receiver_phone"
    ),
    "receiver_address": (
        "CASE WHEN receiver_address IS NOT NULL AND length(receiver_address) >= 6 "
        "THEN substr(receiver_address, 1, 6) || '****' "
        "ELSE receiver_address END AS receiver_address"
    ),
}


def build_pii_select(safe_fields: list[str]) -> str:
    """构建 SELECT 列表，PII 字段自动替换为脱敏 CASE WHEN 表达式。"""
    cols = [_PII_SQL_MAP.get(f, f) for f in safe_fields]
    return ", ".join(cols)


# ── SQL 构建 ─────────────────────────────────────────


def _sql_escape(val: object) -> str:
    """转义 SQL 字符串中的单引号，防止语法错误。"""
    return str(val).replace("'", "''")


def build_export_where(
    doc_type: str,
    filters: list[ValidatedFilter],
    tr: TimeRange,
    org_id: str | None,
) -> str:
    """构建 WHERE 子句（纯 SQL 字符串，值经过单引号转义——仅用于 DuckDB COPY）。"""
    clauses: list[str] = [
        f"doc_type = '{_sql_escape(doc_type)}'",
        f"{tr.time_col} >= '{_sql_escape(_to_duckdb_timestamp(tr.start_iso))}'",
        f"{tr.time_col} < '{_sql_escape(_to_duckdb_timestamp(tr.end_iso))}'",
    ]

    if org_id:
        clauses.append(f"org_id = '{_sql_escape(org_id)}'")
    else:
        clauses.append("org_id IS NULL")

    non_time = [f for f in filters if f.field not in TIME_COLUMNS]
    for f in non_time:
        v = _sql_escape(f.value)
        if f.op == "eq":
            clauses.append(f"{f.field} = '{v}'")
        elif f.op == "ne":
            clauses.append(f"{f.field} != '{v}'")
        elif f.op == "gt":
            clauses.append(f"{f.field} > '{v}'")
        elif f.op == "gte":
            clauses.append(f"{f.field} >= '{v}'")
        elif f.op == "lt":
            clauses.append(f"{f.field} < '{v}'")
        elif f.op == "lte":
            clauses.append(f"{f.field} <= '{v}'")
        elif f.op == "like":
            clauses.append(f"{f.field} ILIKE '{v}'")
        elif f.op == "in" and isinstance(f.value, list) and f.value:
            in_vals = ", ".join(f"'{_sql_escape(x)}'" for x in f.value)
            clauses.append(f"{f.field} IN ({in_vals})")
        elif f.op == "is_null":
            if f.value is True or f.value == "true" or f.value == 1:
                clauses.append(f"{f.field} IS NULL")
            else:
                clauses.append(f"{f.field} IS NOT NULL")
        elif f.op == "between" and isinstance(f.value, list) and len(f.value) == 2:
            clauses.append(
                f"{f.field} BETWEEN '{_sql_escape(f.value[0])}' AND '{_sql_escape(f.value[1])}'"
            )

    return " AND ".join(clauses)


# ── 路径解析 ─────────────────────────────────────────


def resolve_export_path(
    doc_type: str,
    user_id: str | None,
    org_id: str | None,
    conversation_id: str | None,
) -> tuple[Path, str, Path, str]:
    """
    构建导出 Parquet 的 staging 路径。

    返回: (staging_dir, rel_path, staging_path, filename)
    路径格式与 _write_parquet 完全一致，确保沙盒 STAGING_DIR 不变。
    """
    from core.config import get_settings
    from core.workspace import resolve_staging_dir, resolve_staging_rel_path

    settings = get_settings()
    conv_id = conversation_id or "default"
    staging_dir = Path(resolve_staging_dir(
        settings.file_workspace_root,
        user_id=user_id or "", org_id=org_id,
        conversation_id=conv_id,
    ))
    staging_dir.mkdir(parents=True, exist_ok=True)

    ts = int(_time.time())
    filename = f"local_{doc_type}_{ts}.parquet"
    staging_path = staging_dir / filename
    rel_path = resolve_staging_rel_path(conversation_id=conv_id, filename=filename)

    return staging_dir, rel_path, staging_path, filename

