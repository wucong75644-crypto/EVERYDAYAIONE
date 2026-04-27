"""erp_analytics_cross.py + cross_composite.py 单元测试。"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_backend_dir = Path(__file__).parent.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

from services.kuaimai.erp_unified_schema import TimeRange


def _tr(start="2026-04-01", end="2026-04-28"):
    return TimeRange(
        start_iso=start, end_iso=end, time_col="stat_date",
        label=f"{start} ~ {end}", date_range=f"{start} ~ {end}",
    )


def _mock_db(rpc_data=None, table_data=None):
    db = MagicMock()
    table_data = table_data or {}
    rpc_data = rpc_data or {}

    def _chain(rows):
        resp = MagicMock(); resp.data = rows; resp.count = len(rows)
        q = MagicMock()
        for m in ("eq","is_","gte","gt","lt","lte","neq","ilike","in_","order","limit","select"):
            setattr(q, m, MagicMock(return_value=q))
        q.not_ = MagicMock(); q.not_.in_ = MagicMock(return_value=q)
        q.execute = MagicMock(return_value=resp)
        return q

    db.table = MagicMock(side_effect=lambda t: _chain(table_data.get(t, [])))
    def _rpc(name, params=None):
        r = MagicMock(); r.data = rpc_data.get(name, [])
        return MagicMock(execute=MagicMock(return_value=r))
    db.rpc = MagicMock(side_effect=_rpc)
    return db


# ── query_cross 分发 ──


class TestQueryCrossDispatch:

    @pytest.mark.asyncio
    async def test_ds_metric_return_rate(self):
        from services.kuaimai.erp_analytics_cross import query_cross
        db = _mock_db(rpc_data={
            "erp_cross_metric_query": [
                {"group_key": "tb", "metric_value": 3.2, "numerator": 32, "denominator": 1000},
            ],
        })
        result = await query_cross(db, "org-1", [], _tr(), metrics=["return_rate"])
        assert result.status in ("ok", "success")
        assert result.data is not None

    @pytest.mark.asyncio
    async def test_unknown_metric_error(self):
        from services.kuaimai.erp_analytics_cross import query_cross
        db = _mock_db()
        result = await query_cross(db, "org-1", [], _tr(), metrics=["nonexistent"])
        assert "error" in str(result.status).lower()

    @pytest.mark.asyncio
    async def test_empty_metrics_error(self):
        from services.kuaimai.erp_analytics_cross import query_cross
        db = _mock_db()
        result = await query_cross(db, "org-1", [], _tr(), metrics=[])
        assert "error" in str(result.status).lower()

    @pytest.mark.asyncio
    async def test_repurchase_dispatches(self):
        from services.kuaimai.erp_analytics_cross import query_cross
        db = _mock_db(rpc_data={"erp_cross_metric_query": []})
        result = await query_cross(db, "org-1", [], _tr(), metrics=["repurchase_rate"])
        # 即使无数据也不应 crash
        assert result is not None

    @pytest.mark.asyncio
    async def test_ship_time_dispatches(self):
        from services.kuaimai.erp_analytics_cross import query_cross
        db = _mock_db(rpc_data={"erp_cross_metric_query": []})
        result = await query_cross(db, "org-1", [], _tr(), metrics=["avg_ship_time"])
        assert result is not None


# ── composite metrics ──


class TestCompositeMetrics:

    @pytest.mark.asyncio
    async def test_inventory_turnover(self):
        from services.kuaimai.erp_analytics_cross import query_cross
        db = _mock_db(table_data={
            "erp_stock_status": [
                {"outer_id": "P001", "item_name": "A", "available_stock": 100,
                 "total_stock": 100, "sellable_num": 100},
            ],
            "erp_product_daily_stats": [
                {"outer_id": "P001", "order_qty": 30},
            ],
        })
        result = await query_cross(db, "org-1", [], _tr(), metrics=["inventory_turnover"])
        assert result is not None

    @pytest.mark.asyncio
    async def test_sell_through_rate(self):
        from services.kuaimai.erp_analytics_cross import query_cross
        db = _mock_db(table_data={
            "erp_product_daily_stats": [{"outer_id": "P001", "order_qty": 10}],
            "erp_products": [{"outer_id": "P001"}, {"outer_id": "P002"}],
        })
        result = await query_cross(db, "org-1", [], _tr(), metrics=["sell_through_rate"])
        assert result is not None

    @pytest.mark.asyncio
    async def test_inventory_flow(self):
        from services.kuaimai.erp_analytics_cross import query_cross
        db = _mock_db(table_data={
            "erp_stock_status": [
                {"outer_id": "P001", "item_name": "A", "available_stock": 50,
                 "total_stock": 50, "sellable_num": 50},
            ],
            "erp_product_daily_stats": [
                {"outer_id": "P001", "order_qty": 10, "purchase_qty": 20,
                 "purchase_received_qty": 18, "aftersale_return_count": 2},
            ],
        })
        result = await query_cross(db, "org-1", [], _tr(), metrics=["inventory_flow"])
        assert result is not None

    @pytest.mark.asyncio
    async def test_supplier_evaluation(self):
        from services.kuaimai.erp_analytics_cross import query_cross
        db = _mock_db(rpc_data={
            "erp_global_stats_query": [
                {"group_key": "供应商A", "doc_count": 10, "total_qty": 100, "total_amount": 5000},
            ],
        })
        result = await query_cross(db, "org-1", [], _tr(), metrics=["supplier_evaluation"])
        assert result is not None


# ── 辅助函数 ──


class TestCrossHelpers:

    def test_to_date_str(self):
        from services.kuaimai.erp_analytics_cross import to_date_str
        assert to_date_str("2026-04-01T00:00:00+08:00") == "2026-04-01"
        assert to_date_str("2026-04-01") == "2026-04-01"

    def test_translate_platform_keys(self):
        from services.kuaimai.erp_analytics_cross import translate_platform_keys
        rows = [{"group_key": "tb"}, {"group_key": "unknown"}]
        translate_platform_keys(rows)
        assert rows[0]["group_key"] == "淘宝"
        assert rows[1]["group_key"] == "unknown"
