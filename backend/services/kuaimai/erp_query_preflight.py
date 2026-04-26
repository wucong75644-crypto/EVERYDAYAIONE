"""查询预检防御层（Query Preflight Guard）

所有部门 Agent 共享的预检门卫：用 EXPLAIN 估算结果集大小，
超过安全阈值（3 万行）时拒绝执行，返回原因和建议给上层 Agent。

能做就做，做不了就说做不了。

设计文档：docs/document/TECH_查询预检防御层.md
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loguru import logger

from services.kuaimai.erp_unified_schema import TIME_COLUMNS


# DuckDB 远程扫描安全上限（生产实测：历史最大成功 29,422 行）
EXPORT_ROW_LIMIT = 30_000


@dataclass(frozen=True)
class PreflightResult:
    """预检结果"""
    ok: bool                     # True=可以执行, False=拒绝
    estimated_rows: int          # EXPLAIN 估算行数（-1 表示估算失败）
    reject_reason: str = ""      # 拒绝原因（ok=True 时为空）
    suggestions: tuple[str, ...] = ()  # 给上层 Agent 的建议


def preflight_check(
    db: Any,
    doc_type: str,
    time_col: str,
    start_iso: str,
    end_iso: str,
    org_id: str | None,
    mode: str,
) -> PreflightResult:
    """预检门卫：估算数据量，超阈值则拒绝。

    summary 模式：不拦截（RPC 在 PG 侧聚合，不受行数影响）。
    export 模式：超过 EXPORT_ROW_LIMIT 拒绝，返回原因和建议。
    EXPLAIN 失败：静默放行（防御层不能成为新故障点）。
    """
    # summary 走 RPC，PG 侧聚合，不需要预检
    if mode != "export":
        return PreflightResult(ok=True, estimated_rows=-1)

    try:
        estimated = _explain_estimate(
            db, doc_type, time_col, start_iso, end_iso, org_id,
        )
    except Exception as e:
        logger.warning(f"preflight EXPLAIN failed, allowing execution | error={e}")
        return PreflightResult(ok=True, estimated_rows=-1)

    logger.info(
        f"preflight | estimated={estimated:,} | mode={mode} | "
        f"doc_type={doc_type} | limit={EXPORT_ROW_LIMIT:,}"
    )

    if estimated > EXPORT_ROW_LIMIT:
        return PreflightResult(
            ok=False,
            estimated_rows=estimated,
            reject_reason=(
                f"数据量过大（预估 {estimated:,} 行，上限 {EXPORT_ROW_LIMIT:,} 行），"
                f"导出可能超时失败"
            ),
            suggestions=(
                "缩小时间范围（如改为最近 7 天）",
                "添加平台/店铺/商品编码等过滤条件",
                "改用 summary 模式先查看统计汇总",
            ),
        )

    return PreflightResult(ok=True, estimated_rows=estimated)


def _explain_estimate(
    db: Any,
    doc_type: str,
    time_col: str,
    start_iso: str,
    end_iso: str,
    org_id: str | None,
) -> int:
    """用 EXPLAIN (FORMAT JSON) 估算结果集行数。

    基于 PostgreSQL 统计信息，零成本（<5ms），精度 ±2x。
    """
    if time_col not in TIME_COLUMNS:
        raise ValueError(f"invalid time_col for preflight: {time_col}")

    clauses = [
        "doc_type = %(doc_type)s",
        f"{time_col} >= %(start)s",
        f"{time_col} < %(end)s",
    ]
    params: dict[str, Any] = {
        "doc_type": doc_type,
        "start": start_iso,
        "end": end_iso,
    }

    if org_id:
        clauses.append("org_id = %(org_id)s")
        params["org_id"] = org_id
    else:
        clauses.append("org_id IS NULL")

    where = " AND ".join(clauses)
    sql = f"EXPLAIN (FORMAT JSON) SELECT 1 FROM erp_document_items WHERE {where}"

    with db.pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()

    plan_data = row["QUERY PLAN"]
    plan_rows = plan_data[0]["Plan"]["Plan Rows"]
    return int(plan_rows)
