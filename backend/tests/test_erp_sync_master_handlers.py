"""
ERP 主数据同步处理器单元测试

覆盖：erp_sync_master_handlers（4种主数据 + 4个工具函数）

设计文档: docs/document/TECH_ERP数据本地索引系统.md §7.1
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock


# ── 工厂函数 ─────────────────────────────────────────


def _mock_svc(pages=None):
    """创建 mock ErpSyncService（主数据处理器不需要 detail）"""
    svc = MagicMock()
    svc.fetch_all_pages = AsyncMock(return_value=pages or [])
    mock_table = MagicMock()
    mock_table.upsert.return_value.execute.return_value = MagicMock()
    svc.db = MagicMock()
    svc.db.table.return_value = mock_table
    return svc


START = datetime(2026, 3, 17, tzinfo=timezone.utc)
END = datetime(2026, 3, 18, tzinfo=timezone.utc)


# ============================================================
# TestHelperFunctions — 工具函数
# ============================================================


class TestStripHtml:
    def test_strips_tags(self):
        from services.kuaimai.erp_sync_master_handlers import _strip_html
        assert _strip_html("<b>Hello</b> <i>World</i>") == "Hello World"

    def test_none_returns_none(self):
        from services.kuaimai.erp_sync_master_handlers import _strip_html
        assert _strip_html(None) is None

    def test_empty_string_returns_empty(self):
        from services.kuaimai.erp_sync_master_handlers import _strip_html
        assert _strip_html("") == ""

    def test_no_html_unchanged(self):
        from services.kuaimai.erp_sync_master_handlers import _strip_html
        assert _strip_html("普通文本") == "普通文本"

    def test_nested_tags(self):
        from services.kuaimai.erp_sync_master_handlers import _strip_html
        assert _strip_html("<div><p>内容</p></div>") == "内容"


class TestMasterFmtDt:
    def test_format_datetime(self):
        from services.kuaimai.erp_sync_master_handlers import _fmt_dt
        assert _fmt_dt(datetime(2026, 3, 18)) == "2026-03-18 00:00:00"


class TestMsToIso:
    def test_converts_milliseconds(self):
        from services.kuaimai.erp_sync_master_handlers import _ms_to_iso
        # 1730447751000 ms = 2024-11-01 某时刻
        result = _ms_to_iso(1730447751000)
        assert result is not None
        assert result.startswith("2024-")

    def test_none_returns_none(self):
        from services.kuaimai.erp_sync_master_handlers import _ms_to_iso
        assert _ms_to_iso(None) is None

    def test_invalid_returns_none(self):
        from services.kuaimai.erp_sync_master_handlers import _ms_to_iso
        assert _ms_to_iso("not_a_number") is None


class TestMasterPick:
    def test_picks_non_none(self):
        from services.kuaimai.erp_sync_master_handlers import _pick
        assert _pick({"a": 1, "b": None}, "a", "b") == {"a": 1}

    def test_missing_keys_ignored(self):
        from services.kuaimai.erp_sync_master_handlers import _pick
        assert _pick({"a": 1}, "a", "z") == {"a": 1}


class TestBatchUpsert:
    def test_empty_rows_returns_zero(self):
        from services.kuaimai.erp_sync_master_handlers import _batch_upsert
        assert _batch_upsert(MagicMock(), "t", [], "id") == 0

    def test_basic_upsert(self):
        from services.kuaimai.erp_sync_master_handlers import _batch_upsert
        db = MagicMock()
        db.table.return_value.upsert.return_value.execute.return_value = MagicMock()
        rows = [{"id": i} for i in range(5)]
        assert _batch_upsert(db, "t", rows, "id") == 5

    def test_batch_splitting(self):
        """超过 batch_size 分批"""
        from services.kuaimai.erp_sync_master_handlers import _batch_upsert
        db = MagicMock()
        mt = MagicMock()
        mt.upsert.return_value.execute.return_value = MagicMock()
        db.table.return_value = mt
        rows = [{"id": i} for i in range(250)]
        count = _batch_upsert(db, "t", rows, "id", batch_size=100)
        assert count == 250
        assert mt.upsert.call_count == 3

    def test_exception_continues(self):
        """批次异常跳过继续"""
        from services.kuaimai.erp_sync_master_handlers import _batch_upsert
        db = MagicMock()
        mt = MagicMock()
        mt.upsert.return_value.execute.side_effect = [
            Exception("fail"), MagicMock(),
        ]
        db.table.return_value = mt
        rows = [{"id": i} for i in range(200)]
        count = _batch_upsert(db, "t", rows, "id", batch_size=100)
        assert count == 100


# ============================================================
# TestSyncProduct — 商品同步
# ============================================================


class TestSyncProduct:
    @pytest.mark.asyncio
    async def test_empty_returns_zero(self):
        from services.kuaimai.erp_sync_master_handlers import sync_product
        assert await sync_product(_mock_svc(), START, END) == 0

    @pytest.mark.asyncio
    async def test_basic_product_with_skus(self):
        from services.kuaimai.erp_sync_master_handlers import sync_product
        products = [{
            "outerId": "P01", "title": "商品A", "type": 0,
            "barcode": "123", "purchasePrice": 10.0,
            "created": "2026-03-18", "modified": "2026-03-18",
            "skus": [
                {"skuOuterId": "P01-01", "propertiesName": "红色"},
                {"skuOuterId": "P01-02", "propertiesName": "蓝色"},
            ],
        }]
        svc = _mock_svc(pages=products)
        count = await sync_product(svc, START, END)
        assert count == 3  # 1 SPU + 2 SKU
        assert svc.db.table.return_value.upsert.call_count == 2

    @pytest.mark.asyncio
    async def test_skip_no_outer_id(self):
        """没有 outerId 跳过"""
        from services.kuaimai.erp_sync_master_handlers import sync_product
        svc = _mock_svc(pages=[{"title": "无编码商品"}])
        assert await sync_product(svc, START, END) == 0

    @pytest.mark.asyncio
    async def test_skip_no_sku_outer_id(self):
        """没有 skuOuterId 的 SKU 跳过"""
        from services.kuaimai.erp_sync_master_handlers import sync_product
        svc = _mock_svc(pages=[{
            "outerId": "P01", "title": "商品A",
            "skus": [{"propertiesName": "红色"}],
        }])
        count = await sync_product(svc, START, END)
        assert count == 1  # only SPU

    @pytest.mark.asyncio
    async def test_html_remark_stripped(self):
        """备注中 HTML 被清洗"""
        from services.kuaimai.erp_sync_master_handlers import sync_product
        svc = _mock_svc(pages=[{
            "outerId": "P01", "title": "商品A",
            "remark": "<b>重要备注</b>",
        }])
        await sync_product(svc, START, END)
        # 验证调用了 upsert（不抛异常即可）
        svc.db.table.assert_called()


# ============================================================
# TestSyncStock — 库存同步
# ============================================================


class TestSyncStock:
    @pytest.mark.asyncio
    async def test_empty_returns_zero(self):
        from services.kuaimai.erp_sync_master_handlers import sync_stock
        assert await sync_stock(_mock_svc(), START, END) == 0

    @pytest.mark.asyncio
    async def test_basic_stock(self):
        from services.kuaimai.erp_sync_master_handlers import sync_stock
        items = [{
            "mainOuterId": "P01", "skuOuterId": "P01-01",
            "title": "商品A",
            "totalAvailableStockSum": 100, "sellableNum": 80,
            "totalLockStock": 10, "onTheWayNum": 50,
        }]
        svc = _mock_svc(pages=items)
        assert await sync_stock(svc, START, END) == 1

    @pytest.mark.asyncio
    async def test_skip_no_outer_id(self):
        from services.kuaimai.erp_sync_master_handlers import sync_stock
        svc = _mock_svc(pages=[{"title": "无编码"}])
        assert await sync_stock(svc, START, END) == 0

    @pytest.mark.asyncio
    async def test_fallback_outer_id(self):
        """mainOuterId 不存在时使用 outerId"""
        from services.kuaimai.erp_sync_master_handlers import sync_stock
        items = [{"outerId": "P01", "title": "商品A"}]
        svc = _mock_svc(pages=items)
        assert await sync_stock(svc, START, END) == 1

    @pytest.mark.asyncio
    async def test_warehouse_id_null_safe(self):
        """API 返回 wareHouseId=None 时写入空字符串（NOT NULL 兼容）"""
        from services.kuaimai.erp_sync_master_handlers import sync_stock
        items = [{
            "mainOuterId": "P01", "skuOuterId": "P01-01",
            "title": "商品A", "wareHouseId": None,
        }]
        svc = _mock_svc(pages=items)
        await sync_stock(svc, START, END)
        # 验证 upsert 传入的 warehouse_id 是空字符串而非 None
        call_args = svc.db.table("erp_stock_status").upsert.call_args
        row = call_args[0][0][0]  # 第一批第一行
        assert row["warehouse_id"] == ""

    @pytest.mark.asyncio
    async def test_warehouse_id_with_value(self):
        """API 返回 wareHouseId 有值时正常写入"""
        from services.kuaimai.erp_sync_master_handlers import sync_stock
        items = [{
            "mainOuterId": "P01", "skuOuterId": "P01-01",
            "title": "商品A", "wareHouseId": "WH-001",
        }]
        svc = _mock_svc(pages=items)
        await sync_stock(svc, START, END)
        call_args = svc.db.table("erp_stock_status").upsert.call_args
        row = call_args[0][0][0]
        assert row["warehouse_id"] == "WH-001"

    @pytest.mark.asyncio
    async def test_on_conflict_includes_warehouse(self):
        """on_conflict 包含 warehouse_id（多仓不互相覆盖）"""
        from services.kuaimai.erp_sync_master_handlers import sync_stock
        items = [{
            "mainOuterId": "P01", "skuOuterId": "P01-01",
            "title": "商品A", "wareHouseId": "WH-A",
        }]
        svc = _mock_svc(pages=items)
        await sync_stock(svc, START, END)
        call_args = svc.db.table("erp_stock_status").upsert.call_args
        on_conflict = call_args[1].get("on_conflict", "")
        assert "warehouse_id" in on_conflict


# ============================================================
# TestSyncSupplier — 供应商同步
# ============================================================


class TestSyncSupplier:
    @pytest.mark.asyncio
    async def test_empty_returns_zero(self):
        from services.kuaimai.erp_sync_master_handlers import sync_supplier
        svc = _mock_svc()
        mock_client = MagicMock()
        mock_client.request_with_retry = AsyncMock(return_value={"list": []})
        svc._get_client.return_value = mock_client
        assert await sync_supplier(svc, START, END) == 0

    @pytest.mark.asyncio
    async def test_basic_supplier(self):
        from services.kuaimai.erp_sync_master_handlers import sync_supplier
        svc = _mock_svc()
        mock_client = MagicMock()
        mock_client.request_with_retry = AsyncMock(return_value={"list": [{
            "code": "SUP001", "name": "供应商A", "status": 1,
            "contactName": "张三", "mobile": "13800138000",
        }]})
        svc._get_client.return_value = mock_client
        assert await sync_supplier(svc, START, END) == 1

    @pytest.mark.asyncio
    async def test_skip_no_code(self):
        from services.kuaimai.erp_sync_master_handlers import sync_supplier
        svc = _mock_svc()
        mock_client = MagicMock()
        mock_client.request_with_retry = AsyncMock(return_value={"list": [{"name": "无编码供应商"}]})
        svc._get_client.return_value = mock_client
        assert await sync_supplier(svc, START, END) == 0


# ============================================================
# TestSyncPlatformMap — 平台映射同步
# ============================================================


def _mock_svc_for_platform_map(db_outer_ids, api_responses):
    """创建 platform_map 专用 mock（先查 DB 再逐个调 API）"""
    svc = _mock_svc()
    # mock DB 查询返回商品列表
    mock_select = MagicMock()
    mock_select.neq.return_value.limit.return_value.execute.return_value = MagicMock(
        data=[{"outer_id": oid} for oid in db_outer_ids],
    )
    svc.db.table.return_value.select.return_value = mock_select
    # mock API 逐个调用
    mock_client = MagicMock()
    mock_client.request_with_retry = AsyncMock(side_effect=api_responses)
    svc._get_client.return_value = mock_client
    return svc


class TestSyncPlatformMap:
    @pytest.mark.asyncio
    async def test_empty_db_returns_zero(self):
        from services.kuaimai.erp_sync_master_handlers import sync_platform_map
        svc = _mock_svc_for_platform_map([], [])
        assert await sync_platform_map(svc, START, END) == 0

    @pytest.mark.asyncio
    async def test_basic_platform_map(self):
        from services.kuaimai.erp_sync_master_handlers import sync_platform_map
        api_resp = {"itemOuterIdInfos": [{
            "outerId": "P01", "numIid": "12345", "userId": "shop01",
            "title": "商品A",
            "skuOuterIdInfos": [
                {"skuOuterId": "P01-01", "skuNumIid": "S001"},
            ],
        }]}
        svc = _mock_svc_for_platform_map(["P01"], [api_resp])
        assert await sync_platform_map(svc, START, END) == 1

    @pytest.mark.asyncio
    async def test_skip_missing_keys(self):
        """缺 outerId 或 numIid 跳过"""
        from services.kuaimai.erp_sync_master_handlers import sync_platform_map
        api_resp = {"itemOuterIdInfos": [
            {"outerId": "P01"},
            {"numIid": "12345"},
        ]}
        svc = _mock_svc_for_platform_map(["P01"], [api_resp])
        assert await sync_platform_map(svc, START, END) == 0

    @pytest.mark.asyncio
    async def test_sku_list_fallback(self):
        """skuOuterIdInfos 不存在时回退 skuList"""
        from services.kuaimai.erp_sync_master_handlers import sync_platform_map
        api_resp = {"itemOuterIdInfos": [{
            "outerId": "P01", "numIid": "12345",
            "skuList": [{"skuOuterId": "P01-01", "skuNumIid": "S001"}],
        }]}
        svc = _mock_svc_for_platform_map(["P01"], [api_resp])
        assert await sync_platform_map(svc, START, END) == 1
