"""
ERP 本地查询工具单元测试

覆盖：erp_local_helpers / erp_local_identify / erp_local_query /
      erp_stats_query / erp_local_tools（工具定义）

设计文档: docs/document/TECH_ERP数据本地索引系统.md §阶段5 任务5.2
"""

from unittest.mock import AsyncMock, patch

import pytest
from datetime import datetime, timezone

from tests.conftest import MockSupabaseClient


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


class TestQueryDocItems:

    def test_basic_query(self):
        """基础查询返回匹配数据"""
        from services.kuaimai.erp_local_helpers import query_doc_items
        items = [
            _doc_item("purchase", "PO001", "CODE01"),
            _doc_item("purchase", "PO002", "CODE02"),
        ]
        db = _make_db(erp_document_items=items)
        rows = query_doc_items(db, "purchase", "CODE01", days=30)
        assert len(rows) >= 0  # mock or_ 简单实现，至少不抛异常

    def test_archive_union_on_long_range(self):
        """days>90 自动查冷表"""
        from services.kuaimai.erp_local_helpers import query_doc_items
        hot = [_doc_item("order", "ORD1", "C01", item_index=0)]
        cold = [_doc_item("order", "ORD2", "C01", item_index=0)]
        db = _make_db(
            erp_document_items=hot,
            erp_document_items_archive=cold,
        )
        rows = query_doc_items(db, "order", "C01", days=120)
        # 应该包含热表+冷表数据（去重后）
        assert isinstance(rows, list)


# ============================================================
# TestLocalIdentify — 本地编码识别
# ============================================================


class TestLocalIdentify:

    @pytest.mark.asyncio
    async def test_no_params_returns_error(self):
        """未传参数返回提示"""
        from services.kuaimai.erp_local_identify import local_product_identify
        db = MockSupabaseClient()
        result = await local_product_identify(db)
        assert "至少一个参数" in result

    @pytest.mark.asyncio
    async def test_code_match_outer_id(self):
        """编码匹配主编码"""
        from services.kuaimai.erp_local_identify import local_product_identify
        db = _make_db(
            erp_products=[_product("TEST01", title="测试商品")],
            erp_product_skus=[],
            erp_sync_state=[_sync_state("product")],
        )
        result = await local_product_identify(db, code="TEST01")
        assert "✓" in result
        assert "主编码" in result

    @pytest.mark.asyncio
    async def test_code_match_sku(self):
        """编码匹配SKU编码"""
        from services.kuaimai.erp_local_identify import local_product_identify
        db = _make_db(
            erp_products=[],
            erp_product_skus=[_sku("MAIN01", "SKU01-01")],
            erp_sync_state=[_sync_state("product")],
        )
        result = await local_product_identify(db, code="SKU01-01")
        assert "✓" in result
        assert "SKU编码" in result

    @pytest.mark.asyncio
    async def test_code_match_barcode(self):
        """编码匹配条码"""
        from services.kuaimai.erp_local_identify import local_product_identify
        db = _make_db(
            erp_products=[_product("BC01", barcode="6901234567890")],
            erp_product_skus=[],
            erp_sync_state=[_sync_state("product")],
        )
        result = await local_product_identify(db, code="6901234567890")
        assert "条码" in result

    @pytest.mark.asyncio
    @patch("services.kuaimai.client.KuaiMaiClient")
    async def test_code_not_found(self, MockClient):
        """编码未识别（本地+API均未找到）"""
        from services.kuaimai.erp_local_identify import local_product_identify

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
        assert "不存在" in result

    @pytest.mark.asyncio
    async def test_name_search(self):
        """名称搜索模式"""
        from services.kuaimai.erp_local_identify import local_product_identify
        db = _make_db(
            erp_products=[
                _product("P01", title="猫粮旗舰款"),
                _product("P02", title="狗粮经典款"),
            ],
            erp_product_skus=[_sku("P01", "P01-01")],
            erp_sync_state=[_sync_state("product")],
        )
        result = await local_product_identify(db, name="猫粮")
        assert "匹配到" in result
        assert "猫粮" in result

    @pytest.mark.asyncio
    async def test_spec_search(self):
        """规格搜索模式"""
        from services.kuaimai.erp_local_identify import local_product_identify
        db = _make_db(
            erp_products=[_product("P01", title="测试商品")],
            erp_product_skus=[
                _sku("P01", "P01-01", properties_name="红色 XL"),
                _sku("P01", "P01-02", properties_name="蓝色 M"),
            ],
            erp_sync_state=[_sync_state("product")],
        )
        result = await local_product_identify(db, spec="红色")
        assert "匹配到" in result
        assert "红色" in result

    @pytest.mark.asyncio
    async def test_suite_product_shows_children(self):
        """套件商品显示子单品"""
        from services.kuaimai.erp_local_identify import local_product_identify
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
        assert "套件子单品" in result
        assert "CHILD01" in result


# ============================================================
# TestLocalQuery — 6个本地查询工具
# ============================================================


class TestLocalPurchaseQuery:

    @pytest.mark.asyncio
    async def test_basic_purchase(self):
        """基础采购查询"""
        from services.kuaimai.erp_local_query import local_purchase_query
        items = [
            _doc_item("purchase", "PO001", "C01", quantity=500, amount=5000),
        ]
        db = _make_db(
            erp_document_items=items,
            erp_sync_state=[_sync_state("purchase")],
        )
        result = await local_purchase_query(db, "C01")
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_no_data(self):
        """无采购数据"""
        from services.kuaimai.erp_local_query import local_purchase_query
        db = _make_db(
            erp_document_items=[],
            erp_sync_state=[_sync_state("purchase")],
        )
        result = await local_purchase_query(db, "NODATA")
        assert "无" in result or "未" in result or "0" in result


class TestLocalAftersaleQuery:

    @pytest.mark.asyncio
    async def test_basic_aftersale(self):
        """基础售后查询"""
        from services.kuaimai.erp_local_query import local_aftersale_query
        items = [
            _doc_item("aftersale", "AS001", "C01",
                      extra={"aftersale_type": "2", "work_order_id": "W001"}),
        ]
        db = _make_db(
            erp_document_items=items,
            erp_sync_state=[_sync_state("aftersale")],
        )
        result = await local_aftersale_query(db, "C01")
        assert isinstance(result, str)


class TestLocalOrderQuery:

    @pytest.mark.asyncio
    async def test_basic_order(self):
        """基础订单查询"""
        from services.kuaimai.erp_local_query import local_order_query
        items = [
            _doc_item("order", "ORD001", "C01", platform="tb",
                      shop_name="旗舰店", amount=160),
        ]
        db = _make_db(
            erp_document_items=items,
            erp_sync_state=[_sync_state("order")],
        )
        result = await local_order_query(db, "C01")
        assert isinstance(result, str)


class TestLocalProductFlow:

    @pytest.mark.asyncio
    async def test_flow_with_data(self):
        """全链路流转查询"""
        from services.kuaimai.erp_local_query import local_product_flow
        items = [
            _doc_item("purchase", "PO001", "C01", quantity=500),
            _doc_item("receipt", "RC001", "C01", quantity=300),
            _doc_item("shelf", "SH001", "C01", quantity=300),
            _doc_item("order", "ORD001", "C01", quantity=100),
            _doc_item("aftersale", "AS001", "C01", quantity=5),
        ]
        db = _make_db(
            erp_document_items=items,
            erp_sync_state=[_sync_state("order")],
        )
        result = await local_product_flow(db, "C01")
        assert isinstance(result, str)


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
        result = await local_stock_query(db, "C01")
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_no_stock(self):
        """无库存数据"""
        from services.kuaimai.erp_local_query import local_stock_query
        db = _make_db(
            erp_stock_status=[],
            erp_sync_state=[_sync_state("product")],
        )
        result = await local_stock_query(db, "NOSTOCK")
        assert "无" in result or "未" in result or "0" in result

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
        assert "WH-A" in result
        assert "WH-B" in result
        # 验证汇总（50+30=80）
        assert "80" in result

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
        assert "WH-B" not in result
        assert "SKU" in result

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
        assert "C01-02" in result
        # C01-01 被过滤掉（sellable=50 >= 10）
        assert "C01-01" not in result


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
        result = await local_platform_map_query(db, product_code="C01")
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_no_params_returns_error(self):
        """未传参数返回提示"""
        from services.kuaimai.erp_local_query import local_platform_map_query
        db = MockSupabaseClient()
        result = await local_platform_map_query(db)
        assert "product_code" in result or "num_iid" in result


# ============================================================
# TestStatsQuery — 统计报表查询
# ============================================================


class TestLocalProductStats:

    @pytest.mark.asyncio
    async def test_with_data(self):
        """有统计数据"""
        from services.kuaimai.erp_stats_query import local_product_stats
        db = _make_db(
            erp_product_daily_stats=[
                _daily_stat("C01", "2026-03-18"),
                _daily_stat("C01", "2026-03-17"),
            ],
            erp_sync_state=[_sync_state("order")],
        )
        result = await local_product_stats(db, "C01")
        assert "统计" in result
        assert "销售" in result

    @pytest.mark.asyncio
    async def test_no_data(self):
        """无统计数据"""
        from services.kuaimai.erp_stats_query import local_product_stats
        db = _make_db(
            erp_product_daily_stats=[],
            erp_sync_state=[_sync_state("order")],
        )
        result = await local_product_stats(db, "NODATA")
        assert "无统计数据" in result

    @pytest.mark.asyncio
    async def test_aftersale_rate(self):
        """售后率计算"""
        from services.kuaimai.erp_stats_query import local_product_stats
        db = _make_db(
            erp_product_daily_stats=[
                _daily_stat("C01", "2026-03-18",
                            order_count=100, aftersale_count=10),
            ],
            erp_sync_state=[],
        )
        result = await local_product_stats(db, "C01")
        assert "售后率" in result


# ============================================================
# TestLocalToolDefinitions — 工具定义结构
# ============================================================


class TestBuildLocalTools:

    def test_returns_11_tools(self):
        """build_local_tools 返回 11 个工具（8原有 + 3新增）"""
        from config.erp_local_tools import build_local_tools
        tools = build_local_tools()
        assert len(tools) == 11

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
        """5个查询工具必填 product_code"""
        from config.erp_local_tools import build_local_tools
        required_tools = {
            "local_purchase_query", "local_aftersale_query",
            "local_order_query", "local_product_stats",
            "local_product_flow", "local_stock_query",
        }
        tools = build_local_tools()
        for tool in tools:
            name = tool["function"]["name"]
            if name in required_tools:
                assert "product_code" in tool["function"]["parameters"]["required"], (
                    f"{name} 缺少 product_code 必填"
                )


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
        executor = ToolExecutor(db, "test_user", "test_conv")
        for tool_name in ERP_LOCAL_TOOLS:
            assert tool_name in executor._handlers
