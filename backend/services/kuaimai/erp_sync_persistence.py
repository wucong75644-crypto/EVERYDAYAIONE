"""
ERP 同步数据持久化

从 ErpSyncService 拆出的数据写入、排序、聚合逻辑。
ErpSyncService 通过薄委托方法调用此模块。
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from datetime import datetime
from typing import Any

from loguru import logger


# ── item_index 排序键 ────────────────────────────────

ITEM_SORT_KEYS: dict[str, list[str]] = {
    "order": ["sysOuterId", "sysItemOuterId"],
    "aftersale": ["mainOuterId", "outerId"],
    "purchase": ["outerId", "itemOuterId"],
    "purchase_return": ["outerId", "itemOuterId"],
    "receipt": ["outerId", "itemOuterId"],
    "shelf": ["outerId", "itemOuterId"],
}


def sort_and_assign_index(
    items: list[dict[str, Any]], sync_type: str,
) -> list[dict[str, Any]]:
    """
    按确定性字段排序后分配顺序 item_index（0, 1, 2...）

    配合 upsert_document_items 的事务性删+插策略，
    确保单据更新时旧数据被完整替换，不会因 index 变化而错位。
    """
    sort_keys = ITEM_SORT_KEYS.get(sync_type, ["outerId", "itemOuterId"])

    def sort_key(item: dict) -> tuple:
        return tuple(str(item.get(k, "")) for k in sort_keys)

    sorted_items = sorted(items, key=sort_key)
    for idx, item in enumerate(sorted_items):
        item["_item_index"] = idx
    return sorted_items


# ── 事务性写入 ────────────────────────────────────────


def _write_doc_group_txn(
    conn, doc_type: str, doc_id: str, doc_rows: list[dict],
) -> None:
    """在已有事务连接上执行单个单据组的删+插（消除深层嵌套）"""
    conn.execute(
        "DELETE FROM erp_document_items "
        "WHERE doc_type = %s AND doc_id = %s",
        (doc_type, doc_id),
    )
    for row in doc_rows:
        cols = list(row.keys())
        vals = []
        for c in cols:
            v = row[c]
            if isinstance(v, (dict, list)):
                v = json.dumps(v, ensure_ascii=False)
            vals.append(v)
        placeholders = ", ".join(["%s"] * len(cols))
        col_names = ", ".join(cols)
        conn.execute(
            f"INSERT INTO erp_document_items "
            f"({col_names}) VALUES ({placeholders})",
            vals,
        )


def upsert_document_items(db, rows: list[dict[str, Any]]) -> int:
    """
    事务性写入 erp_document_items（按单据分组：删旧→插新）

    对每个 (doc_type, doc_id) 在同一事务内先删除旧行再插入新行，
    保证原子性：要么全部成功，要么全部回滚，不会丢数据。
    不同单据之间互不影响，单个单据失败不阻塞其他单据。

    Returns:
        成功写入的行数
    """
    if not rows:
        return 0

    # 按 (doc_type, doc_id) 分组
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        key = (row.get("doc_type", ""), row.get("doc_id", ""))
        groups[key].append(row)

    total = 0
    pool = getattr(db, "_pool", None)

    for (doc_type, doc_id), doc_rows in groups.items():
        try:
            if pool:
                with pool.connection() as conn:
                    with conn.transaction():
                        _write_doc_group_txn(conn, doc_type, doc_id, doc_rows)
                total += len(doc_rows)
            else:
                # 降级：ORM upsert（无事务保证）
                db.table("erp_document_items").upsert(
                    doc_rows,
                    on_conflict="doc_type,doc_id,item_index",
                ).execute()
                total += len(doc_rows)
        except Exception as e:
            logger.error(
                f"Doc write failed | doc_type={doc_type} | "
                f"doc_id={doc_id} | rows={len(doc_rows)} | error={e}"
            )
    return total


# ── 聚合计算 ──────────────────────────────────────────


def run_aggregation(
    db,
    aggregation_queue: asyncio.Queue | None,
    affected_keys: list[tuple[str, str]],
) -> None:
    """
    将受影响的 (outer_id, stat_date) 推入内存队列，
    由独立消费者串行聚合，避免并发写入时阻塞拉取或打满 DB。

    无内存队列时降级为同步逐条聚合。
    """
    if not affected_keys:
        return

    if aggregation_queue is not None:
        for key in affected_keys:
            try:
                aggregation_queue.put_nowait(key)
            except asyncio.QueueFull:
                logger.warning("Aggregation queue full, dropping oldest")
                try:
                    aggregation_queue.get_nowait()
                    aggregation_queue.put_nowait(key)
                except Exception:
                    pass
    else:
        _run_aggregation_sync(db, affected_keys)


def _run_aggregation_sync(
    db, affected_keys: list[tuple[str, str]],
) -> None:
    """降级：同步逐条聚合"""
    for outer_id, stat_date in affected_keys:
        try:
            db.rpc(
                "erp_aggregate_daily_stats",
                {"p_outer_id": outer_id, "p_stat_date": stat_date},
            ).execute()
        except Exception as e:
            logger.error(
                f"Aggregation failed | outer_id={outer_id} | "
                f"date={stat_date} | error={e}"
            )


def collect_affected_keys(
    rows: list[dict[str, Any]],
) -> list[tuple[str, str]]:
    """从入库行中收集受影响的 (outer_id, stat_date) 对"""
    seen: set[tuple[str, str]] = set()
    for row in rows:
        outer_id = row.get("outer_id")
        created_at = row.get("doc_created_at")
        if outer_id and created_at:
            if isinstance(created_at, str):
                stat_date = created_at[:10]
            elif isinstance(created_at, datetime):
                stat_date = created_at.strftime("%Y-%m-%d")
            else:
                continue
            seen.add((outer_id, stat_date))
    return list(seen)
