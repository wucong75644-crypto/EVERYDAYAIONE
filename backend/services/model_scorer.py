"""
模型动态评分

每小时从 knowledge_metrics 聚合模型表现（7 天窗口），
计算综合评分 → EMA 平滑 → 写入 knowledge_nodes（路由自动注入）。

由 BackgroundTaskWorker 定时调用，fire-and-forget 不阻塞主流程。
"""

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from services.knowledge_config import create_dedicated_connection, is_kb_available
from services.knowledge_service import add_knowledge

# ===== 常量 =====

# EMA 平滑系数（新数据权重 20%）
EMA_ALPHA = 0.2

# 聚合窗口（天）
AGGREGATION_WINDOW_DAYS = 7

# 延迟评分基准（30 秒为最差，超过此值得 0 分）
LATENCY_MAX_MS = 30000

# 加权公式权重
WEIGHT_SUCCESS = 0.40
WEIGHT_LATENCY = 0.25
WEIGHT_RETRY = 0.15
WEIGHT_ERROR = 0.10
WEIGHT_BASELINE = 0.10  # 占位（后续替换为用户粘性）

# Confidence 分级阈值
CONFIDENCE_LOW_THRESHOLD = 10
CONFIDENCE_MID_THRESHOLD = 50

# 审核规则阈值
REVIEW_SCORE_CHANGE_THRESHOLD = 0.1
REVIEW_MIN_SAMPLE_COUNT = 20


# ===== 主入口 =====


async def aggregate_model_scores(org_id: str | None = None) -> None:
    """
    每小时聚合模型评分（由 BackgroundTaskWorker 按 org 迭代调用）

    使用独立连接（不走共享池），避免后台批量任务与在线业务争抢连接。
    单条 SQL 15 秒超时，防止慢查询无限 hang 拖垮 worker。

    流程：聚合 SQL → 计算 raw_score → EMA → 审核判断 → 写入知识库/日志
    """
    if not is_kb_available():
        return

    conn = await create_dedicated_connection(statement_timeout_s=15)
    if conn is None:
        return

    try:
        async with conn:
            rows = await _query_aggregated_metrics(conn, org_id=org_id)
            if not rows:
                logger.debug("Model scoring skipped | no metrics data")
                return

            applied_count = 0
            review_count = 0

            for row in rows:
                try:
                    raw_score = _compute_raw_score(row)
                    old_score = await _get_latest_score(
                        conn, row["model_id"], row["task_type"], org_id=org_id,
                    )
                    ema_score = _apply_ema(raw_score, old_score)
                    confidence = _get_confidence(row["total"])
                    status = _determine_status(ema_score, old_score, row["total"])

                    node_id = None
                    if status == "auto_applied":
                        node_id = await _write_score_to_knowledge(
                            row, ema_score, confidence, org_id=org_id,
                        )
                        applied_count += 1
                    else:
                        review_count += 1

                    await _write_audit_log(
                        conn, row, old_score, ema_score, status, node_id,
                        org_id=org_id,
                    )
                except Exception as e:
                    logger.warning(
                        f"Scoring failed for model | model={row['model_id']} | "
                        f"task={row['task_type']} | error={e}"
                    )

            logger.info(
                f"Model scoring completed | models={len(rows)} | "
                f"applied={applied_count} | pending_review={review_count}"
            )
    except Exception as e:
        logger.error(f"Model scoring connection failed | error={e}")


# ===== 聚合查询 =====


async def _query_aggregated_metrics(
    conn, org_id: str | None = None,
) -> List[Dict[str, Any]]:
    """从 knowledge_metrics 聚合 7 天内模型表现数据（按 org 隔离）"""
    org_filter = (
        "AND org_id = %(org_id)s" if org_id
        else "AND org_id IS NULL"
    )

    sql = f"""
    SELECT
        model_id,
        task_type,
        COUNT(*) AS total,
        COUNT(*) FILTER (WHERE status = 'success') AS success_count,
        PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY cost_time_ms)
            FILTER (WHERE status = 'success' AND cost_time_ms IS NOT NULL)
            AS p75_latency,
        COUNT(*) FILTER (WHERE retried = TRUE) AS retry_count,
        COUNT(*) FILTER (WHERE error_code = 'timeout') AS timeout_count,
        COUNT(*) FILTER (
            WHERE error_code IS NOT NULL
            AND error_code NOT IN ('timeout', 'rate_limit')
        ) AS hard_error_count,
        MIN(created_at) AS period_start,
        MAX(created_at) AS period_end
    FROM knowledge_metrics
    WHERE created_at > NOW() - INTERVAL '{AGGREGATION_WINDOW_DAYS} days'
      {org_filter}
    GROUP BY model_id, task_type
    HAVING COUNT(*) >= 1;
    """

    try:
        async with conn.cursor() as cur:
            await cur.execute(sql, {"org_id": org_id})
            rows = await cur.fetchall()
            if not rows:
                return []
            columns = [desc.name for desc in cur.description]
            return [dict(zip(columns, row)) for row in rows]
    except Exception as e:
        logger.error(f"Metrics aggregation query failed | error={e}")
        return []


# ===== 评分计算 =====


def _compute_raw_score(row: Dict[str, Any]) -> float:
    """
    加权综合评分（0-1）

    权重：成功率 40% + 延迟 25% + 重试 15% + 硬错误 10% + 基准 10%
    """
    total = row["total"]
    if total == 0:
        return 0.0

    success_rate = row["success_count"] / total

    p75 = row.get("p75_latency") or 0
    latency_score = max(0.0, 1.0 - p75 / LATENCY_MAX_MS)

    retry_score = max(0.0, 1.0 - row["retry_count"] / total)

    hard_errors = row.get("hard_error_count", 0)
    error_score = max(0.0, 1.0 - hard_errors * 2 / total)

    raw = (
        WEIGHT_SUCCESS * success_rate
        + WEIGHT_LATENCY * latency_score
        + WEIGHT_RETRY * retry_score
        + WEIGHT_ERROR * error_score
        + WEIGHT_BASELINE * 1.0  # 占位：后续替换为用户粘性指标
    )
    return round(min(1.0, max(0.0, raw)), 4)


def _apply_ema(raw_score: float, old_score: Optional[float]) -> float:
    """EMA 平滑：new = α × raw + (1-α) × old"""
    if old_score is None:
        return raw_score
    return round(EMA_ALPHA * raw_score + (1 - EMA_ALPHA) * old_score, 4)


def _get_confidence(sample_count: int) -> float:
    """按样本量分级 confidence"""
    if sample_count < CONFIDENCE_LOW_THRESHOLD:
        return 0.3
    if sample_count < CONFIDENCE_MID_THRESHOLD:
        return 0.7
    return 0.9


def _determine_status(
    ema_score: float, old_score: Optional[float], sample_count: int
) -> str:
    """
    判断审核状态

    pending_review: 分数变化 ≥0.1 或样本量 <20
    auto_applied: 其余情况
    """
    score_change = abs(ema_score - (old_score if old_score is not None else ema_score))
    if score_change >= REVIEW_SCORE_CHANGE_THRESHOLD:
        return "pending_review"
    if sample_count < REVIEW_MIN_SAMPLE_COUNT:
        return "pending_review"
    return "auto_applied"


# ===== 历史评分查询 =====


async def _get_latest_score(
    conn, model_id: str, task_type: str, org_id: str | None = None,
) -> Optional[float]:
    """查询最近一次已生效的评分（auto_applied 或 approved，按 org 隔离）"""
    org_filter = (
        "AND org_id = %(org_id)s" if org_id
        else "AND org_id IS NULL"
    )

    try:
        async with conn.cursor() as cur:
            await cur.execute(
                f"""
                SELECT new_score FROM scoring_audit_log
                WHERE model_id = %(model_id)s
                    AND task_type = %(task_type)s
                    AND status IN ('auto_applied', 'approved')
                    {org_filter}
                ORDER BY created_at DESC
                LIMIT 1;
                """,
                {"model_id": model_id, "task_type": task_type, "org_id": org_id},
            )
            row = await cur.fetchone()
            return float(row[0]) if row else None
    except Exception as e:
        logger.warning(
            f"Get latest score failed | model={model_id} | "
            f"task={task_type} | error={e}"
        )
        return None


# ===== 知识库写入 =====


async def _write_score_to_knowledge(
    row: Dict[str, Any], score: float, confidence: float,
    org_id: str | None = None,
) -> Optional[str]:
    """将评分作为知识节点写入（add_knowledge 自动 hash 去重/更新）"""
    model_id = row["model_id"]
    task_type = row["task_type"]
    total = row["total"]
    success_rate = round(row["success_count"] / total * 100, 1) if total else 0
    p75_ms = round(row.get("p75_latency") or 0)
    retry_rate = round(row["retry_count"] / total * 100, 1) if total else 0

    period_start, period_end = _format_period(row)

    title = f"{model_id} {task_type} 近期表现评分"
    content = (
        f"成功率{success_rate}% | P75延迟{p75_ms}ms | "
        f"重试率{retry_rate}% | 综合评分{score}/1.0"
    )

    metadata = {
        "model_id": model_id,
        "score": score,
        "task_type": task_type,
        "metrics": {
            "success_rate": success_rate,
            "p75_latency_ms": p75_ms,
            "retry_rate": retry_rate,
            "hard_error_count": row.get("hard_error_count", 0),
            "timeout_count": row.get("timeout_count", 0),
        },
        "sample_count": total,
        "period": f"{period_start}~{period_end}",
    }

    return await add_knowledge(
        category="model",
        subcategory=task_type,
        node_type="performance",
        title=title,
        content=content,
        metadata=metadata,
        source="aggregated",
        confidence=confidence,
        org_id=org_id,
    )


# ===== 审核日志 =====


async def _write_audit_log(
    conn,
    row: Dict[str, Any],
    old_score: Optional[float],
    new_score: float,
    status: str,
    knowledge_node_id: Optional[str],
    org_id: str | None = None,
) -> None:
    """写入 scoring_audit_log 审核记录（按 org 隔离）"""
    score_change = round(
        abs(new_score - (old_score if old_score is not None else new_score)), 4
    )
    period_start, period_end = _format_period_dt(row)

    metrics = {
        "success_rate": round(
            row["success_count"] / row["total"] * 100, 2
        ) if row["total"] else 0,
        "p75_latency_ms": round(row.get("p75_latency") or 0),
        "retry_count": row["retry_count"],
        "hard_error_count": row.get("hard_error_count", 0),
        "timeout_count": row.get("timeout_count", 0),
    }

    try:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO scoring_audit_log (
                    model_id, task_type, old_score, new_score,
                    score_change, sample_count, metrics,
                    period_start, period_end, status, knowledge_node_id,
                    org_id
                ) VALUES (
                    %(model_id)s, %(task_type)s, %(old_score)s, %(new_score)s,
                    %(score_change)s, %(sample_count)s, %(metrics)s,
                    %(period_start)s, %(period_end)s, %(status)s,
                    %(knowledge_node_id)s, %(org_id)s
                );
                """,
                {
                    "model_id": row["model_id"],
                    "task_type": row["task_type"],
                    "old_score": old_score,
                    "new_score": new_score,
                    "score_change": score_change,
                    "sample_count": row["total"],
                    "metrics": json.dumps(metrics),
                    "period_start": period_start,
                    "period_end": period_end,
                    "status": status,
                    "knowledge_node_id": knowledge_node_id,
                    "org_id": org_id,
                },
            )
        await conn.commit()
    except Exception as e:
        logger.warning(
            f"Audit log write failed | model={row['model_id']} | "
            f"task={row['task_type']} | error={e}"
        )


# ===== 工具函数 =====


def _format_period(row: Dict[str, Any]) -> Tuple[str, str]:
    """格式化聚合窗口为 YYYY-MM-DD 字符串"""
    start = row.get("period_start")
    end = row.get("period_end")
    fmt_start = start.strftime("%Y-%m-%d") if hasattr(start, "strftime") else str(start)[:10]
    fmt_end = end.strftime("%Y-%m-%d") if hasattr(end, "strftime") else str(end)[:10]
    return fmt_start, fmt_end


def _format_period_dt(row: Dict[str, Any]) -> Tuple[datetime, datetime]:
    """获取聚合窗口的 datetime（用于写入 TIMESTAMPTZ 字段）"""
    start = row.get("period_start")
    end = row.get("period_end")

    if isinstance(start, datetime):
        return start, end

    # 兜底：如果是字符串则解析
    now = datetime.now(timezone.utc)
    return (
        datetime.fromisoformat(str(start)) if start else now,
        datetime.fromisoformat(str(end)) if end else now,
    )
