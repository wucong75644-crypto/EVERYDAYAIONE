"""查询预检防御层（Query Preflight Guard）

在 summary/export 两个模式之前，用 EXPLAIN 估算结果集大小，
三级路由决策：快路径 / 标准路径 / 分批路径。

设计文档：docs/document/TECH_查询预检防御层.md v2.0
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from loguru import logger

from services.kuaimai.erp_unified_schema import TIME_COLUMNS


# ── 路由决策 ──────────────────────────────────────────

class QueryRoute(str, Enum):
    """预检路由结果"""
    FAST = "fast"          # < FAST_THRESHOLD：PG 直查
    STANDARD = "standard"  # FAST_THRESHOLD ~ BATCH_THRESHOLD：走现有路径
    BATCH = "batch"        # > BATCH_THRESHOLD 且 export：分批 DuckDB


# 阈值（基于生产实测 2026-04-26）
FAST_THRESHOLD = 1_000       # PG 直查上限（ORDER BY + LIMIT 1000 = 0.39s）
BATCH_THRESHOLD = 30_000     # 单次 DuckDB 安全上限（历史最大成功 29,422 行）
REJECT_THRESHOLD = 5_000_000 # 极端场景兜底


@dataclass(frozen=True)
class PreflightResult:
    """预检结果"""
    estimated_rows: int
    route: QueryRoute
    # 仅 REJECT 场景填充
    reject_reason: str = ""
    suggestions: tuple[str, ...] = ()


# ── 核心函数 ──────────────────────────────────────────

def preflight_check(
    db: Any,
    doc_type: str,
    time_col: str,
    start_iso: str,
    end_iso: str,
    org_id: str | None,
    mode: str,
    filters: list[Any] | None = None,
) -> PreflightResult:
    """执行 EXPLAIN 预检，返回路由决策。

    Args:
        db: LocalDBClient（需要 db.pool 原生连接）
        doc_type: 单据类型
        time_col: 时间列名
        start_iso: 开始时间 ISO 格式
        end_iso: 结束时间 ISO 格式
        org_id: 租户 ID
        mode: "summary" 或 "export"
        filters: ValidatedFilter 列表（可选，暂不纳入 EXPLAIN）

    Returns:
        PreflightResult 含 estimated_rows 和 route 决策
    """
    try:
        estimated = _explain_estimate(
            db, doc_type, time_col, start_iso, end_iso, org_id,
        )
    except Exception as e:
        # 预检失败 → 静默降级走标准路径（防御层不能成为新故障点）
        logger.warning(f"preflight EXPLAIN failed, fallback to standard | error={e}")
        return PreflightResult(estimated_rows=-1, route=QueryRoute.STANDARD)

    route = _decide_route(estimated, mode)

    logger.info(
        f"preflight | estimated={estimated:,} | mode={mode} | "
        f"route={route.value} | doc_type={doc_type}"
    )

    if route == QueryRoute.BATCH and estimated > REJECT_THRESHOLD:
        return PreflightResult(
            estimated_rows=estimated,
            route=QueryRoute.BATCH,
            reject_reason=f"数据量过大（预估 {estimated:,} 行）",
            suggestions=(
                "缩小时间范围（如改为最近 7 天）",
                "添加平台/店铺过滤条件",
                "改用 summary 模式查看统计汇总",
            ),
        )

    return PreflightResult(estimated_rows=estimated, route=route)


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
    # time_col 白名单校验（防止 SQL 注入，time_col 直接拼入 SQL）
    if time_col not in TIME_COLUMNS:
        raise ValueError(f"invalid time_col for preflight: {time_col}")

    # 构建安全的参数化 SQL
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

    # psycopg dict_row 格式：{"QUERY PLAN": [...]}
    plan_data = row["QUERY PLAN"]
    plan_rows = plan_data[0]["Plan"]["Plan Rows"]
    return int(plan_rows)


def _decide_route(estimated_rows: int, mode: str) -> QueryRoute:
    """根据预估行数和模式决定路由。

    summary 模式：
      < FAST_THRESHOLD → FAST
      >= FAST_THRESHOLD → STANDARD（RPC 不受行数影响）

    export 模式：
      < FAST_THRESHOLD → FAST
      FAST_THRESHOLD ~ BATCH_THRESHOLD → STANDARD（单次 DuckDB）
      > BATCH_THRESHOLD → BATCH（分批 DuckDB）
    """
    if estimated_rows < FAST_THRESHOLD:
        return QueryRoute.FAST

    if mode == "summary":
        # summary RPC 在 PG 侧执行，不受行数限制
        return QueryRoute.STANDARD

    # export 模式
    if estimated_rows <= BATCH_THRESHOLD:
        return QueryRoute.STANDARD

    return QueryRoute.BATCH
