"""ERP 死信队列 — platform_map 批次重试（Bug 2）

与 detail API 重试的语义不同：
- 输入：{"sku_ids": [...]} 而非 {"id": doc_id}
- 调 erp.item.outerid.list.get（同主流程的 API）
- 成功后 upsert erp_product_platform_map + 标记 SKU 的 checked_at
- 失败递增 retry_count + 指数退避，超 max_retries 标记 dead
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from utils.time_context import now_cn

from services.kuaimai.erp_sync_dead_letter.queue import _calc_next_retry


async def _bump_platform_map_retry(
    db: Any, dl_id: int, doc_id: str,
    retry_count: int, max_retries: int,
    error_msg: str, sku_count: int,
) -> None:
    """递增 platform_map_batch 死信的 retry_count 或标记 dead。"""
    new_count = retry_count + 1
    if new_count >= max_retries:
        await db.table("erp_sync_dead_letter").update({
            "status": "dead",
            "retry_count": new_count,
            "last_error": error_msg[:500],
            "updated_at": now_cn().isoformat(),
        }).eq("id", dl_id).execute()
        logger.error(
            f"Dead letter platform_map_batch exhausted | "
            f"doc_id={doc_id} | retries={new_count} | sku_count={sku_count}"
        )
    else:
        await db.table("erp_sync_dead_letter").update({
            "retry_count": new_count,
            "next_retry_at": _calc_next_retry(new_count),
            "last_error": error_msg[:500],
            "updated_at": now_cn().isoformat(),
        }).eq("id", dl_id).execute()
        logger.warning(
            f"Dead letter platform_map_batch retry failed | "
            f"doc_id={doc_id} | attempt={new_count}/{max_retries} | "
            f"error={error_msg[:200]}"
        )


async def _apply_platform_map_success(
    db: Any, dl_id: int, doc_id: str,
    org_id: str | None, sku_ids: list[str], items: list[dict],
) -> None:
    """处理 platform_map_batch 重试成功：upsert + 标记 checked_at + 删除死信。

    任何步骤失败都会向上抛，由调用方走 _bump_platform_map_retry。
    """
    from core.org_scoped_db import OrgScopedDB
    from services.kuaimai.erp_sync_master_handlers import (
        _dedupe_platform_rows,
        _parse_platform_map_items,
    )
    from services.kuaimai.erp_sync_utils import _batch_upsert

    rows: list[dict[str, Any]] = []
    _parse_platform_map_items(items, rows)
    unique_rows = _dedupe_platform_rows(rows)

    scoped_db = OrgScopedDB(db, org_id)
    upserted = 0
    if unique_rows:
        upserted = await _batch_upsert(
            scoped_db, "erp_product_platform_map", unique_rows,
            "outer_id,num_iid,org_id", org_id=org_id,
        )

    # 标记本批所有 SKU 的 checked_at（含 API 没返回的 = 确认无映射）
    try:
        now_iso = now_cn().isoformat()
        await (
            scoped_db.table("erp_product_skus")
            .update({"platform_map_checked_at": now_iso})
            .in_("sku_outer_id", sku_ids)
            .execute()
        )
    except Exception as e:
        logger.warning(
            f"Dead letter platform_map_batch mark checked_at failed | "
            f"doc_id={doc_id} | error={e}"
        )

    # 删除死信记录
    await db.table("erp_sync_dead_letter").delete().eq("id", dl_id).execute()
    logger.info(
        f"Dead letter platform_map_batch recovered | "
        f"doc_id={doc_id} | sku_count={len(sku_ids)} | upserted={upserted}"
    )


async def _retry_platform_map_batch(
    db: Any, client: Any, row: dict, doc: dict,
) -> None:
    """重试一个 platform_map 失败批次。

    与 detail API 重试不同：
    - 不调 detail API，调 erp.item.outerid.list.get（同主流程的 API）
    - 成功后 upsert erp_product_platform_map 并标记 SKU 的 checked_at
    - 失败递增 retry_count + 指数退避，超 max_retries 标记 dead

    Args:
        row: erp_sync_dead_letter 行
        doc: 解析后的 doc_json，结构 {"id": <doc_id>, "sku_ids": [...]}
    """
    dl_id = row["id"]
    doc_id = row["doc_id"]
    retry_count = row["retry_count"]
    max_retries = row["max_retries"]
    org_id = row.get("org_id")
    sku_ids = doc.get("sku_ids") or []

    if not sku_ids:
        # 没 SKU 列表 = 死信本身有问题，直接标记 dead
        await db.table("erp_sync_dead_letter").update({
            "status": "dead",
            "last_error": "platform_map_batch missing sku_ids",
            "updated_at": now_cn().isoformat(),
        }).eq("id", dl_id).execute()
        logger.error(
            f"Dead letter platform_map_batch invalid | doc_id={doc_id}"
        )
        return

    # 调 API
    try:
        data = await client.request_with_retry(
            "erp.item.outerid.list.get", {"outerIds": ",".join(sku_ids)},
        )
        items = data.get("itemOuterIdInfos") or []
    except Exception as e:
        await _bump_platform_map_retry(
            db, dl_id, doc_id, retry_count, max_retries,
            str(e), len(sku_ids),
        )
        return

    # 成功 → 解析、upsert、标记 checked_at、删除死信
    try:
        await _apply_platform_map_success(
            db, dl_id, doc_id, org_id, sku_ids, items,
        )
    except Exception as e:
        await _bump_platform_map_retry(
            db, dl_id, doc_id, retry_count, max_retries,
            f"upsert failed: {e}", len(sku_ids),
        )
