"""
ERP 单据同步处理器单元测试

覆盖：erp_sync_handlers（6种单据 + 4个工具函数）

设计文档: docs/document/TECH_ERP数据本地索引系统.md §7.1
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from services.kuaimai.erp_sync_service import ErpSyncService


# ── 工厂函数 ─────────────────────────────────────────


def _mock_svc(pages=None, detail=None):
    """创建 mock ErpSyncService 实例"""
    svc = MagicMock()
    svc.fetch_all_pages = AsyncMock(return_value=pages or [])
    client = MagicMock()
    client.request_with_retry = AsyncMock(return_value=detail or {})
    svc._get_client.return_value = client
    svc.sort_and_assign_index = ErpSyncService.sort_and_assign_index
    svc.upsert_document_items = MagicMock(side_effect=lambda rows: len(rows))
    svc.collect_affected_keys = MagicMock(return_value=[])
    svc.run_aggregation = MagicMock()
    return svc


START = datetime(2026, 3, 17, tzinfo=timezone.utc)
END = datetime(2026, 3, 18, tzinfo=timezone.utc)


# ============================================================
# TestHelperFunctions — 工具函数
# ============================================================


class TestFmtDt:
    def test_format_datetime(self):
        from services.kuaimai.erp_sync_handlers import _fmt_dt
        dt = datetime(2026, 3, 18, 15, 30, 45)
        assert _fmt_dt(dt) == "2026-03-18 15:30:45"


class TestFmtD:
    def test_format_date(self):
        from services.kuaimai.erp_sync_handlers import _fmt_d
        dt = datetime(2026, 3, 18)
        assert _fmt_d(dt) == "2026-03-18"


class TestPick:
    def test_picks_existing_non_none(self):
        from services.kuaimai.erp_sync_handlers import _pick
        src = {"a": 1, "b": 2, "c": None, "d": 4}
        assert _pick(src, "a", "b", "c", "e") == {"a": 1, "b": 2}

    def test_empty_source(self):
        from services.kuaimai.erp_sync_handlers import _pick
        assert _pick({}, "a", "b") == {}


class TestToFloat:
    def test_normal_float(self):
        from services.kuaimai.erp_sync_handlers import _to_float
        assert _to_float(3.14) == 3.14

    def test_string_number(self):
        from services.kuaimai.erp_sync_handlers import _to_float
        assert _to_float("42.5") == 42.5

    def test_none_returns_zero(self):
        from services.kuaimai.erp_sync_handlers import _to_float
        assert _to_float(None) == 0.0

    def test_invalid_string_returns_zero(self):
        from services.kuaimai.erp_sync_handlers import _to_float
        assert _to_float("abc") == 0.0

    def test_integer(self):
        from services.kuaimai.erp_sync_handlers import _to_float
        assert _to_float(10) == 10.0


# ============================================================
# TestSyncPurchase — 采购单同步
# ============================================================


class TestSyncPurchase:
    @pytest.mark.asyncio
    async def test_empty_pages_returns_zero(self):
        from services.kuaimai.erp_sync_handlers import sync_purchase
        assert await sync_purchase(_mock_svc(), START, END) == 0

    @pytest.mark.asyncio
    async def test_basic_purchase(self):
        from services.kuaimai.erp_sync_handlers import sync_purchase
        docs = [{"id": 101, "code": "PO001", "status": "FINISHED",
                 "created": "2026-03-18", "modified": "2026-03-18"}]
        detail = {
            "items": [
                {"outerId": "C01", "itemOuterId": "C01-01",
                 "title": "商品A", "purchaseNum": 100,
                 "price": 10.0, "amount": 1000.0},
            ],
            "supplierName": "供应商A",
            "warehouseName": "仓库1",
            "createrName": "张三",
        }
        svc = _mock_svc(pages=docs, detail=detail)
        count = await sync_purchase(svc, START, END)
        assert count == 1
        rows = svc.upsert_document_items.call_args[0][0]
        assert rows[0]["doc_type"] == "purchase"
        assert rows[0]["outer_id"] == "C01"
        assert rows[0]["supplier_name"] == "供应商A"

    @pytest.mark.asyncio
    async def test_detail_failure_skips_doc(self):
        """detail 请求失败跳过该单据"""
        from services.kuaimai.erp_sync_handlers import sync_purchase
        svc = _mock_svc(pages=[{"id": 101}, {"id": 102}])
        svc._get_client().request_with_retry = AsyncMock(
            side_effect=[Exception("timeout"), {"items": []}],
        )
        count = await sync_purchase(svc, START, END)
        assert count == 0


# ============================================================
# TestSyncReceipt — 收货单同步
# ============================================================


class TestSyncReceipt:
    @pytest.mark.asyncio
    async def test_empty_returns_zero(self):
        from services.kuaimai.erp_sync_handlers import sync_receipt
        assert await sync_receipt(_mock_svc(), START, END) == 0

    @pytest.mark.asyncio
    async def test_basic_receipt(self):
        from services.kuaimai.erp_sync_handlers import sync_receipt
        docs = [{"id": 201, "code": "RC001", "status": "FINISHED",
                 "created": "2026-03-18", "modified": "2026-03-18"}]
        detail = {
            "items": [
                {"outerId": "C01", "itemOuterId": "C01-01",
                 "title": "商品A", "quantity": 50, "price": 10.0,
                 "amount": 500.0},
            ],
            "supplierName": "供应商A",
            "purchaseOrderCode": "PO001",
        }
        svc = _mock_svc(pages=docs, detail=detail)
        count = await sync_receipt(svc, START, END)
        assert count == 1
        rows = svc.upsert_document_items.call_args[0][0]
        assert rows[0]["doc_type"] == "receipt"
        assert rows[0]["purchase_order_code"] == "PO001"


# ============================================================
# TestSyncShelf — 上架单同步
# ============================================================


class TestSyncShelf:
    @pytest.mark.asyncio
    async def test_empty_returns_zero(self):
        from services.kuaimai.erp_sync_handlers import sync_shelf
        assert await sync_shelf(_mock_svc(), START, END) == 0

    @pytest.mark.asyncio
    async def test_basic_shelf(self):
        from services.kuaimai.erp_sync_handlers import sync_shelf
        docs = [{"id": 301, "code": "SH001", "status": "FINISHED",
                 "created": "2026-03-18", "modified": "2026-03-18"}]
        detail = {
            "items": [{"outerId": "C01", "title": "商品A", "quantity": 50}],
            "warehouseName": "仓库1",
        }
        svc = _mock_svc(pages=docs, detail=detail)
        count = await sync_shelf(svc, START, END)
        assert count == 1
        rows = svc.upsert_document_items.call_args[0][0]
        assert rows[0]["doc_type"] == "shelf"
        assert rows[0]["warehouse_name"] == "仓库1"


# ============================================================
# TestSyncPurchaseReturn — 采退单同步
# ============================================================


class TestSyncPurchaseReturn:
    @pytest.mark.asyncio
    async def test_empty_returns_zero(self):
        from services.kuaimai.erp_sync_handlers import sync_purchase_return
        assert await sync_purchase_return(_mock_svc(), START, END) == 0

    @pytest.mark.asyncio
    async def test_field_mapping_reversed(self):
        """采退单字段映射反转：itemOuterId→outer_id, outerId→sku_outer_id"""
        from services.kuaimai.erp_sync_handlers import sync_purchase_return
        docs = [{"id": 401, "code": "RT001", "status": "1",
                 "gmCreate": "2026-03-18"}]
        detail = {
            "items": [
                {"outerId": "SKU01", "itemOuterId": "MAIN01",
                 "title": "商品A", "returnNum": 10, "price": 10.0},
            ],
            "purchaseOrderId": 12345,
            "supplierName": "供应商A",
        }
        svc = _mock_svc(pages=docs, detail=detail)
        count = await sync_purchase_return(svc, START, END)
        assert count == 1
        row = svc.upsert_document_items.call_args[0][0][0]
        assert row["outer_id"] == "MAIN01"
        assert row["sku_outer_id"] == "SKU01"
        assert row["purchase_order_code"] == "12345"
        assert row["doc_created_at"] == "2026-03-18"


# ============================================================
# TestSyncAftersale — 售后工单同步
# ============================================================


class TestSyncAftersale:
    @pytest.mark.asyncio
    async def test_empty_returns_zero(self):
        from services.kuaimai.erp_sync_handlers import sync_aftersale
        assert await sync_aftersale(_mock_svc(), START, END) == 0

    @pytest.mark.asyncio
    async def test_with_items(self):
        from services.kuaimai.erp_sync_handlers import sync_aftersale
        docs = [{
            "id": 501, "status": "FINISHED", "created": "2026-03-18",
            "shopName": "旗舰店", "source": "tb", "tid": "T123",
            "afterSaleType": 2, "refundMoney": 50.0,
            "items": [
                {"mainOuterId": "MAIN01", "outerId": "SKU01",
                 "title": "商品A", "receivableCount": 1,
                 "price": 50.0, "payment": 50.0},
            ],
        }]
        svc = _mock_svc(pages=docs)
        count = await sync_aftersale(svc, START, END)
        assert count == 1
        row = svc.upsert_document_items.call_args[0][0][0]
        assert row["aftersale_type"] == 2
        assert row["shop_name"] == "旗舰店"
        assert row["outer_id"] == "MAIN01"

    @pytest.mark.asyncio
    async def test_empty_items_inserts_one_row(self):
        """仅退款（无items）仍插一行"""
        from services.kuaimai.erp_sync_handlers import sync_aftersale
        docs = [{
            "id": 502, "status": "FINISHED", "created": "2026-03-18",
            "afterSaleType": 1, "refundMoney": 100.0, "items": [],
        }]
        svc = _mock_svc(pages=docs)
        count = await sync_aftersale(svc, START, END)
        assert count == 1
        row = svc.upsert_document_items.call_args[0][0][0]
        assert row["item_index"] == 0


# ============================================================
# TestSyncOrder — 订单同步
# ============================================================


class TestSyncOrder:
    @pytest.mark.asyncio
    async def test_empty_returns_zero(self):
        from services.kuaimai.erp_sync_handlers import sync_order
        assert await sync_order(_mock_svc(), START, END) == 0

    @pytest.mark.asyncio
    async def test_basic_order(self):
        from services.kuaimai.erp_sync_handlers import sync_order
        docs = [{
            "sid": "S001", "sysStatus": "FINISHED",
            "created": "2026-03-18", "tid": "T001",
            "shopName": "旗舰店", "source": "tb",
            "discountFee": None,
            "orders": [
                {"oid": "O1", "sysOuterId": "C01", "outerSkuId": "C01-01",
                 "title": "商品A", "num": 2, "price": 50.0,
                 "payment": 100.0},
            ],
        }]
        svc = _mock_svc(pages=docs)
        count = await sync_order(svc, START, END)
        assert count == 1
        row = svc.upsert_document_items.call_args[0][0][0]
        assert row["doc_type"] == "order"
        assert row["doc_id"] == "S001"
        assert row["shop_name"] == "旗舰店"

    @pytest.mark.asyncio
    async def test_discount_fee_distribution(self):
        """折扣按 payment 比例分摊，末项兜底"""
        from services.kuaimai.erp_sync_handlers import sync_order
        docs = [{
            "sid": "S002", "sysStatus": "FINISHED",
            "discountFee": 30.0,
            "orders": [
                {"oid": "O1", "sysOuterId": "C01",
                 "payment": "100.0", "num": 1, "price": 100.0},
                {"oid": "O2", "sysOuterId": "C02",
                 "payment": "200.0", "num": 1, "price": 200.0},
            ],
        }]
        svc = _mock_svc(pages=docs)
        count = await sync_order(svc, START, END)
        assert count == 2
        rows = svc.upsert_document_items.call_args[0][0]
        assert rows[0]["discount_fee"] == 10.0   # 100/300*30
        assert rows[1]["discount_fee"] == 20.0   # 30-10 兜底

    @pytest.mark.asyncio
    async def test_no_orders_skipped(self):
        """无 orders 的单据跳过"""
        from services.kuaimai.erp_sync_handlers import sync_order
        docs = [{"sid": "S003", "orders": None}]
        svc = _mock_svc(pages=docs)
        assert await sync_order(svc, START, END) == 0

    @pytest.mark.asyncio
    async def test_post_fee_only_on_first_item(self):
        """邮费仅挂在首条"""
        from services.kuaimai.erp_sync_handlers import sync_order
        docs = [{
            "sid": "S004", "sysStatus": "FINISHED",
            "postFee": "15.00", "discountFee": None,
            "orders": [
                {"oid": "A", "sysOuterId": "C01", "payment": 50.0,
                 "num": 1, "price": 50.0},
                {"oid": "B", "sysOuterId": "C02", "payment": 50.0,
                 "num": 1, "price": 50.0},
            ],
        }]
        svc = _mock_svc(pages=docs)
        await sync_order(svc, START, END)
        rows = svc.upsert_document_items.call_args[0][0]
        assert rows[0]["post_fee"] == "15.00"
        assert rows[1]["post_fee"] is None
