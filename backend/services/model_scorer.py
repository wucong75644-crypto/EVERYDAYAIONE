"""
模型动态评分

每小时从 knowledge_metrics 聚合模型表现（7 天窗口），
计算综合评分 → EMA 平滑 → 写入 knowledge_nodes（路由自动注入）。

由 BackgroundTaskWorker 定时调用，fire-and-forget 不阻塞主流程。
"""

import asyncio
from datetime import datetime, timezone
import inspect
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from core.database import get_worker_db
from core.db_scope import (
    DatabaseAccessKind,
    DatabaseScope,
    ScopedDatabaseClient,
)
from services.knowledge_config import (
    compute_content_hash,
    compute_embedding,
    is_kb_available,
)

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


async def aggregate_model_scores(
    org_id: str | None = None,
    db_source: Any = None,
) -> None:
    """
    每小时聚合模型评分（由 BackgroundTaskWorker 按 org 迭代调用）

    使用独立连接（不走共享池），避免后台批量任务与在线业务争抢连接。
    单条 SQL 15 秒超时，防止慢查询无限 hang 拖垮 worker。

    流程：聚合 SQL → 计算 raw_score → EMA → 审核判断 → 写入知识库/日志
    """
    if not is_kb_available():
        return

    database_scope = DatabaseScope(
        actor_user_id=None,
        org_id=org_id,
        access_kind=DatabaseAccessKind.WORKER,
        request_id="model-scorer",
    )
    db = ScopedDatabaseClient(
        db_source or get_worker_db(),
        database_scope,
    )

    try:
        rows = await _query_aggregated_metrics(db, org_id=org_id)
        if not rows:
            logger.debug("Model scoring skipped | no metrics data")
            return

        applied_count = 0
        review_count = 0

        for row in rows:
            try:
                raw_score = _compute_raw_score(row)
                old_score = (
                    float(row["old_score"])
                    if row.get("old_score") is not None else None
                )
                ema_score = _apply_ema(raw_score, old_score)
                confidence = _get_confidence(row["total"])
                status = _determine_status(
                    ema_score, old_score, row["total"],
                )

                knowledge = None
                if status == "auto_applied":
                    knowledge = await _build_score_knowledge(
                        row, ema_score, confidence,
                    )
                await _commit_model_score(
                    db, row, old_score, ema_score, status, knowledge,
                    org_id=org_id,
                )
                if status == "auto_applied":
                    applied_count += 1
                else:
                    review_count += 1
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
    db: Any, org_id: str | None = None,
) -> List[Dict[str, Any]]:
    """通过 Worker 窄能力读取企业或逐散客模型指标快照。"""
    try:
        payload = await _execute_rpc(
            db, "worker_model_scoring_snapshot", {"p_org_id": org_id},
        )
        if not isinstance(payload, list):
            raise RuntimeError("MODEL_SCORING_SNAPSHOT_INVALID")
        return [row for row in payload if isinstance(row, dict)]
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


# ===== 知识库写入 =====


async def _build_score_knowledge(
    row: Dict[str, Any], score: float, confidence: float,
) -> Dict[str, Any]:
    """构造由 Worker 提交能力写入的评分知识事实。"""
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

    embedding = await compute_embedding(f"{title} {content}")
    return {
        "title": title,
        "content": content,
        "metadata": metadata,
        "confidence": confidence,
        "content_hash": compute_content_hash("model", title, content),
        "embedding": str(embedding) if embedding else None,
    }


# ===== 审核日志 =====


async def _commit_model_score(
    db: Any,
    row: Dict[str, Any],
    old_score: Optional[float],
    new_score: float,
    status: str,
    knowledge: Optional[Dict[str, Any]],
    org_id: str | None = None,
) -> Optional[str]:
    """原子提交评分知识（可选）与审核记录。"""
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

    knowledge = knowledge or {}
    payload = await _execute_rpc(db, "worker_commit_model_score", {
        "p_org_id": org_id,
        "p_owner_user_id": row.get("owner_user_id"),
        "p_model_id": row["model_id"],
        "p_task_type": row["task_type"],
        "p_old_score": old_score,
        "p_new_score": new_score,
        "p_score_change": score_change,
        "p_sample_count": row["total"],
        "p_metrics": metrics,
        "p_period_start": period_start,
        "p_period_end": period_end,
        "p_status": status,
        "p_title": knowledge.get("title"),
        "p_content": knowledge.get("content"),
        "p_metadata": knowledge.get("metadata"),
        "p_confidence": knowledge.get("confidence"),
        "p_content_hash": knowledge.get("content_hash"),
        "p_embedding": knowledge.get("embedding"),
    })
    if not isinstance(payload, dict) or payload.get("outcome") != "recorded":
        raise RuntimeError("MODEL_SCORING_COMMIT_INVALID")
    node_id = payload.get("knowledge_node_id")
    return str(node_id) if node_id else None


async def _execute_rpc(
    db: Any,
    name: str,
    params: Dict[str, Any],
) -> Any:
    """在线程中执行同步 Worker client，并兼容异步测试客户端。"""
    response = await asyncio.to_thread(
        lambda: db.rpc(name, params).execute(),
    )
    if inspect.isawaitable(response):
        response = await response
    return response.data if response is not None else None


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
