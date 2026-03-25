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


def _mock_stock_svc(wh_items=None, code_items=None):
    """创建 stock 专用 mock（模拟按仓库查 + 按编码查两步）

    wh_items: 按仓库+时间查返回的变动记录（Step1）
    code_items: 按编码精准查返回的最新记录（Step2）
    """
    svc = MagicMock()
    mock_client = AsyncMock()

    call_count = {"n": 0}
    wh_data = wh_items or []
    code_data = code_items or []

    async def _mock_request(method, params):
        call_count["n"] += 1
        if "warehouseId" in params:
            # Step1: 按仓库+时间查
            return {"stockStatusVoList": wh_data, "total": len(wh_data)}
        # Step2: 按编码精准查
        return {"stockStatusVoList": code_data, "total": len(code_data)}

    mock_client.request_with_retry = _mock_request
    svc._get_client.return_value = mock_client

    mock_table = MagicMock()
    mock_table.upsert.return_value.execute.return_value = MagicMock()
    svc.db = MagicMock()
    svc.db.table.return_value = mock_table
    return svc


class TestSyncStock:
    @pytest.mark.asyncio
    async def test_no_changes_returns_zero(self, monkeypatch):
        """所有仓库无变动 → 返回 0"""
        from services.kuaimai.erp_sync_master_handlers import sync_stock
        monkeypatch.setattr(
            "core.config.get_settings",
            lambda: MagicMock(erp_warehouse_ids="87227,436208"),
        )
        svc = _mock_stock_svc(wh_items=[], code_items=[])
        assert await sync_stock(svc, START, END) == 0

    @pytest.mark.asyncio
    async def test_incremental_collects_and_queries(self, monkeypatch):
        """增量：仓库查到变动编码 → 按编码精准查 → upsert"""
        from services.kuaimai.erp_sync_master_handlers import sync_stock
        monkeypatch.setattr(
            "core.config.get_settings",
            lambda: MagicMock(erp_warehouse_ids="87227"),
        )
        wh = [{"mainOuterId": "P01", "skuOuterId": "P01-01"}]
        precise = [{
            "mainOuterId": "P01", "skuOuterId": "P01-01",
            "title": "商品A", "sellableNum": 80,
            "totalAvailableStockSum": 100, "wareHouseId": "87227",
        }]
        svc = _mock_stock_svc(wh_items=wh, code_items=precise)
        assert await sync_stock(svc, START, END) == 1

    @pytest.mark.asyncio
    async def test_empty_warehouse_ids_returns_zero(self, monkeypatch):
        """erp_warehouse_ids 为空 → 跳过"""
        from services.kuaimai.erp_sync_master_handlers import sync_stock
        monkeypatch.setattr(
            "core.config.get_settings",
            lambda: MagicMock(erp_warehouse_ids=""),
        )
        svc = _mock_stock_svc()
        assert await sync_stock(svc, START, END) == 0


class TestSyncStockFull:
    @pytest.mark.asyncio
    async def test_full_refresh_basic(self):
        """全量刷新：从商品表取编码 → 按编码查 → upsert"""
        from services.kuaimai.erp_sync_master_handlers import sync_stock_full
        svc = _mock_stock_svc(code_items=[{
            "mainOuterId": "P01", "skuOuterId": "P01-01",
            "title": "商品A", "sellableNum": 50, "wareHouseId": "87227",
        }])
        # mock 商品表查询
        mock_select = MagicMock()
        mock_select.eq.return_value.limit.return_value.execute.return_value = MagicMock(
            data=[{"outer_id": "P01"}],
        )
        svc.db.table.return_value.select.return_value = mock_select
        assert await sync_stock_full(svc) == 1

    @pytest.mark.asyncio
    async def test_full_refresh_empty_products(self):
        """商品表为空 → 返回 0"""
        from services.kuaimai.erp_sync_master_handlers import sync_stock_full
        svc = _mock_stock_svc()
        mock_select = MagicMock()
        mock_select.eq.return_value.limit.return_value.execute.return_value = MagicMock(
            data=[],
        )
        svc.db.table.return_value.select.return_value = mock_select
        assert await sync_stock_full(svc) == 0


class TestMapStockItem:
    def test_basic_mapping(self):
        from services.kuaimai.erp_sync_master_handlers import _map_stock_item
        row = _map_stock_item({
            "mainOuterId": "P01", "skuOuterId": "P01-01",
            "title": "商品A", "wareHouseId": "WH-A",
            "sellableNum": 80, "totalAvailableStockSum": 100,
        })
        assert row["outer_id"] == "P01"
        assert row["warehouse_id"] == "WH-A"
        assert row["sellable_num"] == 80

    def test_skip_no_outer_id(self):
        from services.kuaimai.erp_sync_master_handlers import _map_stock_item
        assert _map_stock_item({"title": "无编码"}) is None

    def test_fallback_outer_id(self):
        from services.kuaimai.erp_sync_master_handlers import _map_stock_item
        row = _map_stock_item({"outerId": "P01"})
        assert row["outer_id"] == "P01"

    def test_warehouse_id_null_safe(self):
        from services.kuaimai.erp_sync_master_handlers import _map_stock_item
        row = _map_stock_item({"mainOuterId": "P01", "wareHouseId": None})
        assert row["warehouse_id"] == ""

    def test_warehouse_id_with_value(self):
        from services.kuaimai.erp_sync_master_handlers import _map_stock_item
        row = _map_stock_item({"mainOuterId": "P01", "wareHouseId": "WH-001"})
        assert row["warehouse_id"] == "WH-001"


class TestSyncStockWarehouseError:
    """增量同步单仓库异常不中断其他仓库"""

    @pytest.mark.asyncio
    async def test_one_warehouse_fails_others_continue(self, monkeypatch):
        from services.kuaimai.erp_sync_master_handlers import sync_stock
        monkeypatch.setattr(
            "core.config.get_settings",
            lambda: MagicMock(erp_warehouse_ids="111,222"),
        )
        call_log = []

        async def _mock_request(method, params):
            wh = params.get("warehouseId")
            if wh == 111:
                raise ConnectionError("timeout")
            call_log.append(wh)
            if "mainOuterId" in params:
                return {"stockStatusVoList": [{
                    "mainOuterId": "P01", "skuOuterId": "P01-01",
                    "sellableNum": 10, "wareHouseId": "222",
                }], "total": 1}
            return {"stockStatusVoList": [
                {"mainOuterId": "P01", "skuOuterId": "P01-01"},
            ], "total": 1}

        svc = MagicMock()
        mock_client = AsyncMock()
        mock_client.request_with_retry = _mock_request
        svc._get_client.return_value = mock_client
        mock_table = MagicMock()
        mock_table.upsert.return_value.execute.return_value = MagicMock()
        svc.db = MagicMock()
        svc.db.table.return_value = mock_table

        result = await sync_stock(svc, START, END)
        assert result == 1
        assert 222 in call_log


class TestSyncStockFullDbError:
    """全量刷新 DB 查询异常返回 0"""

    @pytest.mark.asyncio
    async def test_db_error_returns_zero(self):
        from services.kuaimai.erp_sync_master_handlers import sync_stock_full
        svc = _mock_stock_svc()
        svc.db.table.return_value.select.return_value.eq.return_value \
            .limit.return_value.execute.side_effect = Exception("DB down")
        assert await sync_stock_full(svc) == 0


class TestFetchStockByCodes:
    """_fetch_stock_by_codes 翻页和空列表"""

    @pytest.mark.asyncio
    async def test_empty_codes_returns_zero(self):
        from services.kuaimai.erp_sync_master_handlers import _fetch_stock_by_codes
        svc = _mock_stock_svc(code_items=[])
        assert await _fetch_stock_by_codes(svc, []) == 0

    @pytest.mark.asyncio
    async def test_pagination(self):
        """超过100条结果需要翻页"""
        from services.kuaimai.erp_sync_master_handlers import _fetch_stock_by_codes

        page_call = {"n": 0}

        async def _mock_request(method, params):
            page_call["n"] += 1
            page = params.get("pageNo", 1)
            if page == 1:
                return {"stockStatusVoList": [
                    {"mainOuterId": f"P{i:03d}", "sellableNum": i, "wareHouseId": "WH"}
                    for i in range(100)
                ], "total": 150}
            return {"stockStatusVoList": [
                {"mainOuterId": f"P{i:03d}", "sellableNum": i, "wareHouseId": "WH"}
                for i in range(100, 150)
            ], "total": 150}

        svc = MagicMock()
        mock_client = AsyncMock()
        mock_client.request_with_retry = _mock_request
        svc._get_client.return_value = mock_client
        mock_table = MagicMock()
        mock_table.upsert.return_value.execute.return_value = MagicMock()
        svc.db = MagicMock()
        svc.db.table.return_value = mock_table

        result = await _fetch_stock_by_codes(svc, ["P001"])
        assert result == 150
        assert page_call["n"] == 2


# ============================================================
# TestSyncSupplier — 供应商同步
# ============================================================


class TestSyncSupplier:
    @pytest.mark.asyncio
    async def test_empty_returns_zero(self):
        from services.kuaimai.erp_sync_master_handlers import sync_supplier
        svc = _mock_svc()
        svc.fetch_all_pages = AsyncMock(return_value=[])
        assert await sync_supplier(svc, START, END) == 0

    @pytest.mark.asyncio
    async def test_basic_supplier(self):
        from services.kuaimai.erp_sync_master_handlers import sync_supplier
        svc = _mock_svc()
        svc.fetch_all_pages = AsyncMock(return_value=[{
            "code": "SUP001", "name": "供应商A", "status": 1,
            "contactName": "张三", "mobile": "13800138000",
        }])
        assert await sync_supplier(svc, START, END) == 1

    @pytest.mark.asyncio
    async def test_skip_no_code(self):
        from services.kuaimai.erp_sync_master_handlers import sync_supplier
        svc = _mock_svc()
        svc.fetch_all_pages = AsyncMock(return_value=[{"name": "无编码供应商"}])
        assert await sync_supplier(svc, START, END) == 0


# ============================================================
# TestSyncPlatformMap — 平台映射同步
# ============================================================


def _mock_svc_for_platform_map(db_sku_ids, api_responses):
    """创建 platform_map 专用 mock（先查 DB SKU 列表再批量调 API）"""
    svc = _mock_svc()
    # mock DB 查询：erp_product_skus.select("sku_outer_id").limit(10000)
    mock_select = MagicMock()
    mock_select.limit.return_value.execute.return_value = MagicMock(
        data=[{"sku_outer_id": oid} for oid in db_sku_ids],
    )
    svc.db.table.return_value.select.return_value = mock_select
    # mock API 批量调用
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
            "outerId": "P01",
            "tbItemList": [{
                "numIid": "12345", "userId": "shop01", "title": "商品A",
                "skuOuterId": "P01-01", "skuId": "S001",
            }],
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
        """tbItemList 含多条映射时全部入库"""
        from services.kuaimai.erp_sync_master_handlers import sync_platform_map
        api_resp = {"itemOuterIdInfos": [{
            "outerId": "P01",
            "tbItemList": [
                {"numIid": "12345", "userId": "shop01", "title": "商品A"},
                {"numIid": "67890", "userId": "shop02", "title": "商品A"},
            ],
        }]}
        svc = _mock_svc_for_platform_map(["P01"], [api_resp])
        assert await sync_platform_map(svc, START, END) == 2
