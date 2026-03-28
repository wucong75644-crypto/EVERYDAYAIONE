"""
ERP 同步死信队列（Dead Letter Queue）

detail API 调用失败时，将单据信息写入 erp_sync_dead_letter 表。
独立消费者协程以指数退避策略异步重试，不阻塞主同步流程。

设计参考：Queue-Based Exponential Backoff + DLQ Pattern
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from typing import Any

from loguru import logger


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
                    "updated_at": datetime.now().isoformat(),
                }).eq("id", existing.data[0]["id"]).execute()
            else:
                await db.table("erp_sync_dead_letter").insert({
                    "doc_type": doc_type,
                    "doc_id": doc_id,
                    "detail_method": detail_method,
                    "doc_json": json.dumps(doc, ensure_ascii=False),
                    "retry_count": 0,
                    "max_retries": DEFAULT_MAX_RETRIES,
                    "next_retry_at": datetime.now().isoformat(),
                    "status": "pending",
                    "last_error": str(error_msg)[:500],
                    "org_id": org_id,
                    "created_at": datetime.now().isoformat(),
                    "updated_at": datetime.now().isoformat(),
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


# ── 消费者（Worker 内独立协程）──────────────────────────


def _calc_next_retry(retry_count: int) -> str:
    """计算下次重试时间（指数退避 + 上限）"""
    delay = min(
        BACKOFF_BASE_SECONDS * (2 ** retry_count),
        BACKOFF_MAX_SECONDS,
    )
    return (datetime.now() + timedelta(seconds=delay)).isoformat()


async def consume_dead_letters(db: Any, is_running_fn) -> None:
    """死信消费者主循环

    Args:
        db: 数据库客户端
        is_running_fn: callable，返回 False 时退出循环
    """
    from services.kuaimai.client import KuaiMaiClient
    client = KuaiMaiClient()

    logger.info("Dead letter consumer started")

    while is_running_fn():
        try:
            processed = await _process_batch(db, client)
            if processed > 0:
                logger.info(f"Dead letter processed | count={processed}")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Dead letter consumer error | error={e}")

        await asyncio.sleep(CONSUMER_POLL_INTERVAL)

    logger.info("Dead letter consumer stopped")


async def _process_batch(db: Any, client: Any = None) -> int:
    """处理一批待重试的死信"""
    # 查询到期的 pending 记录
    now = datetime.now().isoformat()
    try:
        result = await (
            db.table("erp_sync_dead_letter")
            .select("*")
            .eq("status", "pending")
            .lte("next_retry_at", now)
            .order("next_retry_at")
            .limit(CONSUMER_BATCH_SIZE)
            .execute()
        )
    except Exception as e:
        logger.error(f"Dead letter query failed | error={e}")
        return 0

    rows = result.data or []
    if not rows:
        return 0

    # 按 org_id 分组，每组用对应企业的 client
    from services.kuaimai.client import KuaiMaiClient

    org_groups: dict[str | None, list[dict]] = {}
    for row in rows:
        org_id = row.get("org_id")
        org_groups.setdefault(org_id, []).append(row)

    processed = 0
    for org_id, org_rows in org_groups.items():
        # 为每个企业加载凭证
        org_client = client
        if org_client is None:
            if org_id:
                try:
                    from services.org.config_resolver import OrgConfigResolver
                    resolver = OrgConfigResolver(db)
                    creds = resolver.get_erp_credentials(org_id)
                    org_client = KuaiMaiClient(
                        app_key=creds["kuaimai_app_key"],
                        app_secret=creds["kuaimai_app_secret"],
                        access_token=creds["kuaimai_access_token"],
                        refresh_token=creds["kuaimai_refresh_token"],
                    )
                except ValueError as e:
                    logger.warning(f"Skip dead letter retry for org {org_id}: {e}")
                    continue
            else:
                org_client = KuaiMaiClient()

        try:
            for row in org_rows:
                try:
                    await _retry_one(db, org_client, row)
                    processed += 1
                except Exception as e:
                    logger.error(
                        f"Dead letter retry error | id={row.get('id')} | "
                        f"doc_type={row.get('doc_type')} | org_id={org_id} | error={e}"
                    )
        finally:
            if org_client is not client and org_client:
                await org_client.close()

    return processed


async def _retry_one(db: Any, client: Any, row: dict) -> None:
    """重试单条死信"""
    dl_id = row["id"]
    doc_type = row["doc_type"]
    doc_id = row["doc_id"]
    detail_method = row["detail_method"]
    doc_json = row["doc_json"]
    retry_count = row["retry_count"]
    max_retries = row["max_retries"]

    # 解析 doc
    if isinstance(doc_json, str):
        doc = json.loads(doc_json)
    else:
        doc = doc_json

    # 调 detail API
    try:
        detail = await client.request_with_retry(
            detail_method, {"id": doc["id"]}
        )
    except Exception as e:
        # 重试失败
        new_count = retry_count + 1
        if new_count >= max_retries:
            # 超过上限 → 标记 dead
            await db.table("erp_sync_dead_letter").update({
                "status": "dead",
                "retry_count": new_count,
                "last_error": str(e)[:500],
                "updated_at": datetime.now().isoformat(),
            }).eq("id", dl_id).execute()
            logger.error(
                f"Dead letter exhausted | doc_type={doc_type} | "
                f"doc_id={doc_id} | retries={new_count}"
            )
        else:
            # 递增重试次数 + 指数退避
            await db.table("erp_sync_dead_letter").update({
                "retry_count": new_count,
                "next_retry_at": _calc_next_retry(new_count),
                "last_error": str(e)[:500],
                "updated_at": datetime.now().isoformat(),
            }).eq("id", dl_id).execute()
            logger.warning(
                f"Dead letter retry failed | doc_type={doc_type} | "
                f"doc_id={doc_id} | attempt={new_count}/{max_retries} | "
                f"error={e}"
            )
        return

    # detail 成功 → 构建行并 upsert
    try:
        from services.kuaimai.erp_sync_dead_letter_handlers import (
            build_rows_from_detail,
        )
        rows = build_rows_from_detail(doc_type, doc, detail)

        if rows:
            from services.kuaimai.erp_sync_service import ErpSyncService
            dl_org_id = row.get("org_id")
            svc = ErpSyncService(db, org_id=dl_org_id)
            count = await svc.upsert_document_items(rows)
            await svc.run_aggregation(svc.collect_affected_keys(rows))
            logger.info(
                f"Dead letter recovered | doc_type={doc_type} | "
                f"doc_id={doc_id} | rows={count}"
            )

        # 删除已处理的死信
        await db.table("erp_sync_dead_letter").delete().eq("id", dl_id).execute()
    except Exception as e:
        # upsert 失败也要递增 retry_count + 退避，防止无限重试
        new_count = retry_count + 1
        if new_count >= max_retries:
            await db.table("erp_sync_dead_letter").update({
                "status": "dead",
                "retry_count": new_count,
                "last_error": f"upsert failed: {e}"[:500],
                "updated_at": datetime.now().isoformat(),
            }).eq("id", dl_id).execute()
        else:
            await db.table("erp_sync_dead_letter").update({
                "retry_count": new_count,
                "next_retry_at": _calc_next_retry(new_count),
                "last_error": f"upsert failed: {e}"[:500],
                "updated_at": datetime.now().isoformat(),
            }).eq("id", dl_id).execute()
        logger.error(
            f"Dead letter upsert failed | doc_type={doc_type} | "
            f"doc_id={doc_id} | error={e}"
        )
