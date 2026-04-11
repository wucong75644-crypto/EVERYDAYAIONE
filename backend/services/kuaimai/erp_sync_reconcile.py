"""
ERP 分层对账处理器（订单 + 售后）

凌晨定时执行，按2小时分片做三层对账：
第一层：COUNT 比对（API vs DB），一致则跳过
第二层：差异时段拉全量 ID，与 DB diff 找缺失
第三层：只补缺失单据，upsert 写入 + 触发聚合

设计文档: docs/document/TECH_订单凌晨分层对账.md
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from loguru import logger

from services.kuaimai.erp_sync_handlers import (
    _build_aftersale_rows,
    _build_order_rows,
)
from services.kuaimai.erp_sync_utils import _fmt_dt
from utils.time_context import now_cn

if TYPE_CHECKING:
    from services.kuaimai.erp_sync_service import ErpSyncService


# SQL 允许的时间列白名单（防御性校验）
_ALLOWED_TIME_COLS = frozenset({"pay_time", "doc_modified_at", "doc_created_at"})


# ── 通用工具 ────────────────────────────────────────────


def _yesterday_range() -> tuple[datetime, datetime]:
    """返回昨天 00:00 ~ 今天 00:00（北京时间，aware datetime）。

    旧实现用 ``datetime.now()`` 无时区，依赖 OS 时区导致：
    - 容器 TZ=UTC 时凌晨对账查的是 UTC 昨天 = 北京时间 8:00–32:00，丢数据
    - PR3 修复：统一改用 ``utils.time_context.now_cn()``。
    """
    now = now_cn()
    yesterday = (now - timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )
    return yesterday, yesterday + timedelta(days=1)


def _time_slots(
    day_start: datetime, day_end: datetime, hours: int = 2,
) -> list[tuple[datetime, datetime]]:
    """将一天切成 N 个等长时段"""
    slots = []
    cursor = day_start
    while cursor < day_end:
        slot_end = min(cursor + timedelta(hours=hours), day_end)
        slots.append((cursor, slot_end))
        cursor = slot_end
    return slots


async def _db_count_distinct(
    pool, doc_type: str, time_col: str,
    slot_start: datetime, slot_end: datetime, org_id: str | None,
) -> int:
    """查询某时段某类型的去重 doc_id 数量"""
    if time_col not in _ALLOWED_TIME_COLS:
        raise ValueError(f"Invalid time_col: {time_col}")
    org_clause = "org_id = %s" if org_id else "org_id IS NULL"
    params: tuple = (
        (slot_start, slot_end, org_id) if org_id
        else (slot_start, slot_end)
    )
    async with pool.connection() as conn:
        cur = await conn.execute(
            f"SELECT COUNT(DISTINCT doc_id) FROM erp_document_items "
            f"WHERE doc_type = %s AND {time_col} >= %s AND {time_col} < %s AND {org_clause}",
            (doc_type, *params),
        )
        row = await cur.fetchone()
        return list(row.values())[0] if isinstance(row, dict) else row[0]


async def _db_existing_ids(
    pool, doc_type: str, time_col: str,
    slot_start: datetime, slot_end: datetime, org_id: str | None,
) -> set[str]:
    """查询某时段某类型已有的 doc_id 集合"""
    if time_col not in _ALLOWED_TIME_COLS:
        raise ValueError(f"Invalid time_col: {time_col}")
    org_clause = "org_id = %s" if org_id else "org_id IS NULL"
    params: tuple = (
        (slot_start, slot_end, org_id) if org_id
        else (slot_start, slot_end)
    )
    async with pool.connection() as conn:
        cur = await conn.execute(
            f"SELECT DISTINCT doc_id FROM erp_document_items "
            f"WHERE doc_type = %s AND {time_col} >= %s AND {time_col} < %s AND {org_clause}",
            (doc_type, *params),
        )
        rows = await cur.fetchall()
        return {r["doc_id"] if isinstance(r, dict) else r[0] for r in rows}


# ── 订单对账 ────────────────────────────────────────────


async def reconcile_order(svc: ErpSyncService) -> int:
    """订单分层对账：对昨天全天，按2小时分片"""
    yesterday, today = _yesterday_range()
    client = svc._get_client()
    tolerance = svc.settings.erp_reconcile_tolerance
    pool = getattr(svc.db, "_pool", None)
    if not pool:
        logger.error("reconcile_order requires async pool")
        return 0

    # 第一层：COUNT 对账
    mismatch_slots: list[tuple[datetime, datetime]] = []
    for slot_start, slot_end in _time_slots(yesterday, today):
        try:
            resp = await client.request_with_retry(
                "erp.trade.outstock.simple.query",
                {
                    "startTime": _fmt_dt(slot_start),
                    "endTime": _fmt_dt(slot_end),
                    "timeType": "pay_time",
                    "pageNo": 1,
                    "pageSize": 20,
                },
            )
            erp_count = resp.get("total", 0) if resp else 0
        except Exception as e:
            logger.warning(f"reconcile COUNT failed | slot={slot_start.hour}-{slot_end.hour} | error={e}")
            mismatch_slots.append((slot_start, slot_end))
            continue

        db_count = await _db_count_distinct(pool, "order", "pay_time", slot_start, slot_end, svc.org_id)

        if abs(erp_count - db_count) > tolerance:
            mismatch_slots.append((slot_start, slot_end))
            logger.info(
                f"reconcile mismatch | slot={slot_start.hour:02d}-{slot_end.hour:02d} | "
                f"db={db_count} erp={erp_count}"
            )

    if not mismatch_slots:
        logger.info("reconcile_order done | all slots matched")
        return 0

    # 第二层+第三层：差异时段补漏
    total = 0
    affected: set[tuple[str, str]] = set()
    for slot_start, slot_end in mismatch_slots:
        total += await _backfill_order_slot(svc, client, pool, slot_start, slot_end, affected)

    if affected:
        await svc.run_aggregation(list(affected))
    logger.info(f"reconcile_order done | backfilled={total} rows")
    return total


async def _backfill_order_slot(
    svc: ErpSyncService, client, pool,
    slot_start: datetime, slot_end: datetime,
    affected: set[tuple[str, str]],
) -> int:
    """单个时段的订单补漏"""
    db_sids = await _db_existing_ids(pool, "order", "pay_time", slot_start, slot_end, svc.org_id)
    missing_rows: list[dict[str, Any]] = []
    page = 0

    while True:
        page += 1
        try:
            resp = await client.request_with_retry(
                "erp.trade.outstock.simple.query",
                {
                    "startTime": _fmt_dt(slot_start),
                    "endTime": _fmt_dt(slot_end),
                    "timeType": "pay_time",
                    "pageNo": page,
                    "pageSize": 200,
                },
            )
        except Exception as e:
            logger.warning(f"reconcile fetch failed | page={page} | error={e}")
            break

        docs = resp.get("list", []) if resp else []
        if not docs:
            break
        for doc in docs:
            sid = str(doc.get("sid", ""))
            if sid and sid not in db_sids:
                missing_rows.extend(_build_order_rows(doc, svc))
                db_sids.add(sid)
        if len(docs) < 200:
            break

    if not missing_rows:
        return 0

    count = await svc.upsert_document_items(missing_rows)
    affected.update(svc.collect_affected_keys(missing_rows))
    logger.info(
        f"reconcile backfilled | slot={slot_start.hour:02d}-{slot_end.hour:02d} | "
        f"orders={len({r['doc_id'] for r in missing_rows})} rows={count}"
    )
    return count


# ── 售后对账 ────────────────────────────────────────────


async def reconcile_aftersale(svc: ErpSyncService) -> int:
    """售后分层对账：对昨天全天，按2小时分片"""
    yesterday, today = _yesterday_range()
    client = svc._get_client()
    tolerance = svc.settings.erp_reconcile_tolerance
    pool = getattr(svc.db, "_pool", None)
    if not pool:
        logger.error("reconcile_aftersale requires async pool")
        return 0

    # 第一层：COUNT 对账
    mismatch_slots: list[tuple[datetime, datetime]] = []
    for slot_start, slot_end in _time_slots(yesterday, today):
        try:
            resp = await client.request_with_retry(
                "erp.aftersale.list.query",
                {
                    "startModified": _fmt_dt(slot_start),
                    "endModified": _fmt_dt(slot_end),
                    "asVersion": 2,
                    "pageNo": 1,
                    "pageSize": 20,
                },
            )
            erp_count = resp.get("total", 0) if resp else 0
        except Exception as e:
            logger.warning(f"reconcile_as COUNT failed | slot={slot_start.hour}-{slot_end.hour} | error={e}")
            mismatch_slots.append((slot_start, slot_end))
            continue

        db_count = await _db_count_distinct(pool, "aftersale", "doc_modified_at", slot_start, slot_end, svc.org_id)

        if abs(erp_count - db_count) > tolerance:
            mismatch_slots.append((slot_start, slot_end))
            logger.info(
                f"reconcile_as mismatch | slot={slot_start.hour:02d}-{slot_end.hour:02d} | "
                f"db={db_count} erp={erp_count}"
            )

    if not mismatch_slots:
        logger.info("reconcile_aftersale done | all slots matched")
        return 0

    # 第二层+第三层：差异时段补漏
    total = 0
    affected: set[tuple[str, str]] = set()
    for slot_start, slot_end in mismatch_slots:
        total += await _backfill_aftersale_slot(svc, client, pool, slot_start, slot_end, affected)

    if affected:
        await svc.run_aggregation(list(affected))
    logger.info(f"reconcile_aftersale done | backfilled={total} rows")
    return total


async def _backfill_aftersale_slot(
    svc: ErpSyncService, client, pool,
    slot_start: datetime, slot_end: datetime,
    affected: set[tuple[str, str]],
) -> int:
    """单个时段的售后补漏"""
    db_ids = await _db_existing_ids(pool, "aftersale", "doc_modified_at", slot_start, slot_end, svc.org_id)
    missing_rows: list[dict[str, Any]] = []
    page = 0

    while True:
        page += 1
        try:
            resp = await client.request_with_retry(
                "erp.aftersale.list.query",
                {
                    "startModified": _fmt_dt(slot_start),
                    "endModified": _fmt_dt(slot_end),
                    "asVersion": 2,
                    "pageNo": page,
                    "pageSize": 200,
                },
            )
        except Exception as e:
            logger.warning(f"reconcile_as fetch failed | page={page} | error={e}")
            break

        docs = resp.get("list", []) if resp else []
        if not docs:
            break
        for doc in docs:
            doc_id = str(doc.get("id", ""))
            if doc_id and doc_id not in db_ids:
                missing_rows.extend(_build_aftersale_rows(doc, svc))
                db_ids.add(doc_id)
        if len(docs) < 200:
            break

    if not missing_rows:
        return 0

    count = await svc.upsert_document_items(missing_rows)
    affected.update(svc.collect_affected_keys(missing_rows))
    logger.info(
        f"reconcile_as backfilled | slot={slot_start.hour:02d}-{slot_end.hour:02d} | "
        f"aftersales={len({r['doc_id'] for r in missing_rows})} rows={count}"
    )
    return count
