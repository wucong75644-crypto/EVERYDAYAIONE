"""
ERP 仓库单据同步处理器（9种）

allocate / allocate_in / allocate_out / other_in / other_out
inventory_sheet / unshelve / process_order / section_record

每个处理器：list→(detail)→字段映射→upsert→聚合
写入 erp_document_items，复用现有 doc_type 模式。

设计文档: docs/document/TECH_ERP数据本地索引系统.md
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from loguru import logger

from services.kuaimai.erp_sync_utils import (
    _API_SEM,
    _DetailResult,
    _fetch_details,
    _fmt_dt,
    _pick,
    _safe_ts,
)

if TYPE_CHECKING:
    from services.kuaimai.erp_sync_service import ErpSyncService


# ── 调拨单 (allocate) ────────────────────────────────────


async def sync_allocate(
    svc: ErpSyncService, start: datetime, end: datetime,
) -> int:
    """调拨单同步：list + detail（按 code 查详情）"""
    client = svc._get_client()
    docs = await svc.fetch_all_pages(
        "erp.allocate.task.query",
        {"startModified": _fmt_dt(start), "endModified": _fmt_dt(end)},
    )
    if not docs:
        return 0

    # allocate detail 用 code 而非 id
    detail_result = await _fetch_allocate_details(client, docs)
    if detail_result.failed:
        from services.kuaimai.erp_sync_dead_letter import record_dead_letter
        await record_dead_letter(
            svc.db, "allocate", "erp.allocate.task.detail.query",
            detail_result.failed, org_id=svc.org_id,
        )

    all_rows: list[dict[str, Any]] = []
    for doc, detail in detail_result:
        items = detail.get("list") or detail.get("items") or []
        items = svc.sort_and_assign_index(items, "allocate")
        extra = _pick(
            detail, "outWarehouseName", "inWarehouseName",
            "labelName", "totalCount", "totalAmount",
        )
        for item in items:
            all_rows.append({
                "doc_type": "allocate",
                "doc_id": str(doc.get("id") or doc.get("code")),
                "doc_code": doc.get("code"),
                "doc_status": doc.get("status"),
                "doc_created_at": _safe_ts(doc.get("created")),
                "doc_modified_at": _safe_ts(doc.get("modified")),
                "item_index": item["_item_index"],
                "outer_id": item.get("itemOuterId") or item.get("outerId"),
                "sku_outer_id": item.get("outerId") or item.get("skuOuterId"),
                "item_name": item.get("title") or item.get("itemTitle"),
                "quantity": item.get("count") or item.get("quantity"),
                "price": item.get("price"),
                "amount": item.get("amount"),
                "warehouse_name": detail.get("outWarehouseName"),
                "creator_name": detail.get("createrName") or detail.get("creatorName"),
                "remark": doc.get("remark"),
                "extra_json": extra,
            })

    count = await svc.upsert_document_items(all_rows)
    await svc.run_aggregation(svc.collect_affected_keys(all_rows))
    return count


async def _fetch_allocate_details(
    client, docs: list[dict],
) -> _DetailResult:
    """调拨单详情用 code 参数（而非通用的 id）"""
    import asyncio

    async def _one(doc: dict):
        code = doc.get("code")
        if not code:
            return ("fail", doc, "missing code")
        async with _API_SEM:
            try:
                detail = await client.request_with_retry(
                    "erp.allocate.task.detail.query", {"code": code},
                )
                return ("ok", doc, detail)
            except Exception as e:
                logger.warning(
                    f"Allocate detail failed | code={code} | error={e}"
                )
                return ("fail", doc, str(e))

    raw = await asyncio.gather(*[_one(d) for d in docs])
    result = _DetailResult()
    for r in raw:
        if r[0] == "ok":
            result.succeeded.append((r[1], r[2]))
        else:
            result.failed.append(r[1])
    return result


# ── 调拨入库单 (allocate_in) ─────────────────────────────


async def sync_allocate_in(
    svc: ErpSyncService, start: datetime, end: datetime,
) -> int:
    """调拨入库单同步：list + detail"""
    client = svc._get_client()
    docs = await svc.fetch_all_pages(
        "allocate.in.task.query",
        {"startModified": _fmt_dt(start), "endModified": _fmt_dt(end)},
    )
    if not docs:
        return 0

    detail_result = await _fetch_details(client, "allocate.in.task.get", docs)
    if detail_result.failed:
        from services.kuaimai.erp_sync_dead_letter import record_dead_letter
        await record_dead_letter(
            svc.db, "allocate_in", "allocate.in.task.get",
            detail_result.failed, org_id=svc.org_id,
        )

    all_rows = _map_warehouse_doc_items(svc, "allocate_in", detail_result)
    count = await svc.upsert_document_items(all_rows)
    await svc.run_aggregation(svc.collect_affected_keys(all_rows))
    return count


# ── 调拨出库单 (allocate_out) ────────────────────────────


async def sync_allocate_out(
    svc: ErpSyncService, start: datetime, end: datetime,
) -> int:
    """调拨出库单同步：list + detail"""
    client = svc._get_client()
    docs = await svc.fetch_all_pages(
        "allocate.out.task.query",
        {"startModified": _fmt_dt(start), "endModified": _fmt_dt(end)},
    )
    if not docs:
        return 0

    detail_result = await _fetch_details(client, "allocate.out.task.get", docs)
    if detail_result.failed:
        from services.kuaimai.erp_sync_dead_letter import record_dead_letter
        await record_dead_letter(
            svc.db, "allocate_out", "allocate.out.task.get",
            detail_result.failed, org_id=svc.org_id,
        )

    all_rows = _map_warehouse_doc_items(svc, "allocate_out", detail_result)
    count = await svc.upsert_document_items(all_rows)
    await svc.run_aggregation(svc.collect_affected_keys(all_rows))
    return count


# ── 其他入库单 (other_in) ────────────────────────────────


async def sync_other_in(
    svc: ErpSyncService, start: datetime, end: datetime,
) -> int:
    """其他入库单同步：list + detail"""
    client = svc._get_client()
    docs = await svc.fetch_all_pages(
        "other.in.order.query",
        {"startModified": _fmt_dt(start), "endModified": _fmt_dt(end)},
    )
    if not docs:
        return 0

    detail_result = await _fetch_details(client, "other.in.order.get", docs)
    if detail_result.failed:
        from services.kuaimai.erp_sync_dead_letter import record_dead_letter
        await record_dead_letter(
            svc.db, "other_in", "other.in.order.get",
            detail_result.failed, org_id=svc.org_id,
        )

    all_rows = _map_warehouse_doc_items(svc, "other_in", detail_result)
    count = await svc.upsert_document_items(all_rows)
    await svc.run_aggregation(svc.collect_affected_keys(all_rows))
    return count


# ── 其他出库单 (other_out) ───────────────────────────────


async def sync_other_out(
    svc: ErpSyncService, start: datetime, end: datetime,
) -> int:
    """其他出库单同步：list + detail"""
    client = svc._get_client()
    docs = await svc.fetch_all_pages(
        "other.out.order.query",
        {"startModified": _fmt_dt(start), "endModified": _fmt_dt(end)},
    )
    if not docs:
        return 0

    detail_result = await _fetch_details(client, "other.out.order.get", docs)
    if detail_result.failed:
        from services.kuaimai.erp_sync_dead_letter import record_dead_letter
        await record_dead_letter(
            svc.db, "other_out", "other.out.order.get",
            detail_result.failed, org_id=svc.org_id,
        )

    all_rows = _map_warehouse_doc_items(svc, "other_out", detail_result)
    count = await svc.upsert_document_items(all_rows)
    await svc.run_aggregation(svc.collect_affected_keys(all_rows))
    return count


# ── 盘点单 (inventory_sheet) ─────────────────────────────


async def sync_inventory_sheet(
    svc: ErpSyncService, start: datetime, end: datetime,
) -> int:
    """盘点单同步：list + detail（detail 用 code 参数）"""
    client = svc._get_client()
    docs = await svc.fetch_all_pages(
        "inventory.sheet.query",
        {"startModified": _fmt_dt(start), "endModified": _fmt_dt(end)},
    )
    if not docs:
        return 0

    # 盘点单 detail 用 code 而非 id
    detail_result = await _fetch_inventory_details(client, docs)
    if detail_result.failed:
        from services.kuaimai.erp_sync_dead_letter import record_dead_letter
        await record_dead_letter(
            svc.db, "inventory_sheet", "inventory.sheet.get",
            detail_result.failed, org_id=svc.org_id,
        )

    all_rows: list[dict[str, Any]] = []
    for doc, detail in detail_result:
        items = detail.get("list") or detail.get("items") or []
        items = svc.sort_and_assign_index(items, "inventory_sheet")
        extra = _pick(
            detail, "totalProfitCount", "totalLossCount",
            "totalProfitAmount", "totalLossAmount",
        )
        for item in items:
            # 盘点单特有字段：实盘数量 vs 账面数量
            system_qty = item.get("systemCount") or item.get("stockCount")
            actual_qty = item.get("actualCount") or item.get("count")
            diff_qty = None
            if system_qty is not None and actual_qty is not None:
                try:
                    diff_qty = float(actual_qty) - float(system_qty)
                except (TypeError, ValueError):
                    pass
            all_rows.append({
                "doc_type": "inventory_sheet",
                "doc_id": str(doc.get("id") or doc.get("code")),
                "doc_code": doc.get("code"),
                "doc_status": doc.get("status"),
                "doc_created_at": _safe_ts(doc.get("created")),
                "doc_modified_at": _safe_ts(doc.get("modified")),
                "item_index": item["_item_index"],
                "outer_id": item.get("itemOuterId") or item.get("outerId"),
                "sku_outer_id": item.get("outerId") or item.get("skuOuterId"),
                "item_name": item.get("title") or item.get("itemTitle"),
                "quantity": actual_qty,
                "quantity_received": system_qty,
                "warehouse_name": detail.get("warehouseName"),
                "creator_name": detail.get("createrName") or detail.get("creatorName"),
                "remark": doc.get("remark"),
                "extra_json": {
                    **extra,
                    "diff_qty": diff_qty,
                    "system_qty": system_qty,
                },
            })

    count = await svc.upsert_document_items(all_rows)
    await svc.run_aggregation(svc.collect_affected_keys(all_rows))
    return count


async def _fetch_inventory_details(
    client, docs: list[dict],
) -> _DetailResult:
    """盘点单详情用 code 参数"""
    import asyncio

    async def _one(doc: dict):
        code = doc.get("code")
        if not code:
            return ("fail", doc, "missing code")
        async with _API_SEM:
            try:
                detail = await client.request_with_retry(
                    "inventory.sheet.get", {"code": code},
                )
                return ("ok", doc, detail)
            except Exception as e:
                logger.warning(
                    f"Inventory detail failed | code={code} | error={e}"
                )
                return ("fail", doc, str(e))

    raw = await asyncio.gather(*[_one(d) for d in docs])
    result = _DetailResult()
    for r in raw:
        if r[0] == "ok":
            result.succeeded.append((r[1], r[2]))
        else:
            result.failed.append(r[1])
    return result


# ── 下架单 (unshelve) ────────────────────────────────────


async def sync_unshelve(
    svc: ErpSyncService, start: datetime, end: datetime,
) -> int:
    """下架单同步：list + detail"""
    client = svc._get_client()
    docs = await svc.fetch_all_pages(
        "erp.wms.unshelve.order.query",
        {"startModified": _fmt_dt(start), "endModified": _fmt_dt(end)},
    )
    if not docs:
        return 0

    detail_result = await _fetch_details(client, "erp.wms.unshelve.order.get", docs)
    if detail_result.failed:
        from services.kuaimai.erp_sync_dead_letter import record_dead_letter
        await record_dead_letter(
            svc.db, "unshelve", "erp.wms.unshelve.order.get",
            detail_result.failed, org_id=svc.org_id,
        )

    all_rows = _map_warehouse_doc_items(svc, "unshelve", detail_result)
    count = await svc.upsert_document_items(all_rows)
    await svc.run_aggregation(svc.collect_affected_keys(all_rows))
    return count


# ── 加工单 (process_order) ───────────────────────────────


async def sync_process_order(
    svc: ErpSyncService, start: datetime, end: datetime,
) -> int:
    """加工单同步：list + detail（组装/拆卸）"""
    client = svc._get_client()
    docs = await svc.fetch_all_pages(
        "erp.stock.product.order.query",
        {"modifiedStart": _fmt_dt(start), "modifiedEnd": _fmt_dt(end)},
    )
    if not docs:
        return 0

    # process_order detail 用 productOrderId
    detail_result = await _fetch_process_details(client, docs)
    if detail_result.failed:
        from services.kuaimai.erp_sync_dead_letter import record_dead_letter
        await record_dead_letter(
            svc.db, "process_order", "erp.stock.product.order.get",
            detail_result.failed, org_id=svc.org_id,
        )

    all_rows: list[dict[str, Any]] = []
    for doc, detail in detail_result:
        items = detail.get("list") or detail.get("items") or []
        items = svc.sort_and_assign_index(items, "process_order")
        process_type = doc.get("type")  # 1=组装, 2=拆卸
        extra = _pick(
            detail, "productCode", "productTitle",
            "productOuterId", "productCount",
        )
        extra["process_type"] = process_type
        for item in items:
            all_rows.append({
                "doc_type": "process_order",
                "doc_id": str(doc.get("id") or doc.get("productOrderId")),
                "doc_code": doc.get("code"),
                "doc_status": doc.get("status"),
                "doc_created_at": _safe_ts(doc.get("created")),
                "doc_modified_at": _safe_ts(doc.get("modified")),
                "item_index": item["_item_index"],
                "outer_id": item.get("itemOuterId") or item.get("outerId"),
                "sku_outer_id": item.get("outerId") or item.get("skuOuterId"),
                "item_name": item.get("title") or item.get("itemTitle"),
                "quantity": item.get("count") or item.get("quantity"),
                "price": item.get("price"),
                "amount": item.get("amount"),
                "warehouse_name": detail.get("warehouseName"),
                "creator_name": detail.get("createrName") or detail.get("creatorName"),
                "remark": doc.get("remark"),
                "extra_json": extra,
            })

    count = await svc.upsert_document_items(all_rows)
    await svc.run_aggregation(svc.collect_affected_keys(all_rows))
    return count


async def _fetch_process_details(
    client, docs: list[dict],
) -> _DetailResult:
    """加工单详情用 productOrderId 参数"""
    import asyncio

    async def _one(doc: dict):
        pid = doc.get("productOrderId") or doc.get("id")
        if not pid:
            return ("fail", doc, "missing productOrderId")
        async with _API_SEM:
            try:
                detail = await client.request_with_retry(
                    "erp.stock.product.order.get", {"productOrderId": pid},
                )
                return ("ok", doc, detail)
            except Exception as e:
                logger.warning(
                    f"Process order detail failed | id={pid} | error={e}"
                )
                return ("fail", doc, str(e))

    raw = await asyncio.gather(*[_one(d) for d in docs])
    result = _DetailResult()
    for r in raw:
        if r[0] == "ok":
            result.succeeded.append((r[1], r[2]))
        else:
            result.failed.append(r[1])
    return result


# ── 货位进出记录 (section_record) ────────────────────────


async def sync_section_record(
    svc: ErpSyncService, start: datetime, end: datetime,
) -> int:
    """货位进出记录同步：仅 list，无 detail（交易日志）"""
    records = await svc.fetch_all_pages(
        "goods.section.in.out.record.query",
        {"operateStartTime": _fmt_dt(start), "operateEndTime": _fmt_dt(end)},
    )
    if not records:
        return 0

    all_rows: list[dict[str, Any]] = []
    for i, r in enumerate(records):
        rid = r.get("id") or f"sr_{i}"
        all_rows.append({
            "doc_type": "section_record",
            "doc_id": str(rid),
            "doc_code": r.get("orderNumber"),
            "doc_status": r.get("inOutType"),  # in/out
            "doc_created_at": _safe_ts(r.get("operateTime")),
            "doc_modified_at": _safe_ts(r.get("operateTime")),
            "item_index": 0,
            "outer_id": r.get("outerId") or r.get("itemOuterId"),
            "sku_outer_id": r.get("skuOuterId") or r.get("outerId"),
            "item_name": r.get("title") or r.get("itemTitle"),
            "quantity": r.get("count") or r.get("quantity"),
            "warehouse_name": r.get("warehouseName"),
            "creator_name": r.get("operatorName"),
            "extra_json": _pick(
                r, "sectionCode", "sectionName", "batchNo",
                "inOutType", "bizType",
            ),
        })

    count = await svc.upsert_document_items(all_rows)
    await svc.run_aggregation(svc.collect_affected_keys(all_rows))
    return count


# ── 通用映射函数 ─────────────────────────────────────────


def _map_warehouse_doc_items(
    svc: ErpSyncService,
    doc_type: str,
    detail_result: _DetailResult,
) -> list[dict[str, Any]]:
    """标准仓库单据字段映射（allocate_in/out, other_in/out, unshelve 共用）"""
    all_rows: list[dict[str, Any]] = []
    for doc, detail in detail_result:
        items = detail.get("list") or detail.get("items") or []
        items = svc.sort_and_assign_index(items, doc_type)
        extra = _pick(
            detail, "warehouseName", "outWarehouseName",
            "inWarehouseName", "customTypeName", "busyTypeDesc",
        )
        for item in items:
            all_rows.append({
                "doc_type": doc_type,
                "doc_id": str(doc.get("id")),
                "doc_code": doc.get("code"),
                "doc_status": doc.get("status"),
                "doc_created_at": _safe_ts(doc.get("created")),
                "doc_modified_at": _safe_ts(doc.get("modified")),
                "item_index": item["_item_index"],
                "outer_id": item.get("itemOuterId") or item.get("outerId"),
                "sku_outer_id": item.get("outerId") or item.get("skuOuterId"),
                "item_name": item.get("title") or item.get("itemTitle"),
                "quantity": item.get("count") or item.get("quantity"),
                "price": item.get("price"),
                "amount": item.get("amount"),
                "warehouse_name": detail.get("warehouseName"),
                "supplier_name": detail.get("supplierName"),
                "creator_name": detail.get("createrName") or detail.get("creatorName"),
                "remark": doc.get("remark"),
                "extra_json": extra,
            })
    return all_rows
