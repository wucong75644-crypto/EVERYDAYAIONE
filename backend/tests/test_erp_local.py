"""
ERP 本地查询工具单元测试

覆盖：erp_local_helpers / erp_local_identify / erp_local_query /
      erp_stats_query / erp_local_tools（工具定义）

设计文档: docs/document/TECH_ERP数据本地索引系统.md §阶段5 任务5.2
"""

from unittest.mock import AsyncMock, patch

import pytest
from datetime import datetime, timezone

import sys
from pathlib import Path

_tests_dir = Path(__file__).parent
if str(_tests_dir) not in sys.path:
    sys.path.insert(0, str(_tests_dir))
_backend_dir = _tests_dir.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

from conftest import MockSupabaseClient


# ── 测试数据工厂 ─────────────────────────────────────


def _product(outer_id: str, **kw) -> dict:
    """创建商品测试数据"""
    base = {
        "outer_id": outer_id,
        "title": f"商品{outer_id}",
        "item_type": 0,
        "active_status": 1,
        "shipper": "测试货主",
        "barcode": "",
        "pic_url": "",
        "remark": "",
        "purchase_price": None,
        "suit_singles": None,
    }
    base.update(kw)
    return base


def _sku(outer_id: str, sku_outer_id: str, **kw) -> dict:
    """创建SKU测试数据"""
    base = {
        "outer_id": outer_id,
        "sku_outer_id": sku_outer_id,
        "properties_name": "默认规格",
        "barcode": "",
        "pic_url": "",
    }
    base.update(kw)
    return base


def _doc_item(doc_type: str, doc_id: str, outer_id: str, **kw) -> dict:
    """创建单据明细测试数据"""
    base = {
        "doc_type": doc_type,
        "doc_id": doc_id,
        "outer_id": outer_id,
        "sku_outer_id": "",
        "item_index": 0,
        "quantity": 100,
        "amount": 1000.0,
        "status": "FINISHED",
        "doc_created_at": "2026-03-18T10:00:00+00:00",
        "doc_modified_at": "2026-03-18T10:00:00+00:00",
        "shop_name": "旗舰店",
        "platform": "tb",
        "extra": {},
    }
    base.update(kw)
    return base


def _sync_state(sync_type: str, healthy: bool = True) -> dict:
    """创建同步状态数据"""
    now = datetime.now(timezone.utc).isoformat()
    return {
        "sync_type": sync_type,
        "last_run_at": now,
        "error_count": 0 if healthy else 5,
        "is_initial_done": True,
    }


def _stock(outer_id: str, sku_id: str, **kw) -> dict:
    """创建库存测试数据"""
    base = {
        "outer_id": outer_id,
        "sku_outer_id": sku_id,
        "properties_name": "默认",
        "sellable_num": 100,
        "total_num": 150,
        "lock_num": 30,
        "purchase_on_way_num": 200,
        "stock_status": 1,
        "safe_stock_num": 50,
        "updated_at": "2026-03-18T10:00:00+00:00",
    }
    base.update(kw)
    return base


def _daily_stat(outer_id: str, stat_date: str, **kw) -> dict:
    """创建日统计测试数据"""
    base = {
        "outer_id": outer_id,
        "stat_date": stat_date,
        "order_count": 10,
        "order_qty": 20,
        "order_amount": 2000.0,
        "purchase_count": 1,
        "purchase_qty": 100,
        "purchase_amount": 5000.0,
        "receipt_count": 1,
        "receipt_qty": 80,
        "shelf_count": 1,
        "shelf_qty": 80,
        "aftersale_count": 2,
        "aftersale_qty": 3,
        "aftersale_amount": 300.0,
        "return_count": 0,
        "return_qty": 0,
    }
    base.update(kw)
    return base


def _platform_map(outer_id: str, num_iid: str, **kw) -> dict:
    """创建平台映射测试数据"""
    base = {
        "outer_id": outer_id,
        "num_iid": num_iid,
        "user_id": "shop_001",
        "platform": "tb",
        "sku_count": 3,
        "updated_at": "2026-03-18T10:00:00+00:00",
    }
    base.update(kw)
    return base


def _make_db(**table_data) -> MockSupabaseClient:
    """创建带预设数据的 MockDB"""
    db = MockSupabaseClient()
    for name, data in table_data.items():
        db.set_table_data(name, data)
    return db


# ============================================================
# TestLocalHelpers — 共享工具函数
# ============================================================


class TestCheckSyncHealth:

    def test_healthy_returns_empty(self):
        """同步健康时返回空字符串"""
        from services.kuaimai.erp_local_helpers import check_sync_health
        db = _make_db(erp_sync_state=[_sync_state("product")])
        result = check_sync_health(db, ["product"])
        assert result == ""

    def test_error_count_triggers_warning(self):
        """error_count>=3 触发警告"""
        from services.kuaimai.erp_local_helpers import check_sync_health
        db = _make_db(erp_sync_state=[_sync_state("product", healthy=False)])
        result = check_sync_health(db, ["product"])
        assert "⚠" in result
        assert "连续失败" in result

    def test_initial_not_done_shows_info(self):
        """首次同步未完成显示提示"""
        from services.kuaimai.erp_local_helpers import check_sync_health
        state = _sync_state("order")
        state["is_initial_done"] = False
        db = _make_db(erp_sync_state=[state])
        result = check_sync_health(db, ["order"])
        assert "同步进行中" in result

    def test_old_last_run_triggers_warning(self):
        """last_run_at 超过5分钟触发警告"""
        from services.kuaimai.erp_local_helpers import check_sync_health
        state = _sync_state("purchase")
        state["last_run_at"] = "2020-01-01T00:00:00+00:00"
        db = _make_db(erp_sync_state=[state])
        result = check_sync_health(db, ["purchase"])
        assert "⚠" in result


class TestCutoffIso:

    def test_returns_iso_string(self):
        """返回有效 ISO 日期字符串"""
        from services.kuaimai.erp_local_helpers import cutoff_iso
        result = cutoff_iso(30)
        assert "T" in result
        dt = datetime.fromisoformat(result)
        assert dt.tzinfo is not None


# ============================================================
# TestLocalIdentify — 本地编码识别
# ============================================================


class TestLocalIdentify:

    @pytest.mark.asyncio
    async def test_no_params_returns_error(self):
        """未传参数返回提示"""
        from services.kuaimai.erp_local_identify import local_product_identify
        from services.agent.tool_output import OutputStatus, ToolOutput
        db = MockSupabaseClient()
        result = await local_product_identify(db)
        assert isinstance(result, ToolOutput)
        assert result.status == OutputStatus.ERROR
        assert "至少一个参数" in result.summary

    @pytest.mark.asyncio
    async def test_code_match_outer_id(self):
        """编码匹配主编码"""
        from services.kuaimai.erp_local_identify import local_product_identify
        from services.agent.tool_output import ToolOutput
        db = _make_db(
            erp_products=[_product("TEST01", title="测试商品")],
            erp_product_skus=[],
            erp_sync_state=[_sync_state("product")],
        )
        result = await local_product_identify(db, code="TEST01")
        assert isinstance(result, ToolOutput)
        assert "✓" in result.summary
        assert "主编码" in result.summary

    @pytest.mark.asyncio
    async def test_code_match_sku(self):
        """编码匹配SKU编码"""
        from services.kuaimai.erp_local_identify import local_product_identify
        from services.agent.tool_output import ToolOutput
        db = _make_db(
            erp_products=[],
            erp_product_skus=[_sku("MAIN01", "SKU01-01")],
            erp_sync_state=[_sync_state("product")],
        )
        result = await local_product_identify(db, code="SKU01-01")
        assert isinstance(result, ToolOutput)
        assert "✓" in result.summary
        assert "SKU编码" in result.summary

    @pytest.mark.asyncio
    async def test_code_match_barcode(self):
        """编码匹配条码"""
        from services.kuaimai.erp_local_identify import local_product_identify
        from services.agent.tool_output import ToolOutput
        db = _make_db(
            erp_products=[_product("BC01", barcode="6901234567890")],
            erp_product_skus=[],
            erp_sync_state=[_sync_state("product")],
        )
        result = await local_product_identify(db, code="6901234567890")
        assert isinstance(result, ToolOutput)
        assert "条码" in result.summary

    @pytest.mark.asyncio
    @patch("services.kuaimai.client.KuaiMaiClient")
    async def test_code_not_found(self, MockClient):
        """编码未识别（本地+API均未找到）"""
        from services.kuaimai.erp_local_identify import local_product_identify
        from services.agent.tool_output import OutputStatus, ToolOutput

        mock_client = AsyncMock()
        mock_client.is_configured = False
        mock_client.close = AsyncMock()
        MockClient.return_value = mock_client

        db = _make_db(
            erp_products=[],
            erp_product_skus=[],
            erp_sync_state=[_sync_state("product")],
        )
        result = await local_product_identify(db, code="NOTEXIST")
        assert isinstance(result, ToolOutput)
        assert result.status == OutputStatus.EMPTY
        assert "不存在" in result.summary

    @pytest.mark.asyncio
    async def test_name_search(self):
        """名称搜索模式"""
        from services.kuaimai.erp_local_identify import local_product_identify
        from services.agent.tool_output import ToolOutput
        db = _make_db(
            erp_products=[
                _product("P01", title="猫粮旗舰款"),
                _product("P02", title="狗粮经典款"),
            ],
            erp_product_skus=[_sku("P01", "P01-01")],
            erp_sync_state=[_sync_state("product")],
        )
        result = await local_product_identify(db, name="猫粮")
        assert isinstance(result, ToolOutput)
        assert result.data is not None
        assert "匹配到" in result.summary
        assert "猫粮" in result.summary

    @pytest.mark.asyncio
    async def test_spec_search(self):
        """规格搜索模式"""
        from services.kuaimai.erp_local_identify import local_product_identify
        from services.agent.tool_output import ToolOutput
        db = _make_db(
            erp_products=[_product("P01", title="测试商品")],
            erp_product_skus=[
                _sku("P01", "P01-01", properties_name="红色 XL"),
                _sku("P01", "P01-02", properties_name="蓝色 M"),
            ],
            erp_sync_state=[_sync_state("product")],
        )
        result = await local_product_identify(db, spec="红色")
        assert isinstance(result, ToolOutput)
        assert result.data is not None
        assert "匹配到" in result.summary
        assert "红色" in result.summary

    @pytest.mark.asyncio
    async def test_suite_product_shows_children(self):
        """套件商品显示子单品"""
        from services.kuaimai.erp_local_identify import local_product_identify
        from services.agent.tool_output import ToolOutput
        db = _make_db(
            erp_products=[_product(
                "SUITE01", item_type=2, title="套件商品",
                suit_singles=[
                    {"outerId": "CHILD01", "ratio": 1},
                    {"outerId": "CHILD02", "ratio": 2},
                ],
            )],
            erp_product_skus=[],
            erp_document_items=[],
            erp_sync_state=[_sync_state("product")],
        )
        result = await local_product_identify(db, code="SUITE01")
        assert isinstance(result, ToolOutput)
        assert "套件子单品" in result.summary
        assert "CHILD01" in result.summary


# TestLocalPurchaseQuery/AftersaleQuery/OrderQuery/ProductFlow 已移除
# — 功能统一由 local_data (UnifiedQueryEngine) 覆盖


class TestLocalStockQuery:

    @pytest.mark.asyncio
    async def test_basic_stock(self):
        """基础库存查询"""
        from services.kuaimai.erp_local_query import local_stock_query
        db = _make_db(
            erp_stock_status=[
                _stock("C01", "C01-01", sellable_num=100),
                _stock("C01", "C01-02", sellable_num=5, stock_status=2),
            ],
            erp_sync_state=[_sync_state("product")],
        )
        from services.agent.tool_output import ToolOutput
        result = await local_stock_query(db, "C01")
        assert isinstance(result, ToolOutput)
        assert result.data is not None

    @pytest.mark.asyncio
    async def test_no_stock(self):
        """无库存数据"""
        from services.kuaimai.erp_local_query import local_stock_query
        db = _make_db(
            erp_stock_status=[],
            erp_sync_state=[_sync_state("product")],
        )
        result = await local_stock_query(db, "NOSTOCK")
        assert "无" in result.summary or "未" in result.summary or "0" in result.summary

    @pytest.mark.asyncio
    async def test_multi_warehouse_display(self):
        """多仓分组展示：不同仓库各显示一组 + 有汇总行"""
        from services.kuaimai.erp_local_query import local_stock_query
        db = _make_db(
            erp_stock_status=[
                _stock("C01", "C01-01", warehouse_id="WH-A",
                       sellable_num=50, total_stock=80, lock_stock=10,
                       purchase_num=20),
                _stock("C01", "C01-01", warehouse_id="WH-B",
                       sellable_num=30, total_stock=40, lock_stock=5,
                       purchase_num=10),
            ],
            erp_sync_state=[_sync_state("stock")],
        )
        result = await local_stock_query(db, "C01")
        # 验证按仓库分组
        assert "WH-A" in result.summary
        assert "WH-B" in result.summary
        # 验证汇总（50+30=80）
        assert "80" in result.summary

    @pytest.mark.asyncio
    async def test_multi_warehouse_with_names(self):
        """多仓分组展示：注入仓库表后输出仓库名称而非裸ID"""
        from services.kuaimai.erp_local_query import local_stock_query
        db = _make_db(
            erp_stock_status=[
                _stock("C01", "C01-01", warehouse_id="WH-A",
                       sellable_num=50, total_stock=80),
                _stock("C01", "C01-02", warehouse_id="WH-B",
                       sellable_num=30, total_stock=40),
            ],
            erp_warehouses=[
                {"warehouse_id": "WH-A", "name": "义乌主仓"},
                {"warehouse_id": "WH-B", "name": "杭州分仓"},
            ],
            erp_sync_state=[_sync_state("stock")],
        )
        result = await local_stock_query(db, "C01")
        assert "义乌主仓" in result.summary
        assert "杭州分仓" in result.summary
        # 裸ID不应出现在仓库标签中
        assert "仓库: WH-A" not in result.summary
        assert "仓库: WH-B" not in result.summary

    @pytest.mark.asyncio
    async def test_warehouse_name_fallback_when_no_data(self):
        """仓库表无数据时降级回warehouse_id"""
        from services.kuaimai.erp_local_query import local_stock_query
        db = _make_db(
            erp_stock_status=[
                _stock("C01", "C01-01", warehouse_id="WH-X",
                       sellable_num=10, total_stock=20),
                _stock("C01", "C01-02", warehouse_id="WH-Y",
                       sellable_num=5, total_stock=10),
            ],
            erp_sync_state=[_sync_state("stock")],
        )
        result = await local_stock_query(db, "C01")
        assert "WH-X" in result.summary
        assert "WH-Y" in result.summary

    @pytest.mark.asyncio
    async def test_single_warehouse_no_group(self):
        """单仓不分组（保持原有逻辑）"""
        from services.kuaimai.erp_local_query import local_stock_query
        db = _make_db(
            erp_stock_status=[
                _stock("C01", "C01-01", warehouse_id="WH-A",
                       sellable_num=50, total_stock=80),
                _stock("C01", "C01-02", warehouse_id="WH-A",
                       sellable_num=20, total_stock=30),
            ],
            erp_sync_state=[_sync_state("stock")],
        )
        result = await local_stock_query(db, "C01")
        # 单仓不显示仓库分组头
        assert "WH-B" not in result.summary
        assert "SKU" in result.summary

    @pytest.mark.asyncio
    async def test_low_stock_multi_warehouse(self):
        """多仓 low_stock=True 过滤"""
        from services.kuaimai.erp_local_query import local_stock_query
        db = _make_db(
            erp_stock_status=[
                _stock("C01", "C01-01", warehouse_id="WH-A",
                       sellable_num=50, total_stock=80),
                _stock("C01", "C01-02", warehouse_id="WH-B",
                       sellable_num=3, total_stock=5),
            ],
            erp_sync_state=[_sync_state("stock")],
        )
        result = await local_stock_query(db, "C01", low_stock=True)
        # 只保留 sellable < 10 的 SKU
        assert "C01-02" in result.summary
        # C01-01 被过滤掉（sellable=50 >= 10）
        assert "C01-01" not in result.summary


class TestLocalStockQueryKitFallback:
    """套件库存物化视图 fallback 分支"""

    @pytest.mark.asyncio
    async def test_kit_fallback_when_stock_empty(self):
        """erp_stock_status 无数据时 fallback 到 mv_kit_stock"""
        from services.kuaimai.erp_local_query import local_stock_query
        db = _make_db(
            erp_stock_status=[],
            mv_kit_stock=[
                {
                    "outer_id": "TJ-KIT01",
                    "sku_outer_id": "TJ-KIT01-01",
                    "item_name": "套件测试",
                    "properties_name": "规格A",
                    "warehouse_id": "",
                    "sellable_num": 100,
                    "total_stock": 150,
                    "lock_stock": 0,
                    "purchase_num": 50,
                    "stock_status": 1,
                },
            ],
            erp_sync_state=[_sync_state("stock")],
        )
        result = await local_stock_query(db, "TJ-KIT01-01")
        assert "套件" in result.summary
        assert "100" in result.summary

    @pytest.mark.asyncio
    async def test_kit_fallback_with_status_filter(self):
        """套件查询透传 stock_status 过滤"""
        from services.kuaimai.erp_local_query import local_stock_query
        db = _make_db(
            erp_stock_status=[],
            mv_kit_stock=[
                {
                    "outer_id": "TJ-KIT01",
                    "sku_outer_id": "TJ-KIT01-01",
                    "item_name": "套件测试",
                    "properties_name": "规格A",
                    "warehouse_id": "",
                    "sellable_num": 100,
                    "total_stock": 150,
                    "lock_stock": 0,
                    "purchase_num": 50,
                    "stock_status": 1,
                },
            ],
            erp_sync_state=[_sync_state("stock")],
        )
        # stock_status=3(无货) 不匹配 status=1 的数据 → 无结果
        result = await local_stock_query(db, "TJ-KIT01-01", stock_status="3")
        assert "无库存记录" in result.summary or "无" in result.summary

    @pytest.mark.asyncio
    async def test_kit_fallback_table_not_exist(self):
        """mv_kit_stock 表不存在时静默降级"""
        from services.kuaimai.erp_local_query import local_stock_query
        db = _make_db(
            erp_stock_status=[],
            erp_sync_state=[_sync_state("stock")],
        )
        # mv_kit_stock 未设置数据 → MockDB 会抛异常或返回空
        result = await local_stock_query(db, "TJ-NOKIT")
        assert "无库存记录" in result.summary or "无" in result.summary

    @pytest.mark.asyncio
    async def test_normal_stock_takes_priority(self):
        """erp_stock_status 有数据时不走 kit fallback"""
        from services.kuaimai.erp_local_query import local_stock_query
        db = _make_db(
            erp_stock_status=[
                _stock("C01", "C01-01", sellable_num=200),
            ],
            mv_kit_stock=[
                {
                    "outer_id": "C01",
                    "sku_outer_id": "C01-01",
                    "item_name": "不应出现",
                    "properties_name": "",
                    "warehouse_id": "",
                    "sellable_num": 999,
                    "total_stock": 999,
                    "lock_stock": 0,
                    "purchase_num": 0,
                    "stock_status": 1,
                },
            ],
            erp_sync_state=[_sync_state("stock")],
        )
        result = await local_stock_query(db, "C01")
        # 应显示普通库存 200，不是套件的 999
        assert "套件" not in result.summary
        assert "200" in result.summary


class TestLocalPlatformMapQuery:

    @pytest.mark.asyncio
    async def test_by_product_code(self):
        """按商品编码查平台映射"""
        from services.kuaimai.erp_local_query import local_platform_map_query
        db = _make_db(
            erp_product_platform_map=[
                _platform_map("C01", "123456"),
                _platform_map("C01", "789012", platform="pdd"),
            ],
            erp_products=[_product("C01", title="测试商品")],
            erp_sync_state=[_sync_state("product")],
        )
        from services.agent.tool_output import ToolOutput
        result = await local_platform_map_query(db, product_code="C01")
        assert isinstance(result, ToolOutput)
        assert result.data is not None

    @pytest.mark.asyncio
    async def test_no_params_returns_error(self):
        """未传参数返回提示"""
        from services.kuaimai.erp_local_query import local_platform_map_query
        db = MockSupabaseClient()
        result = await local_platform_map_query(db)
        assert "product_code" in result.summary or "num_iid" in result.summary

    @pytest.mark.asyncio
    async def test_with_shop_names(self):
        """注入店铺表后输出店铺名称(平台)而非裸user_id"""
        from services.kuaimai.erp_local_query import local_platform_map_query
        db = _make_db(
            erp_product_platform_map=[
                _platform_map("C01", "111", user_id="S001"),
                _platform_map("C01", "222", user_id="S002"),
            ],
            erp_products=[_product("C01", title="测试商品")],
            erp_shops=[
                {"shop_id": "S001", "name": "旗舰店", "platform": "tb"},
                {"shop_id": "S002", "name": "拼多多店", "platform": "pdd"},
            ],
            erp_sync_state=[_sync_state("product")],
        )
        result = await local_platform_map_query(db, product_code="C01")
        assert "旗舰店(淘宝)" in result.summary
        assert "拼多多店(拼多多)" in result.summary
        # 裸ID不应出现
        assert "S001" not in result.summary
        assert "S002" not in result.summary

    @pytest.mark.asyncio
    async def test_shop_name_fallback_when_no_data(self):
        """店铺表无数据时降级回user_id"""
        from services.kuaimai.erp_local_query import local_platform_map_query
        db = _make_db(
            erp_product_platform_map=[
                _platform_map("C01", "111", user_id="UNKNOWN_SHOP"),
            ],
            erp_products=[_product("C01", title="测试商品")],
            erp_sync_state=[_sync_state("product")],
        )
        result = await local_platform_map_query(db, product_code="C01")
        assert "UNKNOWN_SHOP" in result.summary


# ============================================================
# TestStatsQuery — 统计报表查询
# ============================================================


class TestLocalProductStats:

    @pytest.mark.asyncio
    async def test_with_data(self):
        """有统计数据"""
        from services.kuaimai.erp_stats_query import local_product_stats
        from services.agent.tool_output import ToolOutput
        db = _make_db(
            erp_product_daily_stats=[
                _daily_stat("C01", "2026-03-18"),
                _daily_stat("C01", "2026-03-17"),
            ],
            erp_sync_state=[_sync_state("order")],
        )
        result = await local_product_stats(db, "C01", start_date="2026-03-17", end_date="2026-03-18")
        assert isinstance(result, ToolOutput)
        assert result.data is not None
        assert "统计" in result.summary
        assert "销售" in result.summary

    @pytest.mark.asyncio
    async def test_no_data(self):
        """无统计数据"""
        from services.kuaimai.erp_stats_query import local_product_stats
        from services.agent.tool_output import OutputStatus, ToolOutput
        db = _make_db(
            erp_product_daily_stats=[],
            erp_sync_state=[_sync_state("order")],
        )
        result = await local_product_stats(db, "NODATA")
        assert isinstance(result, ToolOutput)
        assert result.status == OutputStatus.EMPTY
        assert "无统计数据" in result.summary

    @pytest.mark.asyncio
    async def test_aftersale_rate(self):
        """售后率计算"""
        from services.kuaimai.erp_stats_query import local_product_stats
        from services.agent.tool_output import ToolOutput
        db = _make_db(
            erp_product_daily_stats=[
                _daily_stat("C01", "2026-03-18",
                            order_count=100, aftersale_count=10),
            ],
            erp_sync_state=[],
        )
        result = await local_product_stats(db, "C01", start_date="2026-03-18", end_date="2026-03-18")
        assert isinstance(result, ToolOutput)
        assert "售后率" in result.summary


# ============================================================
# TestLocalShopList — 店铺列表查询
# ============================================================


class TestLocalShopList:

    @pytest.mark.asyncio
    async def test_with_data(self):
        """正常返回多店铺，按平台分组"""
        from services.kuaimai.erp_local_query import local_shop_list
        db = _make_db(erp_sync_state=[_sync_state("order")])
        db.set_rpc_result("erp_distinct_shops", [
            {"shop_name": "旗舰店", "platform": "tb"},
            {"shop_name": "拼多多官方店", "platform": "pdd"},
            {"shop_name": "京东自营", "platform": "jd"},
        ])
        result = await local_shop_list(db)
        assert "共 3 个店铺" in result.summary
        assert "旗舰店" in result.summary
        assert "【pdd】" in result.summary
        assert "【tb】" in result.summary

    @pytest.mark.asyncio
    async def test_no_data(self):
        """无店铺数据返回提示"""
        from services.kuaimai.erp_local_query import local_shop_list
        db = _make_db(erp_sync_state=[_sync_state("order")])
        db.set_rpc_result("erp_distinct_shops", [])
        result = await local_shop_list(db)
        assert "暂无店铺数据" in result.summary

    @pytest.mark.asyncio
    async def test_platform_filter(self):
        """按平台过滤"""
        from services.kuaimai.erp_local_query import local_shop_list
        db = _make_db(erp_sync_state=[_sync_state("order")])
        db.set_rpc_result("erp_distinct_shops", [
            {"shop_name": "拼多多官方店", "platform": "pdd"},
        ])
        result = await local_shop_list(db, platform="pdd")
        assert "拼多多官方店" in result.summary
        assert "共 1 个店铺" in result.summary

    @pytest.mark.asyncio
    async def test_empty_shop_name_filtered(self):
        """空店铺名被过滤"""
        from services.kuaimai.erp_local_query import local_shop_list
        db = _make_db(erp_sync_state=[_sync_state("order")])
        db.set_rpc_result("erp_distinct_shops", [
            {"shop_name": "", "platform": "tb"},
            {"shop_name": "  ", "platform": "tb"},
            {"shop_name": "旗舰店", "platform": "tb"},
        ])
        result = await local_shop_list(db)
        assert "共 1 个店铺" in result.summary

    @pytest.mark.asyncio
    async def test_rpc_error(self):
        """RPC 报错返回错误信息"""
        from services.kuaimai.erp_local_query import local_shop_list
        db = _make_db()
        # mock RPC 抛异常
        from unittest.mock import MagicMock
        mock_rpc = MagicMock()
        mock_rpc.execute.side_effect = Exception("function erp_distinct_shops does not exist")
        db.rpc = MagicMock(return_value=mock_rpc)
        result = await local_shop_list(db)
        assert "查询失败" in result.summary

    @pytest.mark.asyncio
    async def test_no_data_with_platform_label(self):
        """无数据时平台标签显示"""
        from services.kuaimai.erp_local_query import local_shop_list
        db = _make_db(erp_sync_state=[_sync_state("order")])
        db.set_rpc_result("erp_distinct_shops", [])
        result = await local_shop_list(db, platform="pdd")
        assert "平台: 拼多多" in result.summary


# ============================================================
# TestLocalSupplierList — 供应商列表查询
# ============================================================


class TestLocalSupplierList:

    @pytest.mark.asyncio
    async def test_with_data(self):
        """正常返回多供应商，按分类分组"""
        from services.kuaimai.erp_local_query import local_supplier_list
        db = _make_db(
            erp_suppliers=[
                {"code": "0001", "name": "供应商A", "status": 1,
                 "contact_name": "张三", "mobile": "13800001111",
                 "category_name": "采购陈,跟单徐", "remark": ""},
                {"code": "0002", "name": "供应商B", "status": 1,
                 "contact_name": None, "mobile": None,
                 "category_name": "采购段,跟单马", "remark": ""},
                {"code": "0003", "name": "供应商C", "status": 0,
                 "contact_name": "李四", "mobile": "13900002222",
                 "category_name": None, "remark": ""},
            ],
            erp_sync_state=[_sync_state("supplier")],
        )
        result = await local_supplier_list(db)
        assert "共 3 个供应商" in result.summary
        assert "供应商A" in result.summary
        assert "供应商B" in result.summary
        assert "供应商C" in result.summary
        assert "采购陈" in result.summary
        assert "未分类" in result.summary

    @pytest.mark.asyncio
    async def test_filter_by_category(self):
        """按分类过滤"""
        from services.kuaimai.erp_local_query import local_supplier_list
        db = _make_db(
            erp_suppliers=[
                {"code": "0001", "name": "供应商A", "status": 1,
                 "contact_name": None, "mobile": None,
                 "category_name": "采购陈", "remark": ""},
            ],
            erp_sync_state=[_sync_state("supplier")],
        )
        result = await local_supplier_list(db, category="采购陈")
        assert "供应商A" in result.summary

    @pytest.mark.asyncio
    async def test_empty_data(self):
        """无供应商数据"""
        from services.kuaimai.erp_local_query import local_supplier_list
        db = _make_db(
            erp_suppliers=[],
            erp_sync_state=[_sync_state("supplier")],
        )
        result = await local_supplier_list(db)
        assert "暂无供应商数据" in result.summary

    @pytest.mark.asyncio
    async def test_contact_info_display(self):
        """联系人信息正确展示"""
        from services.kuaimai.erp_local_query import local_supplier_list
        db = _make_db(
            erp_suppliers=[
                {"code": "0001", "name": "供应商A", "status": 1,
                 "contact_name": "张三", "mobile": "13800001111",
                 "category_name": None, "remark": ""},
            ],
            erp_sync_state=[_sync_state("supplier")],
        )
        result = await local_supplier_list(db)
        assert "联系人:张三" in result.summary
        assert "13800001111" in result.summary

    @pytest.mark.asyncio
    async def test_disabled_supplier_label(self):
        """停用供应商有标记"""
        from services.kuaimai.erp_local_query import local_supplier_list
        db = _make_db(
            erp_suppliers=[
                {"code": "0001", "name": "停用供应商", "status": 0,
                 "contact_name": None, "mobile": None,
                 "category_name": None, "remark": ""},
            ],
            erp_sync_state=[_sync_state("supplier")],
        )
        result = await local_supplier_list(db)
        assert "[停用]" in result.summary


# ============================================================
# TestLocalToolDefinitions — 工具定义结构
# ============================================================


class TestBuildLocalTools:

    def test_returns_10_tools(self):
        """build_local_tools 返回 10 个工具（1 统一查询 + 8 专用 + 1 同步）"""
        from config.erp_local_tools import build_local_tools
        tools = build_local_tools()
        assert len(tools) == 10

    def test_each_tool_structure(self):
        """每个工具有完整 function calling 结构"""
        from config.erp_local_tools import build_local_tools
        for tool in build_local_tools():
            assert tool["type"] == "function"
            func = tool["function"]
            assert "name" in func
            assert "description" in func
            assert "parameters" in func
            assert func["parameters"]["type"] == "object"

    def test_local_tool_names(self):
        """工具名与 ERP_LOCAL_TOOLS 集合一致"""
        from config.erp_local_tools import ERP_LOCAL_TOOLS, build_local_tools
        names = {t["function"]["name"] for t in build_local_tools()}
        assert names == ERP_LOCAL_TOOLS

    def test_schemas_cover_all_tools(self):
        """LOCAL_TOOL_SCHEMAS 覆盖全部8个工具"""
        from config.erp_local_tools import ERP_LOCAL_TOOLS, LOCAL_TOOL_SCHEMAS
        assert set(LOCAL_TOOL_SCHEMAS.keys()) == ERP_LOCAL_TOOLS

    def test_identify_tool_has_no_required(self):
        """local_product_identify 无必填参数（code/name/spec 至少传一个）"""
        from config.erp_local_tools import build_local_tools
        tools = build_local_tools()
        identify = [t for t in tools
                    if t["function"]["name"] == "local_product_identify"][0]
        assert identify["function"]["parameters"]["required"] == []

    def test_query_tools_require_product_code(self):
        """product_stats 和 stock_query 必填 product_code"""
        from config.erp_local_tools import build_local_tools
        required_tools = {
            "local_product_stats", "local_stock_query",
        }
        tools = build_local_tools()
        for tool in tools:
            name = tool["function"]["name"]
            if name in required_tools:
                assert "product_code" in tool["function"]["parameters"]["required"], (
                    f"{name} 缺少 product_code 必填"
                )

    def test_local_data_tool_definition(self):
        """local_data 统一查询工具定义正确"""
        from config.erp_local_tools import build_local_tools
        tools = build_local_tools()
        ld = [t for t in tools
              if t["function"]["name"] == "local_data"][0]
        params = ld["function"]["parameters"]
        assert "doc_type" in params["required"]
        assert "filters" in params["required"]
        assert "filters" in params["properties"]
        assert params["properties"]["filters"]["type"] == "array"
        assert "mode" in params["properties"]
        assert "summary" in params["properties"]["mode"]["enum"]
        assert "detail" in params["properties"]["mode"]["enum"]
        assert "export" in params["properties"]["mode"]["enum"]

    def test_local_data_has_limit_description(self):
        """local_data limit 描述包含默认值和上限"""
        from config.erp_local_tools import build_local_tools
        tools = build_local_tools()
        ld = [t for t in tools
              if t["function"]["name"] == "local_data"][0]
        desc = ld["function"]["parameters"]["properties"]["limit"]["description"]
        assert "20" in desc
        assert "10000" in desc


class TestLocalToolRegistry:

    def test_local_tools_registered_in_tool_registry(self):
        """本地工具在 tool_registry 中注册且 priority=1"""
        from config.tool_registry import TOOL_REGISTRY
        from config.erp_local_tools import ERP_LOCAL_TOOLS
        for name in ERP_LOCAL_TOOLS:
            entry = TOOL_REGISTRY.get(name)
            assert entry is not None, f"{name} 未注册"
            assert entry.priority == 1, f"{name} priority={entry.priority}"


class TestToolExecutorLocalDispatch:

    def test_local_tools_registered(self):
        """ToolExecutor 注册了8个本地工具"""
        from config.erp_local_tools import ERP_LOCAL_TOOLS
        from services.tool_executor import ToolExecutor
        db = MockSupabaseClient()
        executor = ToolExecutor(db, "test_user", "test_conv", org_id="org-test")
        for tool_name in ERP_LOCAL_TOOLS:
            assert tool_name in executor._handlers


# ============================================================
# CN_TZ 时区 + cutoff_iso 使用中国时间
# ============================================================


class TestCNTimezone:
    """CN_TZ 常量和使用中国时间的函数"""

    def test_cn_tz_is_utc_plus_8(self):
        from services.kuaimai.erp_local_helpers import CN_TZ
        from datetime import datetime, timedelta
        # ZoneInfo 的 utcoffset 需要传一个 datetime 才能算（DST aware）
        # 中国 1991 年起无夏令时，传任意 datetime 结果都是 +08:00
        offset = CN_TZ.utcoffset(datetime(2026, 4, 10))
        assert offset == timedelta(hours=8)

    def test_cutoff_iso_uses_cn_tz(self):
        """cutoff_iso 应使用中国时区"""
        from services.kuaimai.erp_local_helpers import cutoff_iso
        result = cutoff_iso(30)
        dt = datetime.fromisoformat(result)
        # 应该带 +08:00 时区信息
        assert dt.utcoffset().total_seconds() == 8 * 3600


# ============================================================
# local_global_stats time_type 参数
# ============================================================


class TestUnifiedSchemaTimeType:
    """统一查询引擎 time_type 常量校验"""

    def test_valid_time_cols(self):
        from services.kuaimai.erp_unified_schema import VALID_TIME_COLS
        assert "doc_created_at" in VALID_TIME_COLS
        assert "pay_time" in VALID_TIME_COLS
        assert "consign_time" in VALID_TIME_COLS

    def test_invalid_time_type_not_in_whitelist(self):
        """无效 time_type 不在白名单中"""
        from services.kuaimai.erp_unified_schema import VALID_TIME_COLS
        invalid = "hacked_column; DROP TABLE"
        assert invalid not in VALID_TIME_COLS

    def test_local_data_tool_has_time_type_param(self):
        """local_data 工具定义应包含 time_type 参数"""
        from config.erp_local_tools import build_local_tools
        tools = build_local_tools()
        data_tool = next(
            t for t in tools
            if t["function"]["name"] == "local_data"
        )
        props = data_tool["function"]["parameters"]["properties"]
        assert "time_type" in props
        assert "pay_time" in props["time_type"]["enum"]
        assert "consign_time" in props["time_type"]["enum"]
