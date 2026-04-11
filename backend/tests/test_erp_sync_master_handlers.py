"""
ERP 主数据同步处理器单元测试

覆盖：erp_sync_master_handlers（4种主数据 + 4个工具函数）

设计文档: docs/document/TECH_ERP数据本地索引系统.md §7.1
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import sys
from pathlib import Path

_tests_dir = Path(__file__).parent
if str(_tests_dir) not in sys.path:
    sys.path.insert(0, str(_tests_dir))
_backend_dir = _tests_dir.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

from conftest import MockErpAsyncDBClient


# ── 工厂函数 ─────────────────────────────────────────


def _mock_svc(pages=None):
    """创建 mock ErpSyncService（主数据处理器不需要 detail）

    注意：_lock_extend_fn 默认 None，让代码走干净的 None 分支。
    需要测续锁的测试请显式设 svc._lock_extend_fn = AsyncMock()。
    （MagicMock 默认属性是 truthy 且 await 时会抛 TypeError，
    会被代码 try/except 隐藏从而掩盖问题。）
    """
    svc = MagicMock()
    svc.fetch_all_pages = AsyncMock(return_value=pages or [])
    svc.db = MockErpAsyncDBClient()
    svc.org_id = None
    svc._apply_org = lambda q: q
    svc._lock_extend_fn = None
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
    @pytest.mark.asyncio
    async def test_empty_rows_returns_zero(self):
        from services.kuaimai.erp_sync_master_handlers import _batch_upsert
        db = MockErpAsyncDBClient()
        assert await _batch_upsert(db, "t", [], "id") == 0

    @pytest.mark.asyncio
    async def test_basic_upsert(self):
        from services.kuaimai.erp_sync_master_handlers import _batch_upsert
        db = MockErpAsyncDBClient()
        rows = [{"id": i} for i in range(5)]
        assert await _batch_upsert(db, "t", rows, "id") == 5

    @pytest.mark.asyncio
    async def test_batch_splitting(self):
        """超过 batch_size 分批"""
        from services.kuaimai.erp_sync_master_handlers import _batch_upsert
        db = MockErpAsyncDBClient()
        rows = [{"id": i} for i in range(250)]
        count = await _batch_upsert(db, "t", rows, "id", batch_size=100)
        assert count == 250

    @pytest.mark.asyncio
    async def test_exception_continues(self):
        """批次异常跳过继续（async mock 下用 side_effect 模拟异常）"""
        from services.kuaimai.erp_sync_utils import _batch_upsert
        db = MagicMock()
        mt = MagicMock()
        call_count = 0
        async def _mock_execute():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("fail")
            return MagicMock()
        mt.upsert.return_value.execute = _mock_execute
        db.table.return_value = mt
        rows = [{"id": i} for i in range(200)]
        count = await _batch_upsert(db, "t", rows, "id", batch_size=100)
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
        count = await sync_product(svc, START, END)
        # 验证 upsert 写入了数据（1 SPU）
        assert count == 1

    @pytest.mark.asyncio
    async def test_dimensions_extracted_to_columns(self):
        """x/y/z 提升为 length/width/height 独立列"""
        from services.kuaimai.erp_sync_master_handlers import sync_product
        svc = _mock_svc(pages=[{
            "outerId": "P01", "title": "商品A",
            "x": 29.2, "y": 21.6, "z": 3.8,
            "skus": [
                {"skuOuterId": "P01-01", "propertiesName": "红色",
                 "x": 10.0, "y": 8.0, "z": 2.0},
            ],
        }])
        count = await sync_product(svc, START, END)
        assert count == 2  # 1 SPU + 1 SKU
        # 验证 SPU 尺寸写入独立列
        spu_data = svc.db._tables["erp_products"]._data
        assert spu_data[0]["length"] == 29.2
        assert spu_data[0]["width"] == 21.6
        assert spu_data[0]["height"] == 3.8
        # 验证 x/y/z 不再出现在 extra_json
        assert "x" not in spu_data[0].get("extra_json", {})
        # 验证 SKU 尺寸写入独立列
        sku_data = svc.db._tables["erp_product_skus"]._data
        assert sku_data[0]["length"] == 10.0
        assert sku_data[0]["width"] == 8.0
        assert sku_data[0]["height"] == 2.0

    @pytest.mark.asyncio
    async def test_dimensions_none_when_missing(self):
        """API 未返回尺寸时，列值为 None"""
        from services.kuaimai.erp_sync_master_handlers import sync_product
        svc = _mock_svc(pages=[{
            "outerId": "P02", "title": "无尺寸商品",
        }])
        count = await sync_product(svc, START, END)
        assert count == 1
        spu_data = svc.db._tables["erp_products"]._data
        assert spu_data[0]["length"] is None
        assert spu_data[0]["width"] is None
        assert spu_data[0]["height"] is None

    @pytest.mark.asyncio
    async def test_classify_and_seller_cat_extracted(self):
        """classify_name 和 seller_cat_name 正确提取"""
        from services.kuaimai.erp_sync_master_handlers import sync_product
        svc = _mock_svc(pages=[{
            "outerId": "P01", "title": "卡册A",
            "classify": {"id": 9179, "name": "卡册", "parentId": -1},
            "sellerCats": [
                {"id": 1, "name": "亚克力", "fullName": '["亚克力"]'},
                {"id": 2, "name": "立牌", "fullName": '["亚克力","立牌"]'},
            ],
        }])
        await sync_product(svc, START, END)
        spu_data = svc.db._tables["erp_products"]._data
        assert spu_data[0]["classify_name"] == "卡册"
        # 取最后一个（最具体的）分类
        assert spu_data[0]["seller_cat_name"] == '["亚克力","立牌"]'

    @pytest.mark.asyncio
    async def test_classify_none_when_missing(self):
        """API 未返回 classify 时为 None"""
        from services.kuaimai.erp_sync_master_handlers import sync_product
        svc = _mock_svc(pages=[{"outerId": "P01", "title": "商品A"}])
        await sync_product(svc, START, END)
        spu_data = svc.db._tables["erp_products"]._data
        assert spu_data[0]["classify_name"] is None

    @pytest.mark.asyncio
    async def test_seller_cat_empty_array(self):
        """sellerCats 为空数组时为 None"""
        from services.kuaimai.erp_sync_master_handlers import sync_product
        svc = _mock_svc(pages=[{
            "outerId": "P01", "title": "商品A",
            "sellerCats": [],
        }])
        await sync_product(svc, START, END)
        spu_data = svc.db._tables["erp_products"]._data
        assert spu_data[0]["seller_cat_name"] is None

    @pytest.mark.asyncio
    async def test_sku_remark_extracted(self):
        """sku_remark 正确提取"""
        from services.kuaimai.erp_sync_master_handlers import sync_product
        svc = _mock_svc(pages=[{
            "outerId": "P01", "title": "商品A",
            "skus": [
                {"skuOuterId": "P01-01", "skuRemark": "清完下"},
                {"skuOuterId": "P01-02", "skuRemark": ""},
            ],
        }])
        await sync_product(svc, START, END)
        sku_data = svc.db._tables["erp_product_skus"]._data
        assert sku_data[0]["sku_remark"] == "清完下"
        assert sku_data[1]["sku_remark"] is None  # 空字符串转 None


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

    wh_data = wh_items or []
    code_data = code_items or []

    async def _mock_request(method, params):
        if "warehouseId" in params:
            return {"stockStatusVoList": wh_data, "total": len(wh_data)}
        return {"stockStatusVoList": code_data, "total": len(code_data)}

    mock_client.request_with_retry = _mock_request
    svc._get_client.return_value = mock_client
    svc.db = MockErpAsyncDBClient()
    svc.org_id = None
    svc._apply_org = lambda q: q  # 散客模式不过滤
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
        # 预填 erp_products 表数据
        svc.db.set_table_data("erp_products", [
            {"outer_id": "P01", "active_status": 1},
        ])
        assert await sync_stock_full(svc) == 1

    @pytest.mark.asyncio
    async def test_full_refresh_empty_products(self):
        """商品表为空 → 返回 0"""
        from services.kuaimai.erp_sync_master_handlers import sync_stock_full
        svc = _mock_stock_svc()
        # erp_products 表为空（默认）
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
        svc.org_id = None
        mock_client = AsyncMock()
        mock_client.request_with_retry = _mock_request
        svc._get_client.return_value = mock_client
        svc.db = MockErpAsyncDBClient()

        result = await sync_stock(svc, START, END)
        assert result == 1
        assert 222 in call_log


class TestSyncStockFullDbError:
    """全量刷新 DB 查询异常返回 0"""

    @pytest.mark.asyncio
    async def test_db_error_returns_zero(self):
        from services.kuaimai.erp_sync_master_handlers import sync_stock_full
        svc = _mock_stock_svc()
        # 让 erp_products 表的 execute 抛异常
        tbl = svc.db.table("erp_products")
        async def _raise_execute():
            raise Exception("DB down")
        tbl.execute = _raise_execute
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
        svc.db = MockErpAsyncDBClient()

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
    # 预填 erp_product_skus 表数据
    svc.db.set_table_data(
        "erp_product_skus",
        [{"sku_outer_id": oid} for oid in db_sku_ids],
    )
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


# ============================================================
# TestSyncPlatformMapIncremental — Bug 1+2 新增覆盖
# ============================================================


class TestSyncPlatformMapIncremental:
    """Bug 1+2 修复后的增量 + 异常分类行为测试

    覆盖场景：
    - checked_at 写入（成功路径 + 20150 路径）
    - 致命错误 raise（TokenExpired / Signature）
    - 半批降级（payload too large）
    - 未知业务错误 → DLQ
    - 网络/未知错误 → 不标记 + 不抛
    """

    @pytest.mark.asyncio
    async def test_marks_checked_at_on_success(self):
        """成功响应后整批 SKU 应被 UPDATE checked_at"""
        from services.kuaimai.erp_sync_master_handlers import sync_platform_map
        api_resp = {"itemOuterIdInfos": [{
            "outerId": "SKU01",
            "tbItemList": [{
                "numIid": "TBN1", "userId": "shop", "title": "X",
            }],
        }]}
        svc = _mock_svc_for_platform_map(["SKU01"], [api_resp])
        await sync_platform_map(svc, START, END)

        # 校验 erp_product_skus 表的 update 被调用且包含 checked_at
        # MockErpAsyncTable 的 update() 把数据存在 self._update_data
        sku_table = svc.db._tables["erp_product_skus"]
        assert hasattr(sku_table, "_update_data")
        assert "platform_map_checked_at" in sku_table._update_data

    @pytest.mark.asyncio
    async def test_marks_checked_at_on_20150_business_error(self):
        """整批无映射（20150）也应标记 checked_at（业务正常路径）"""
        from services.kuaimai.errors import KuaiMaiBusinessError
        from services.kuaimai.erp_sync_master_handlers import sync_platform_map

        svc = _mock_svc_for_platform_map(["SKU_NO_MAP"], [])
        # 让 client 抛 20150
        mock_client = MagicMock()
        mock_client.request_with_retry = AsyncMock(
            side_effect=KuaiMaiBusinessError(
                message="outerIds 不存在", code="20150",
            )
        )
        svc._get_client.return_value = mock_client

        # 不应抛异常
        await sync_platform_map(svc, START, END)

        # checked_at 应被标记
        sku_table = svc.db._tables["erp_product_skus"]
        assert hasattr(sku_table, "_update_data")
        assert "platform_map_checked_at" in sku_table._update_data

    @pytest.mark.asyncio
    async def test_token_expired_raises_to_caller(self):
        """TokenExpiredError 必须 raise 让 _update_sync_state_error 涨 error_count"""
        from services.kuaimai.errors import KuaiMaiTokenExpiredError
        from services.kuaimai.erp_sync_master_handlers import sync_platform_map

        svc = _mock_svc_for_platform_map(["SKU1"], [])
        mock_client = MagicMock()
        mock_client.request_with_retry = AsyncMock(
            side_effect=KuaiMaiTokenExpiredError()
        )
        svc._get_client.return_value = mock_client

        with pytest.raises(KuaiMaiTokenExpiredError):
            await sync_platform_map(svc, START, END)

    @pytest.mark.asyncio
    async def test_signature_error_raises_to_caller(self):
        """SignatureError 同样必须 raise 触发告警"""
        from services.kuaimai.errors import KuaiMaiSignatureError
        from services.kuaimai.erp_sync_master_handlers import sync_platform_map

        svc = _mock_svc_for_platform_map(["SKU1"], [])
        mock_client = MagicMock()
        mock_client.request_with_retry = AsyncMock(
            side_effect=KuaiMaiSignatureError()
        )
        svc._get_client.return_value = mock_client

        with pytest.raises(KuaiMaiSignatureError):
            await sync_platform_map(svc, START, END)

    @pytest.mark.asyncio
    async def test_network_error_skips_batch_no_mark(self):
        """网络/未知错误：跳过此批，不抛、不标记"""
        from services.kuaimai.erp_sync_master_handlers import sync_platform_map

        svc = _mock_svc_for_platform_map(["SKU1"], [])
        mock_client = MagicMock()
        mock_client.request_with_retry = AsyncMock(
            side_effect=ConnectionError("network down")
        )
        svc._get_client.return_value = mock_client

        # 不应抛异常
        result = await sync_platform_map(svc, START, END)
        assert result == 0

        # 这一批未被标记 checked_at（_update_data 不应存在）
        sku_table = svc.db._tables["erp_product_skus"]
        assert not hasattr(sku_table, "_update_data")

    @pytest.mark.asyncio
    async def test_payload_too_large_splits_batch(self):
        """code=1 payload too large 应触发半批降级递归"""
        from unittest.mock import patch
        from services.kuaimai.errors import KuaiMaiBusinessError
        from services.kuaimai.erp_sync_master_handlers import sync_platform_map

        # 80 个 SKU + ROUND_FRACTION=1 让本轮处理全部 80 个
        sku_ids = [f"S{i:03d}" for i in range(80)]
        svc = _mock_svc_for_platform_map(sku_ids, [])

        call_sizes: list[int] = []

        async def fake_request(method, params):
            ids = params["outerIds"].split(",")
            call_sizes.append(len(ids))
            # 第一次（80 个）触发 payload too large；后续半批成功
            if len(ids) >= 80:
                raise KuaiMaiBusinessError(
                    message="Data length too large: too big",
                    code="1",
                )
            return {"itemOuterIdInfos": []}

        mock_client = MagicMock()
        mock_client.request_with_retry = AsyncMock(side_effect=fake_request)
        svc._get_client.return_value = mock_client

        # patch ROUND_FRACTION 让本轮处理全部 80 个 SKU
        # 必须 patch 实际定义模块（platform_map.py），而不是 __init__ re-export
        with patch(
            "services.kuaimai.erp_sync_master_handlers.platform_map._PLATFORM_MAP_ROUND_FRACTION",
            1,
        ):
            await sync_platform_map(svc, START, END)

        # 应该至少调用 3 次：80 失败 → 40+40 各成功
        assert call_sizes[0] == 80
        assert any(s == 40 for s in call_sizes[1:])
        assert len([s for s in call_sizes if s == 40]) == 2

    @pytest.mark.asyncio
    async def test_unknown_business_error_writes_to_dlq(self):
        """未知业务错误（非 20150 / 非 1）写入死信队列"""
        from services.kuaimai.errors import KuaiMaiBusinessError
        from services.kuaimai.erp_sync_master_handlers import sync_platform_map
        from unittest.mock import patch

        svc = _mock_svc_for_platform_map(["SKU_X"], [])
        mock_client = MagicMock()
        mock_client.request_with_retry = AsyncMock(
            side_effect=KuaiMaiBusinessError(
                message="unknown business error", code="99999",
            )
        )
        svc._get_client.return_value = mock_client

        with patch(
            "services.kuaimai.erp_sync_dead_letter.record_dead_letter",
            new_callable=AsyncMock,
        ) as mock_record:
            await sync_platform_map(svc, START, END)

        mock_record.assert_called_once()
        # 校验 doc_type / detail_method
        kwargs = mock_record.call_args.kwargs
        assert kwargs["doc_type"] == "platform_map_batch"
        assert kwargs["detail_method"] == "erp.item.outerid.list.get"
        # failed_docs 里应包含 sku_ids
        failed = kwargs["failed_docs"]
        assert len(failed) == 1
        assert failed[0]["sku_ids"] == ["SKU_X"]
        assert failed[0]["id"].startswith("pm_batch_")  # hash doc_id

    @pytest.mark.asyncio
    async def test_increment_uses_count_query_for_round_size(self):
        """增量化：本轮处理量 = total / 4（向上取整）"""
        from services.kuaimai.erp_sync_master_handlers import sync_platform_map

        # 100 个 SKU → 本轮应取 25
        sku_ids = [f"S{i:03d}" for i in range(100)]
        svc = _mock_svc_for_platform_map(sku_ids, [])
        mock_client = MagicMock()
        # 返回空结果，让 sync 顺利跑完
        mock_client.request_with_retry = AsyncMock(
            return_value={"itemOuterIdInfos": []}
        )
        svc._get_client.return_value = mock_client

        await sync_platform_map(svc, START, END)

        # 至少应调过一次 API
        assert mock_client.request_with_retry.called
        # 第一批传入的 outerIds 数量应是 min(25, BATCH_SIZE=400) = 25
        first_call = mock_client.request_with_retry.call_args_list[0]
        first_outer_ids = first_call.args[1]["outerIds"].split(",")
        assert len(first_outer_ids) == 25

    @pytest.mark.asyncio
    async def test_lock_extend_called_periodically_with_small_batch(self):
        """续锁路径：构造 > 10 批 让 lock_extend_fn 被调到至少 1 次。

        用 patch 把 BATCH_SIZE 临时设小到 10，配合 ROUND_FRACTION=1，
        让 110 个 SKU 拆出 11 批 → 第 10 批后触发续锁。
        """
        from unittest.mock import patch
        from services.kuaimai.erp_sync_master_handlers import sync_platform_map

        sku_ids = [f"S{i:04d}" for i in range(110)]
        svc = _mock_svc_for_platform_map(sku_ids, [])
        mock_client = MagicMock()
        mock_client.request_with_retry = AsyncMock(
            return_value={"itemOuterIdInfos": []},
        )
        svc._get_client.return_value = mock_client

        # 显式注入 AsyncMock 续锁回调
        lock_extend = AsyncMock()
        svc._lock_extend_fn = lock_extend

        # patch 实际定义模块（platform_map.py），而不是 __init__ re-export
        with patch(
            "services.kuaimai.erp_sync_master_handlers.platform_map._PLATFORM_MAP_BATCH_SIZE",
            10,
        ), patch(
            "services.kuaimai.erp_sync_master_handlers.platform_map._PLATFORM_MAP_ROUND_FRACTION",
            1,
        ):
            await sync_platform_map(svc, START, END)

        # 110 SKU / 10 = 11 批 → 第 10 批后触发 1 次续锁
        # 注意：第 11 批是最后一批，(11) % 10 != 0 不触发
        assert lock_extend.call_count == 1
