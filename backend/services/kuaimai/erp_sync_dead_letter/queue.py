"""ERP 死信队列 — 写入 + 退避计算

主同步流程调用 record_dead_letter 把失败的单据/批次写入 erp_sync_dead_letter 表。
独立消费者协程 (consumer.py) 按指数退避策略异步重试。
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from loguru import logger

from utils.time_context import now_cn


# ── 常量 ────────────────────────────────────────────────

# 退避基数（秒），实际延迟 = BASE * 2^retry_count
BACKOFF_BASE_SECONDS = 5
# 最大退避上限（秒），防止延迟过长
BACKOFF_MAX_SECONDS = 3600
# 默认最大重试次数
DEFAULT_MAX_RETRIES = 10
# 消费者扫描间隔（秒）
CONSUMER_POLL_INTERVAL = 30
# 每轮最多处理条数（避免一次性拉太多）
CONSUMER_BATCH_SIZE = 20


def _calc_next_retry(retry_count: int) -> str:
    """计算下次重试时间（指数退避 + 上限）"""
    delay = min(
        BACKOFF_BASE_SECONDS * (2 ** retry_count),
        BACKOFF_MAX_SECONDS,
    )
    return (now_cn() + timedelta(seconds=delay)).isoformat()


# ── 写入（主同步流程调用）────────────────────────────────


async def record_dead_letter(
    db: Any,
    doc_type: str,
    detail_method: str,
    failed_docs: list[dict],
    error_msg: str = "",
    org_id: str | None = None,
) -> int:
    """将失败的单据写入死信表

    Args:
        db: 数据库客户端
        doc_type: 单据类型（purchase/receipt/shelf/...）
        detail_method: detail API 方法名
        failed_docs: list API 返回的 doc 列表（含 id 等字段）
        error_msg: 最后一次错误信息

    Returns:
        写入成功的条数
    """
    if not failed_docs:
        return 0

    count = 0
    for doc in failed_docs:
        doc_id = str(doc.get("id", ""))
        if not doc_id:
            continue
        try:
            # 检查是否已有 pending 记录（条件唯一索引不支持 ON CONFLICT）
            existing = await (
                db.table("erp_sync_dead_letter")
                .select("id")
                .eq("doc_type", doc_type)
                .eq("doc_id", doc_id)
                .eq("status", "pending")
                .limit(1)
                .execute()
            )
            if existing.data:
                # 已有 pending 记录，更新错误信息和时间
                await db.table("erp_sync_dead_letter").update({
                    "last_error": str(error_msg)[:500],
                    "updated_at": now_cn().isoformat(),
                }).eq("id", existing.data[0]["id"]).execute()
            else:
                await db.table("erp_sync_dead_letter").insert({
                    "doc_type": doc_type,
                    "doc_id": doc_id,
                    "detail_method": detail_method,
                    "doc_json": json.dumps(doc, ensure_ascii=False),
                    "retry_count": 0,
                    "max_retries": DEFAULT_MAX_RETRIES,
                    "next_retry_at": now_cn().isoformat(),
                    "status": "pending",
                    "last_error": str(error_msg)[:500],
                    "org_id": org_id,
                    "created_at": now_cn().isoformat(),
                    "updated_at": now_cn().isoformat(),
                }).execute()
            count += 1
        except Exception as e:
            logger.error(
                f"Dead letter write failed | doc_type={doc_type} | "
                f"doc_id={doc_id} | error={e}"
            )
    if count:
        logger.info(
            f"Dead letter recorded | doc_type={doc_type} | count={count}"
        )
    return count
