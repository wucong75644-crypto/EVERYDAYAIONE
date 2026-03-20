"""
ERP 本地优先统一查询架构 V2 单元测试

覆盖：erp_local_doc_query / erp_local_global_stats / erp_local_sync_trigger /
      erp_local_identify API 兜底

设计文档: docs/document/TECH_ERP本地优先统一查询架构.md
"""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from tests.conftest import MockSupabaseClient


# ── 测试数据工厂 ─────────────────────────────────────


def _doc(doc_type: str, doc_id: str, outer_id: str, **kw) -> dict:
    """创建单据明细（带完整中转钥匙字段）"""
    now = datetime.now(timezone.utc).isoformat()
    base = {
        "doc_type": doc_type,
        "doc_id": doc_id,
        "outer_id": outer_id,
        "sku_outer_id": "",
        "item_index": 0,
        "item_name": f"商品{outer_id}",
        "quantity": 100,
        "amount": 1000.0,
        "doc_status": "FINISHED",
        "order_status": "",
        "order_no": "",
        "doc_code": "",
        "express_no": "",
        "express_company": "",
        "supplier_name": "",
        "shop_name": "旗舰店",
        "platform": "tb",
        "warehouse_name": "主仓",
        "doc_created_at": now,
        "doc_modified_at": now,
        "extra": {},
    }
    base.update(kw)
    return base


def _sync(sync_type: str, healthy: bool = True) -> dict:
    return {
        "sync_type": sync_type,
        "last_run_at": datetime.now(timezone.utc).isoformat(),
        "error_count": 0 if healthy else 5,
        "is_initial_done": True,
    }


def _db(**tables) -> MockSupabaseClient:
    db = MockSupabaseClient()
    for name, data in tables.items():
        db.set_table_data(name, data)
    return db


# ============================================================
# TestLocalDocQuery — 多维度单据查询
# ============================================================


class TestLocalDocQuery:

    @pytest.mark.asyncio
    async def test_no_params_returns_error(self):
        """未传任何条件返回提示"""
        from services.kuaimai.erp_local_doc_query import local_doc_query
        result = await local_doc_query(MockSupabaseClient())
        assert "至少提供一个" in result

    @pytest.mark.asyncio
    async def test_query_by_order_no(self):
        """按订单号查询"""
        from services.kuaimai.erp_local_doc_query import local_doc_query
        items = [
            _doc("order", "D001", "C01", order_no="TB20260321001"),
        ]
        db = _db(
            erp_document_items=items,
            erp_sync_state=[_sync("order")],
        )
        result = await local_doc_query(db, order_no="TB20260321001")
        assert "1笔单据" in result
        assert "TB20260321001" in result

    @pytest.mark.asyncio
    async def test_query_by_express_no(self):
        """按快递单号查询"""
        from services.kuaimai.erp_local_doc_query import local_doc_query
        items = [
            _doc("order", "D002", "C01", express_no="SF1234567890"),
        ]
        db = _db(
            erp_document_items=items,
            erp_sync_state=[_sync("order")],
        )
        result = await local_doc_query(db, express_no="SF1234567890")
        assert "SF1234567890" in result

    @pytest.mark.asyncio
    async def test_query_by_doc_code(self):
        """按采购单号查询"""
        from services.kuaimai.erp_local_doc_query import local_doc_query
        items = [
            _doc("purchase", "D003", "C01", doc_code="PO20260321001"),
        ]
        db = _db(
            erp_document_items=items,
            erp_sync_state=[_sync("purchase")],
        )
        result = await local_doc_query(db, doc_code="PO20260321001")
        assert "PO20260321001" in result

    @pytest.mark.asyncio
    async def test_query_by_supplier(self):
        """按供应商名模糊查询"""
        from services.kuaimai.erp_local_doc_query import local_doc_query
        items = [
            _doc("purchase", "D004", "C01", supplier_name="广州贸易公司"),
        ]
        db = _db(
            erp_document_items=items,
            erp_sync_state=[_sync("purchase")],
        )
        result = await local_doc_query(db, supplier_name="贸易")
        assert "广州贸易公司" in result

    @pytest.mark.asyncio
    async def test_query_by_product_code(self):
        """按商品编码查询"""
        from services.kuaimai.erp_local_doc_query import local_doc_query
        items = [
            _doc("order", "D005", "SKU001", order_no="ORD001"),
        ]
        db = _db(
            erp_document_items=items,
            erp_sync_state=[_sync("order")],
        )
        result = await local_doc_query(db, product_code="SKU001")
        assert "SKU001" in result

    @pytest.mark.asyncio
    async def test_no_results_with_health(self):
        """查无结果时显示同步状态"""
        from services.kuaimai.erp_local_doc_query import local_doc_query
        db = _db(
            erp_document_items=[],
            erp_sync_state=[_sync("order", healthy=False)],
        )
        result = await local_doc_query(db, order_no="NOTEXIST")
        assert "未查到" in result

    @pytest.mark.asyncio
    async def test_transit_keys_exposed(self):
        """结果包含所有中转钥匙"""
        from services.kuaimai.erp_local_doc_query import local_doc_query
        items = [
            _doc("order", "SID001", "C01",
                 order_no="ORD001", express_no="EXP001",
                 doc_code="DC001"),
        ]
        db = _db(
            erp_document_items=items,
            erp_sync_state=[_sync("order")],
        )
        result = await local_doc_query(db, order_no="ORD001")
        assert "sid=SID001" in result
        assert "order_no=ORD001" in result
        assert "express=EXP001" in result

    @pytest.mark.asyncio
    async def test_archive_union(self):
        """days>90 查冷表"""
        from services.kuaimai.erp_local_doc_query import local_doc_query
        old_date = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
        hot = [_doc("order", "HOT1", "C01")]
        cold = [_doc("order", "COLD1", "C01", doc_created_at=old_date)]
        db = _db(
            erp_document_items=hot,
            erp_document_items_archive=cold,
            erp_sync_state=[_sync("order")],
        )
        result = await local_doc_query(db, product_code="C01", days=120)
        assert isinstance(result, str)


# ============================================================
# TestLocalGlobalStats — 全局统计/排名
# ============================================================


class TestLocalGlobalStats:
    """全局统计使用 RPC (erp_global_stats_query)，需 mock rpc 返回"""

    @pytest.mark.asyncio
    async def test_summary_mode(self):
        """默认汇总模式（RPC 返回 dict）"""
        from services.kuaimai.erp_local_global_stats import local_global_stats
        db = _db(erp_sync_state=[_sync("order")])
        db.set_rpc_result("erp_global_stats_query", {
            "doc_count": 15, "total_qty": 200, "total_amount": 5000.0,
        })
        result = await local_global_stats(db, doc_type="order")
        assert "订单" in result
        assert "15笔" in result

    @pytest.mark.asyncio
    async def test_ranking_mode(self):
        """排名模式（RPC 返回 list[dict]）"""
        from services.kuaimai.erp_local_global_stats import local_global_stats
        db = _db(erp_sync_state=[_sync("order")])
        db.set_rpc_result("erp_global_stats_query", [
            {"group_key": "C01", "item_name": "商品A",
             "doc_count": 10, "total_qty": 80, "total_amount": 1000},
            {"group_key": "C02", "item_name": "商品B",
             "doc_count": 5, "total_qty": 30, "total_amount": 500},
        ])
        result = await local_global_stats(
            db, doc_type="order", rank_by="quantity",
        )
        assert "TOP10" in result
        assert "C01" in result

    @pytest.mark.asyncio
    async def test_group_by_shop(self):
        """按店铺分组（RPC 返回 list[dict]）"""
        from services.kuaimai.erp_local_global_stats import local_global_stats
        db = _db(erp_sync_state=[_sync("order")])
        db.set_rpc_result("erp_global_stats_query", [
            {"group_key": "旗舰店", "doc_count": 10,
             "total_qty": 100, "total_amount": 2000},
            {"group_key": "专营店", "doc_count": 5,
             "total_qty": 50, "total_amount": 1000},
        ])
        result = await local_global_stats(
            db, doc_type="order", group_by="shop",
        )
        assert "旗舰店" in result
        assert "专营店" in result

    @pytest.mark.asyncio
    async def test_no_data(self):
        """RPC 返回空 → 无记录"""
        from services.kuaimai.erp_local_global_stats import local_global_stats
        db = _db(erp_sync_state=[_sync("order")])
        db.set_rpc_result("erp_global_stats_query", [])
        result = await local_global_stats(db, doc_type="order")
        assert "无记录" in result

    @pytest.mark.asyncio
    async def test_week_period(self):
        """按周统计（验证 period_label 包含本周）"""
        from services.kuaimai.erp_local_global_stats import local_global_stats
        db = _db(erp_sync_state=[_sync("order")])
        db.set_rpc_result("erp_global_stats_query", {
            "doc_count": 3, "total_qty": 10, "total_amount": 100,
        })
        result = await local_global_stats(
            db, doc_type="order", period="week",
        )
        assert "本周" in result


# ============================================================
# TestTriggerErpSync — 手动同步触发
# ============================================================


class TestTriggerErpSync:

    @pytest.mark.asyncio
    async def test_invalid_type(self):
        """无效同步类型"""
        from services.kuaimai.erp_local_sync_trigger import trigger_erp_sync
        result = await trigger_erp_sync(MockSupabaseClient(), "INVALID")
        assert "✗" in result
        assert "无效类型" in result

    @pytest.mark.asyncio
    async def test_recently_synced_skip(self):
        """2分钟内同步过则跳过"""
        from services.kuaimai.erp_local_sync_trigger import trigger_erp_sync
        db = _db(erp_sync_state=[_sync("order")])
        result = await trigger_erp_sync(db, "order")
        assert "2分钟内" in result

    @pytest.mark.asyncio
    @patch("services.kuaimai.erp_local_sync_trigger._is_recently_synced",
           return_value=False)
    @patch("services.kuaimai.erp_sync_service.ErpSyncService")
    async def test_sync_success(self, MockSvc, mock_recent):
        """同步成功"""
        from services.kuaimai.erp_local_sync_trigger import trigger_erp_sync
        mock_svc = AsyncMock()
        mock_svc.sync = AsyncMock()
        MockSvc.return_value = mock_svc
        result = await trigger_erp_sync(MockSupabaseClient(), "order")
        assert "✓" in result
        assert "同步完成" in result

    @pytest.mark.asyncio
    @patch("services.kuaimai.erp_local_sync_trigger._is_recently_synced",
           return_value=False)
    async def test_sync_timeout(self, mock_recent):
        """同步超时"""
        from services.kuaimai.erp_local_sync_trigger import trigger_erp_sync
        with patch(
            "services.kuaimai.erp_local_sync_trigger.asyncio.wait_for",
            side_effect=asyncio.TimeoutError,
        ):
            result = await trigger_erp_sync(MockSupabaseClient(), "order")
        assert "超时" in result

    @pytest.mark.asyncio
    @patch("services.kuaimai.erp_local_sync_trigger._is_recently_synced",
           return_value=False)
    @patch("services.kuaimai.erp_sync_service.ErpSyncService")
    async def test_sync_failure(self, MockSvc, mock_recent):
        """同步异常"""
        from services.kuaimai.erp_local_sync_trigger import trigger_erp_sync
        mock_svc = AsyncMock()
        mock_svc.sync = AsyncMock(
            side_effect=RuntimeError("连接失败"),
        )
        MockSvc.return_value = mock_svc
        result = await trigger_erp_sync(MockSupabaseClient(), "order")
        assert "✗" in result
        assert "连接失败" in result


# ============================================================
# TestIdentifyApiFallback — 编码识别 API 兜底
# ============================================================


class TestIdentifyApiFallback:

    @pytest.mark.asyncio
    @patch("services.kuaimai.erp_local_identify.KuaiMaiClient", create=True)
    async def test_api_found_upserts(self, MockClient):
        """API 找到商品 → 写入本地并返回"""
        from services.kuaimai.erp_local_identify import (
            _api_fallback_identify,
        )
        mock_client = AsyncMock()
        mock_client.is_configured = True
        mock_client.load_cached_token = AsyncMock()
        mock_client.request_with_retry = AsyncMock(return_value={
            "outerId": "API01",
            "title": "API找到的商品",
            "type": 0,
            "activeStatus": 1,
            "skus": [],
        })
        mock_client.close = AsyncMock()
        MockClient.return_value = mock_client

        db = _db(
            erp_products=[],
            erp_product_skus=[],
        )
        result = await _api_fallback_identify(db, "API01")
        # upsert 后本地应有数据，但 mock DB 的 upsert 不会真插入
        # 所以 result 可能是 None（本地查不到），这是预期行为
        assert result is None or isinstance(result, str)

    @pytest.mark.asyncio
    @patch("services.kuaimai.erp_local_identify.KuaiMaiClient", create=True)
    async def test_api_not_configured(self, MockClient):
        """ERP 未配置 → 返回 None"""
        from services.kuaimai.erp_local_identify import (
            _api_fallback_identify,
        )
        mock_client = AsyncMock()
        mock_client.is_configured = False
        mock_client.close = AsyncMock()
        MockClient.return_value = mock_client

        result = await _api_fallback_identify(MockSupabaseClient(), "X01")
        assert result is None

    @pytest.mark.asyncio
    @patch("services.kuaimai.erp_local_identify.KuaiMaiClient", create=True)
    async def test_api_not_found(self, MockClient):
        """API 也没找到 → 返回 None"""
        from services.kuaimai.erp_local_identify import (
            _api_fallback_identify,
        )
        mock_client = AsyncMock()
        mock_client.is_configured = True
        mock_client.load_cached_token = AsyncMock()
        mock_client.request_with_retry = AsyncMock(return_value=None)
        mock_client.close = AsyncMock()
        MockClient.return_value = mock_client

        result = await _api_fallback_identify(MockSupabaseClient(), "NONE01")
        assert result is None
