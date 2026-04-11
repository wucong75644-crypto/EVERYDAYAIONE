"""
ERP 同步死信队列（Dead Letter Queue）

detail API 调用失败时，将单据信息写入 erp_sync_dead_letter 表。
独立消费者协程以指数退避策略异步重试，不阻塞主同步流程。

设计参考：Queue-Based Exponential Backoff + DLQ Pattern
"""

from __future__ import annotations

import asyncio
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


# ── 消费者（Worker 内独立协程）──────────────────────────


def _calc_next_retry(retry_count: int) -> str:
    """计算下次重试时间（指数退避 + 上限）"""
    delay = min(
        BACKOFF_BASE_SECONDS * (2 ** retry_count),
        BACKOFF_MAX_SECONDS,
    )
    return (now_cn() + timedelta(seconds=delay)).isoformat()


async def consume_dead_letters(db: Any, is_running_fn) -> None:
    """死信消费者主循环

    维护 org_clients 缓存，跨批次复用企业 client，避免每批重建。

    Args:
        db: 数据库客户端（AsyncLocalDBClient）
        is_running_fn: callable，返回 False 时退出循环
    """
    from services.kuaimai.client import KuaiMaiClient

    # 企业 client 缓存：{org_id: KuaiMaiClient}，跨批次复用
    org_clients: dict[str | None, KuaiMaiClient] = {}
    # 缓存创建时间，30 分钟后过期重建（凭证刷新后生效）
    client_ages: dict[str | None, float] = {}

    logger.info("Dead letter consumer started")

    try:
        while is_running_fn():
            try:
                processed = await _process_batch(db, org_clients, client_ages)
                if processed > 0:
                    logger.info(f"Dead letter processed | count={processed}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Dead letter consumer error | error={e}")

            await asyncio.sleep(CONSUMER_POLL_INTERVAL)
    finally:
        # 统一清理所有缓存的 client
        for c in org_clients.values():
            try:
                await c.close()
            except Exception:
                pass
        org_clients.clear()

    logger.info("Dead letter consumer stopped")


_CLIENT_CACHE_TTL = 1800  # 30 分钟后过期重建（凭证刷新后生效）


async def _process_batch(
    db: Any,
    org_clients: dict[str | None, Any],
    client_ages: dict[str | None, float],
) -> int:
    """处理一批待重试的死信

    Args:
        db: 异步数据库客户端
        org_clients: 企业 client 缓存（跨批次复用，由 consume_dead_letters 管理）
        client_ages: client 创建时间戳（TTL 过期用）
    """
    # 清理过期的缓存 client
    import time as _time
    now_ts = _time.time()
    for org_id in list(client_ages):
        if now_ts - client_ages[org_id] > _CLIENT_CACHE_TTL:
            old_client = org_clients.pop(org_id, None)
            client_ages.pop(org_id, None)
            if old_client:
                try:
                    await old_client.close()
                except Exception:
                    pass

    # 查询到期的 pending 记录
    now = now_cn().isoformat()
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

    # 按 org_id 分组
    org_groups: dict[str | None, list[dict]] = {}
    for row in rows:
        org_id = row.get("org_id")
        org_groups.setdefault(org_id, []).append(row)

    processed = 0
    for org_id, org_rows in org_groups.items():
        # 从缓存获取或创建企业 client
        org_client = await _get_or_create_client(db, org_clients, client_ages, org_id)
        if org_client is None:
            # 凭证不可用，递增 retry_count 防止僵尸记录
            await _mark_batch_retry_failed(db, org_rows, "Client unavailable (credentials missing)")
            continue

        for row in org_rows:
            try:
                await _retry_one(db, org_client, row)
                processed += 1
            except Exception as e:
                logger.error(
                    f"Dead letter retry error | id={row.get('id')} | "
                    f"doc_type={row.get('doc_type')} | org_id={org_id} | error={e}"
                )

    return processed


async def _get_or_create_client(
    db: Any,
    org_clients: dict[str | None, Any],
    client_ages: dict[str | None, float],
    org_id: str | None,
) -> Any | None:
    """从缓存获取或创建企业 KuaiMaiClient"""
    if org_id in org_clients:
        return org_clients[org_id]

    import time as _time
    from services.kuaimai.client import KuaiMaiClient

    if org_id is None:
        # 散客模式
        client = KuaiMaiClient()
        if client.is_configured:
            org_clients[None] = client
            client_ages[None] = _time.time()
            return client
        await client.close()
        return None

    # 企业模式：用 AsyncOrgConfigResolver 加载凭证
    try:
        from services.org.config_resolver import AsyncOrgConfigResolver
        resolver = AsyncOrgConfigResolver(db)
        creds = await resolver.get_erp_credentials(org_id)
        client = KuaiMaiClient(
            app_key=creds["kuaimai_app_key"],
            app_secret=creds["kuaimai_app_secret"],
            access_token=creds["kuaimai_access_token"],
            refresh_token=creds["kuaimai_refresh_token"],
            org_id=org_id,
        )
        org_clients[org_id] = client
        client_ages[org_id] = _time.time()
        return client
    except ValueError as e:
        logger.warning(f"Skip dead letter retry for org {org_id}: {e}")
        return None


async def _mark_batch_retry_failed(
    db: Any, rows: list[dict], error_msg: str,
) -> None:
    """凭证不可用时，递增这批死信的 retry_count，防止僵尸记录。"""
    for row in rows:
        dl_id = row["id"]
        new_count = row["retry_count"] + 1
        max_retries = row["max_retries"]
        try:
            if new_count >= max_retries:
                await db.table("erp_sync_dead_letter").update({
                    "status": "dead",
                    "retry_count": new_count,
                    "last_error": error_msg[:500],
                    "updated_at": now_cn().isoformat(),
                }).eq("id", dl_id).execute()
            else:
                await db.table("erp_sync_dead_letter").update({
                    "retry_count": new_count,
                    "next_retry_at": _calc_next_retry(new_count),
                    "last_error": error_msg[:500],
                    "updated_at": now_cn().isoformat(),
                }).eq("id", dl_id).execute()
        except Exception as e:
            logger.warning(f"Failed to mark dead letter retry | id={dl_id} | error={e}")


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
                "updated_at": now_cn().isoformat(),
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
                "updated_at": now_cn().isoformat(),
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
            from core.org_scoped_db import OrgScopedDB
            from services.kuaimai.erp_sync_service import ErpSyncService
            dl_org_id = row.get("org_id")
            scoped_db = OrgScopedDB(db, dl_org_id)
            svc = ErpSyncService(scoped_db, org_id=dl_org_id)
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
                "updated_at": now_cn().isoformat(),
            }).eq("id", dl_id).execute()
        else:
            await db.table("erp_sync_dead_letter").update({
                "retry_count": new_count,
                "next_retry_at": _calc_next_retry(new_count),
                "last_error": f"upsert failed: {e}"[:500],
                "updated_at": now_cn().isoformat(),
            }).eq("id", dl_id).execute()
        logger.error(
            f"Dead letter upsert failed | doc_type={doc_type} | "
            f"doc_id={doc_id} | error={e}"
        )
