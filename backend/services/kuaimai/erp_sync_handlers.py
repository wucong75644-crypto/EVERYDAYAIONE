"""
ERP 单据同步处理器（6种单据类型）

purchase / receipt / shelf / purchase_return / aftersale / order
每个处理器：list→(detail)→字段映射→upsert→聚合

设计文档: docs/document/TECH_ERP数据本地索引系统.md §7.1
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from services.kuaimai.erp_sync_service import ErpSyncService


# ── 工具函数 ────────────────────────────────────────────


def _fmt_dt(dt: datetime) -> str:
    """yyyy-MM-dd HH:mm:ss（采购/采退时间参数格式）"""
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _fmt_d(dt: datetime) -> str:
    """YYYY-MM-DD（收货/上架/售后/订单时间参数格式）"""
    return dt.strftime("%Y-%m-%d")


def _pick(src: dict, *keys: str) -> dict:
    """从 dict 中提取存在且非 None 的键值对（用于 extra_json）"""
    return {k: src[k] for k in keys if k in src and src[k] is not None}


def _to_float(val: Any) -> float:
    """安全转 float（用于折扣分摊计算）"""
    if val is None:
        return 0.0
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


class _ApiRateLimiter:
    """全局 API 速率限制器（Leaky Bucket）

    Semaphore 只限并发数，无法控制 QPS（云服务器内网延迟 ~30ms 时 Sem(4)=133 req/s）。
    此限制器确保请求启动间隔 ≥ 1/max_qps 秒，多请求可并发执行但启动节奏受控。
    """

    def __init__(self, max_qps: float = 12.0) -> None:
        self._min_interval = 1.0 / max_qps
        self._lock: asyncio.Lock | None = None
        self._lock_loop_id: int | None = None
        self._last_request_time = 0.0

    def _get_lock(self) -> asyncio.Lock:
        """延迟创建 Lock，事件循环变化时自动重建"""
        loop_id = id(asyncio.get_event_loop())
        if self._lock is None or self._lock_loop_id != loop_id:
            self._lock = asyncio.Lock()
            self._lock_loop_id = loop_id
        return self._lock

    async def __aenter__(self):
        async with self._get_lock():
            now = time.monotonic()
            wait = self._min_interval - (now - self._last_request_time)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request_time = time.monotonic()
        return self

    async def __aexit__(self, *args):
        pass


_API_SEM = _ApiRateLimiter(max_qps=12)
"""全局 API 限流器：≤12 req/s（低于 API 15 req/s 限额，留安全余量）。
所有 API 调用（detail / list 翻页）共享此限流器。"""


class _DetailResult:
    """_fetch_details 返回结果，包含成功列表和失败列表"""
    __slots__ = ("succeeded", "failed")

    def __init__(self) -> None:
        self.succeeded: list[tuple[dict, dict]] = []
        self.failed: list[dict] = []

    def __iter__(self):
        """向后兼容：for doc, detail in await _fetch_details(...)"""
        return iter(self.succeeded)


async def _fetch_details(
    client, method: str, docs: list[dict],
) -> _DetailResult:
    """并发获取单据详情（通过全局 _API_SEM 限流）

    Returns:
        _DetailResult: .succeeded 为成功的 (doc, detail) 列表，
                       .failed 为 detail 调用失败的 doc 列表。
    """

    async def _one(doc: dict):
        async with _API_SEM:
            try:
                detail = await client.request_with_retry(method, {"id": doc["id"]})
                return ("ok", doc, detail)
            except Exception as e:
                logger.warning(
                    f"Detail failed | method={method} | id={doc.get('id')} | error={e}"
                )
                return ("fail", doc, str(e))

    raw_results = await asyncio.gather(*[_one(d) for d in docs])

    result = _DetailResult()
    for r in raw_results:
        if r[0] == "ok":
            result.succeeded.append((r[1], r[2]))
        else:
            result.failed.append(r[1])
    return result


def _safe_ts(val: Any) -> str | None:
    """安全转换时间值（毫秒时间戳或字符串）→ ISO 字符串

    快麦API部分字段返回毫秒时间戳（如 1767457525000），
    另一些返回 ISO 字符串（如 '2026-01-03 15:25:25'），
    还有些返回字符串形式的毫秒时间戳（如 "946656000000"）。
    PostgreSQL TIMESTAMP 列无法接受裸毫秒数字。
    """
    if val is None:
        return None
    if isinstance(val, str):
        # 纯数字字符串 → 当作毫秒时间戳处理（如 "946656000000"）
        if val.isdigit() and len(val) >= 10:
            return _safe_ts(int(val))
        return val  # 已经是日期字符串，直接写入
    try:
        ts = int(val)
        # 超过 year-3000 的秒值一定是毫秒时间戳（946656000000 < 1e12 但显然是 ms）
        if ts > 32503680000:  # 3000-01-01 00:00:00 UTC in seconds
            ts = ts / 1000
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return str(val)


# ── 采购单 (purchase) ──────────────────────────────────


async def sync_purchase(
    svc: ErpSyncService, start: datetime, end: datetime,
) -> int:
    """采购单同步：list + detail，items 按 outerId+itemOuterId 排序"""
    client = svc._get_client()
    docs = await svc.fetch_all_pages(
        "purchase.order.query",
        {"startModified": _fmt_dt(start), "endModified": _fmt_dt(end)},
    )
    if not docs:
        return 0

    detail_result = await _fetch_details(client, "purchase.order.get", docs)
    if detail_result.failed:
        from services.kuaimai.erp_sync_dead_letter import record_dead_letter
        record_dead_letter(svc.db, "purchase", "purchase.order.get", detail_result.failed)

    all_rows: list[dict[str, Any]] = []
    for doc, detail in detail_result:
        items = detail.get("list") or []
        items = svc.sort_and_assign_index(items, "purchase")
        extra = _pick(
            detail, "shortId", "totalAmount", "actualTotalAmount",
            "financeStatus", "arrivedQuantity", "receiveQuantity",
            "totalFee", "amendAmount",
        )
        for item in items:
            all_rows.append({
                "doc_type": "purchase",
                "doc_id": str(doc["id"]),
                "doc_code": doc.get("code"),
                "doc_status": doc.get("status"),
                "doc_created_at": _safe_ts(doc.get("created")),
                "doc_modified_at": _safe_ts(doc.get("modified")),
                "item_index": item["_item_index"],
                "outer_id": item.get("itemOuterId"),      # itemOuterId=主编码
                "sku_outer_id": item.get("outerId"),      # outerId=SKU编码
                "item_name": item.get("title"),
                "quantity": item.get("count"),
                "quantity_received": item.get("arrivedQuantity"),
                "price": item.get("price"),
                "amount": item.get("amount") or item.get("totalFee"),
                "supplier_name": item.get("supplierName") or detail.get("supplierName"),
                "warehouse_name": detail.get("warehouseName"),
                "creator_name": detail.get("createrName"),
                "delivery_date": _safe_ts(item.get("deliveryDate") or detail.get("deliveryDate")),
                "remark": doc.get("remark"),
                "extra_json": extra,
            })

    count = svc.upsert_document_items(all_rows)
    svc.run_aggregation(svc.collect_affected_keys(all_rows))
    return count


# ── 收货单 (receipt) ────────────────────────────────────


async def sync_receipt(
    svc: ErpSyncService, start: datetime, end: datetime,
) -> int:
    """收货单同步：list + detail"""
    client = svc._get_client()
    docs = await svc.fetch_all_pages(
        "warehouse.entry.list.query",
        {"startModified": _fmt_dt(start), "endModified": _fmt_dt(end)},
    )
    if not docs:
        return 0

    detail_result = await _fetch_details(client, "warehouse.entry.list.get", docs)
    if detail_result.failed:
        from services.kuaimai.erp_sync_dead_letter import record_dead_letter
        record_dead_letter(svc.db, "receipt", "warehouse.entry.list.get", detail_result.failed)

    all_rows: list[dict[str, Any]] = []
    for doc, detail in detail_result:
        items = detail.get("list") or []
        items = svc.sort_and_assign_index(items, "receipt")
        extra = _pick(
            detail, "shelvedQuantity", "getGoodNum", "getBadNum",
            "totalDetailFee", "busyTypeDesc",
        )
        for item in items:
            all_rows.append({
                "doc_type": "receipt",
                "doc_id": str(doc["id"]),
                "doc_code": doc.get("code"),
                "doc_status": doc.get("status"),
                "doc_created_at": _safe_ts(doc.get("created")),
                "doc_modified_at": _safe_ts(doc.get("modified")),
                "item_index": item["_item_index"],
                "outer_id": item.get("itemOuterId"),      # itemOuterId=主编码
                "sku_outer_id": item.get("outerId"),      # outerId=SKU编码
                "item_name": item.get("title"),
                "quantity": item.get("count"),
                "price": item.get("price"),
                "amount": item.get("amount"),
                "supplier_name": detail.get("supplierName"),
                "warehouse_name": detail.get("warehouseName"),
                "creator_name": detail.get("createrName"),
                "purchase_order_code": detail.get("purchaseOrderCode"),
                "extra_json": extra,
            })

    count = svc.upsert_document_items(all_rows)
    svc.run_aggregation(svc.collect_affected_keys(all_rows))
    return count


# ── 上架单 (shelf) ──────────────────────────────────────


async def sync_shelf(
    svc: ErpSyncService, start: datetime, end: datetime,
) -> int:
    """上架单同步：list + detail"""
    client = svc._get_client()
    docs = await svc.fetch_all_pages(
        "erp.purchase.shelf.query",
        {"startModified": _fmt_dt(start), "endModified": _fmt_dt(end)},
    )
    if not docs:
        return 0

    detail_result = await _fetch_details(client, "erp.purchase.shelf.get", docs)
    if detail_result.failed:
        from services.kuaimai.erp_sync_dead_letter import record_dead_letter
        record_dead_letter(svc.db, "shelf", "erp.purchase.shelf.get", detail_result.failed)

    all_rows: list[dict[str, Any]] = []
    for doc, detail in detail_result:
        items = detail.get("list") or []
        items = svc.sort_and_assign_index(items, "shelf")
        for item in items:
            all_rows.append({
                "doc_type": "shelf",
                "doc_id": str(doc["id"]),
                "doc_code": doc.get("code"),
                "doc_status": doc.get("status"),
                "doc_created_at": _safe_ts(doc.get("created")),
                "doc_modified_at": _safe_ts(doc.get("modified")),
                "item_index": item["_item_index"],
                "outer_id": item.get("itemOuterId"),      # itemOuterId=主编码
                "sku_outer_id": item.get("outerId"),      # outerId=SKU编码
                "item_name": item.get("title"),
                "quantity": item.get("count"),
                "warehouse_name": detail.get("warehouseName"),
            })

    count = svc.upsert_document_items(all_rows)
    svc.run_aggregation(svc.collect_affected_keys(all_rows))
    return count


# ── 采退单 (purchase_return) ────────────────────────────


async def sync_purchase_return(
    svc: ErpSyncService, start: datetime, end: datetime,
) -> int:
    """采购退货单同步：list + detail（注意 gmCreate / 编码映射反转）"""
    client = svc._get_client()
    docs = await svc.fetch_all_pages(
        "purchase.return.list.query",
        {"startModified": _fmt_dt(start), "endModified": _fmt_dt(end)},
    )
    if not docs:
        return 0

    detail_result = await _fetch_details(client, "purchase.return.list.get", docs)
    if detail_result.failed:
        from services.kuaimai.erp_sync_dead_letter import record_dead_letter
        record_dead_letter(svc.db, "purchase_return", "purchase.return.list.get", detail_result.failed)

    all_rows: list[dict[str, Any]] = []
    for doc, detail in detail_result:
        items = detail.get("list") or []
        items = svc.sort_and_assign_index(items, "purchase_return")
        extra = _pick(
            detail, "shortId", "totalAmount", "financeStatus",
            "statusName", "tagName",
        )
        # 采退单 purchaseOrderId 是数字 ID，转为字符串存储
        po_id = detail.get("purchaseOrderId")
        po_code = str(po_id) if po_id is not None else None
        for item in items:
            all_rows.append({
                "doc_type": "purchase_return",
                "doc_id": str(doc["id"]),
                "doc_code": doc.get("code"),
                "doc_status": str(doc.get("status", "")),
                "doc_created_at": _safe_ts(doc.get("gmCreate")),  # 设计文档：字段名为 gmCreate
                "doc_modified_at": _safe_ts(doc.get("modified") or doc.get("gmModified")),
                "item_index": item["_item_index"],
                "outer_id": item.get("itemOuterId"),     # 设计文档：itemOuterId→outer_id
                "sku_outer_id": item.get("outerId"),     # 设计文档：outerId→sku_outer_id
                "item_name": item.get("title"),
                "quantity": item.get("returnNum"),
                "actual_return_qty": item.get("actualReturnNum"),
                "price": item.get("price"),
                "amount": item.get("amount"),
                "supplier_name": detail.get("supplierName"),
                "warehouse_name": detail.get("warehouseName"),
                "creator_name": detail.get("createrName"),
                "purchase_order_code": po_code,
                "extra_json": extra,
            })

    count = svc.upsert_document_items(all_rows)
    svc.run_aggregation(svc.collect_affected_keys(all_rows))
    return count


# ── 售后工单 (aftersale) ────────────────────────────────


async def sync_aftersale(
    svc: ErpSyncService, start: datetime, end: datetime,
) -> int:
    """售后工单同步：流式拉取 + 每1000条刷库，避免内存堆积"""
    flush_size = svc.FLUSH_THRESHOLD
    all_rows: list[dict[str, Any]] = []
    affected_key_set: set[tuple[str, str]] = set()
    total_count = 0

    async for page_docs in svc.fetch_pages_streaming(
        "erp.aftersale.list.query",
        {
            "startModified": _fmt_dt(start),
            "endModified": _fmt_dt(end),
            "asVersion": 2,
        },
        page_size=200,
    ):
        for doc in page_docs:
            doc_extra = _pick(
                doc, "goodStatus", "refundWarehouseName",
                "refundExpressCompany", "refundExpressId",
                "reissueSid", "platformId", "shortId",
            )
            doc_base = {
                "doc_type": "aftersale",
                "doc_id": str(doc["id"]),
                "doc_status": doc.get("status"),
                "doc_created_at": _safe_ts(doc.get("created")),
                "doc_modified_at": _safe_ts(doc.get("modified")),
                "shop_name": doc.get("shopName"),
                "platform": doc.get("source"),
                "order_no": doc.get("tid"),
                "aftersale_type": doc.get("afterSaleType"),
                "refund_money": doc.get("refundMoney"),
                "raw_refund_money": doc.get("rawRefundMoney"),
                "text_reason": doc.get("textReason"),
                "finished_at": _safe_ts(doc.get("finished")),
                "remark": doc.get("remark"),
            }

            items = doc.get("items") or []
            if not items:
                all_rows.append({**doc_base, "item_index": 0, "extra_json": doc_extra})
                continue

            items = svc.sort_and_assign_index(items, "aftersale")
            for item in items:
                item_extra = _pick(item, "goodItemCount", "badItemCount", "type")
                merged_extra = {**doc_extra, **item_extra} if item_extra else doc_extra
                all_rows.append({
                    **doc_base,
                    "item_index": item["_item_index"],
                    "outer_id": item.get("mainOuterId"),
                    "sku_outer_id": item.get("outerId"),
                    "item_name": item.get("title"),
                    "quantity": item.get("receivableCount"),
                    "real_qty": item.get("itemRealQty"),
                    "price": item.get("price"),
                    "amount": item.get("payment"),
                    "extra_json": merged_extra,
                })

        # 每 1000 条刷一次库，释放内存
        if len(all_rows) >= flush_size:
            total_count += svc.upsert_document_items(all_rows)
            affected_key_set.update(svc.collect_affected_keys(all_rows))
            all_rows.clear()

    # 剩余数据刷库
    if all_rows:
        total_count += svc.upsert_document_items(all_rows)
        affected_key_set.update(svc.collect_affected_keys(all_rows))

    svc.run_aggregation(list(affected_key_set))
    return total_count


# ── 销售订单 (order) ────────────────────────────────────


def _build_order_rows(
    doc: dict, svc: ErpSyncService,
) -> list[dict[str, Any]]:
    """从单个订单 doc 构建 DB 行（供 sync_order 和死信重试共用）"""
    items = doc.get("orders") or []
    if not items:
        return []
    items = svc.sort_and_assign_index(items, "order")

    total_discount = _to_float(doc.get("discountFee"))
    total_payment = sum(_to_float(i.get("payment")) for i in items) or 1
    doc_extra = _pick(
        doc, "type", "payAmount",
        "isCancel", "isRefund", "isExcep", "isHalt", "isUrgent",
    )

    rows: list[dict[str, Any]] = []
    discount_used = 0.0
    for pos, item in enumerate(items):
        payment = _to_float(item.get("payment"))
        is_last = (pos == len(items) - 1)
        if not is_last:
            item_discount = round(total_discount * payment / total_payment, 2)
            discount_used += item_discount
        else:
            item_discount = round(total_discount - discount_used, 2)

        rows.append({
            "doc_type": "order",
            "doc_id": str(doc.get("sid", "")),
            "doc_status": doc.get("sysStatus"),
            "doc_created_at": _safe_ts(doc.get("created")),
            "doc_modified_at": _safe_ts(doc.get("modified")),
            "item_index": item["_item_index"],
            "outer_id": item.get("sysItemOuterId"),   # 主编码
            "sku_outer_id": item.get("sysOuterId"),    # SKU编码
            "item_name": item.get("title"),
            "quantity": item.get("num"),
            "price": item.get("price"),
            "amount": item.get("payment"),
            "cost": item.get("cost"),
            "refund_status": item.get("refundStatus"),
            "discount_fee": item_discount if total_discount else None,
            "post_fee": doc.get("postFee") if pos == 0 else None,
            "gross_profit": doc.get("grossProfit") if pos == 0 else None,
            "order_no": doc.get("tid"),
            "order_status": doc.get("sysStatus"),
            "express_no": doc.get("outSid"),
            "express_company": doc.get("expressCompanyName"),
            "shop_name": doc.get("shopName"),
            "platform": doc.get("source"),
            "warehouse_name": doc.get("warehouseName"),
            "pay_time": _safe_ts(doc.get("payTime")),
            "consign_time": _safe_ts(doc.get("consignTime")),
            "remark": doc.get("sellerMemo"),
            "sys_memo": doc.get("sysMemo"),
            "buyer_message": doc.get("buyerMessage"),
            "extra_json": {**doc_extra, "payment": item.get("payment")},
        })
    return rows


# 订单同步的时间维度：upd_time 拉变更，pay_time 补漏
# （部分订单 modified=None，upd_time 查不到，pay_time 兜底）
_ORDER_TIME_TYPES = ["upd_time", "pay_time"]


async def sync_order(
    svc: ErpSyncService, start: datetime, end: datetime,
) -> int:
    """订单同步：双维度拉取（upd_time + pay_time），upsert 自动去重"""
    flush_size = svc.FLUSH_THRESHOLD
    all_rows: list[dict[str, Any]] = []
    affected_key_set: set[tuple[str, str]] = set()
    total_count = 0
    seen_sids: set[str] = set()  # 跨维度去重

    for time_type in _ORDER_TIME_TYPES:
        async for page_docs in svc.fetch_pages_streaming(
            "erp.trade.list.query",
            {
                "startTime": _fmt_dt(start),
                "endTime": _fmt_dt(end),
                "timeType": time_type,
            },
            page_size=200,
        ):
            for doc in page_docs:
                sid = str(doc.get("sid", ""))
                if sid in seen_sids:
                    continue  # 跨维度去重，同一订单只处理一次
                seen_sids.add(sid)

                rows = _build_order_rows(doc, svc)
                all_rows.extend(rows)

            # 每 1000 条刷一次库，释放内存
            if len(all_rows) >= flush_size:
                total_count += svc.upsert_document_items(all_rows)
                affected_key_set.update(svc.collect_affected_keys(all_rows))
                all_rows.clear()

    # 剩余数据刷库
    if all_rows:
        total_count += svc.upsert_document_items(all_rows)
        affected_key_set.update(svc.collect_affected_keys(all_rows))

    svc.run_aggregation(list(affected_key_set))
    return total_count
