"""erp_analytics_trend.py 单元测试——趋势+对比分析内部逻辑。"""
import sys
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_backend_dir = Path(__file__).parent.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))


# ── shift_time_range ──


class TestShiftTimeRange:

    def _shift(self, start, end, cr):
        from services.kuaimai.erp_analytics_trend import shift_time_range
        return shift_time_range(start, end, cr)

    def test_mom(self):
        s, e = self._shift(date(2026, 4, 1), date(2026, 4, 30), "mom")
        assert s == date(2026, 3, 1)
        assert e == date(2026, 3, 30)

    def test_yoy(self):
        s, e = self._shift(date(2026, 4, 1), date(2026, 4, 30), "yoy")
        assert s == date(2025, 4, 1)
        assert e == date(2025, 4, 30)

    def test_wow(self):
        s, e = self._shift(date(2026, 4, 21), date(2026, 4, 28), "wow")
        assert s == date(2026, 4, 14)
        assert e == date(2026, 4, 21)

    def test_mom_month_end_31_to_30(self):
        """3月31日 → 2月28日（月末安全降级）。"""
        s, _ = self._shift(date(2026, 3, 31), date(2026, 3, 31), "mom")
        assert s.month == 2
        assert s.day == 28

    def test_yoy_leap_day(self):
        """闰年 2-29 → 非闰年 2-28。"""
        s, _ = self._shift(date(2024, 2, 29), date(2024, 2, 29), "yoy")
        assert s == date(2023, 2, 28)

    def test_unknown_raises(self):
        with pytest.raises(ValueError):
            self._shift(date(2026, 4, 1), date(2026, 4, 30), "invalid")


# ── _auto_adjust_granularity ──


class TestAutoAdjustGranularity:

    def _adjust(self, g, start, end):
        from services.kuaimai.erp_analytics_trend import _auto_adjust_granularity
        return _auto_adjust_granularity(g, start, end)

    def test_day_over_365_becomes_month(self):
        assert self._adjust("day", "2025-01-01", "2026-04-01") == "month"

    def test_week_under_7_becomes_day(self):
        assert self._adjust("week", "2026-04-01", "2026-04-05") == "day"

    def test_day_normal_stays(self):
        assert self._adjust("day", "2026-04-01", "2026-04-30") == "day"

    def test_month_stays(self):
        assert self._adjust("month", "2026-01-01", "2026-04-01") == "month"

    def test_invalid_defaults_to_day(self):
        assert self._adjust("year", "2026-04-01", "2026-04-30") == "day"


# ── _fill_zero_periods ──


class TestFillZeroPeriods:

    def _fill(self, rows, start, end, g="day", metrics=None):
        from services.kuaimai.erp_analytics_trend import _fill_zero_periods
        return _fill_zero_periods(rows, start, end, g, metrics or ["order_count"])

    def test_fills_missing_day(self):
        rows = [{"period": "2026-04-01", "order_count": 10}]
        result = self._fill(rows, "2026-04-01", "2026-04-04")
        assert len(result) == 3  # 04-01, 04-02, 04-03
        dates = [r["period"] for r in result]
        assert "2026-04-02" in dates
        assert result[1]["order_count"] == 0  # 补零

    def test_no_fill_when_complete(self):
        rows = [
            {"period": "2026-04-01", "order_count": 10},
            {"period": "2026-04-02", "order_count": 20},
        ]
        result = self._fill(rows, "2026-04-01", "2026-04-03")
        assert len(result) == 2


# ── _sanitize_metrics ──


class TestSanitizeMetrics:

    def _sanitize(self, metrics):
        from services.kuaimai.erp_analytics_trend import _sanitize_metrics
        return _sanitize_metrics(metrics)

    def test_none_returns_defaults(self):
        result = self._sanitize(None)
        assert "order_count" in result
        assert "order_amount" in result

    def test_valid_metrics_passthrough(self):
        result = self._sanitize(["order_amount", "order_qty"])
        assert result == ["order_amount", "order_qty"]

    def test_invalid_metrics_filtered(self):
        result = self._sanitize(["order_amount", "hacker_field"])
        assert result == ["order_amount"]

    def test_all_invalid_returns_defaults(self):
        result = self._sanitize(["fake1", "fake2"])
        assert "order_count" in result


# ── compute_comparison ──


class TestComputeComparison:

    def _compare(self, cur, prev, group_by=None):
        from services.kuaimai.erp_analytics_trend import compute_comparison
        return compute_comparison(cur, prev, group_by)

    def test_simple_no_group(self):
        cur = {"doc_count": 100, "total_qty": 500, "total_amount": 50000}
        prev = {"doc_count": 80, "total_qty": 400, "total_amount": 40000}
        result = self._compare(cur, prev)
        assert len(result) == 1
        r = result[0]
        assert r["current_doc_count"] == 100
        assert r["prev_doc_count"] == 80
        assert r["doc_count_change"] == 20
        assert r["doc_count_growth"] == "+25.0%"

    def test_prev_zero_growth_infinity(self):
        cur = {"doc_count": 10, "total_qty": 0, "total_amount": 0}
        prev = {"doc_count": 0, "total_qty": 0, "total_amount": 0}
        result = self._compare(cur, prev)
        assert result[0]["doc_count_growth"] == "+∞%"

    def test_both_zero_returns_none(self):
        cur = {"doc_count": 0, "total_qty": 0, "total_amount": 0}
        prev = {"doc_count": 0, "total_qty": 0, "total_amount": 0}
        result = self._compare(cur, prev)
        assert len(result) == 0

    def test_grouped_comparison(self):
        cur = [
            {"group_key": "tb", "doc_count": 100, "total_qty": 500, "total_amount": 50000},
            {"group_key": "jd", "doc_count": 30, "total_qty": 100, "total_amount": 15000},
        ]
        prev = [
            {"group_key": "tb", "doc_count": 80, "total_qty": 400, "total_amount": 42000},
        ]
        result = self._compare(cur, prev, group_by="platform")
        assert len(result) == 2
        tb = next(r for r in result if r["group_key"] == "tb")
        assert tb["total_amount_growth"] == "+19.0%"

    def test_negative_growth(self):
        cur = {"doc_count": 50, "total_qty": 200, "total_amount": 20000}
        prev = {"doc_count": 100, "total_qty": 400, "total_amount": 40000}
        result = self._compare(cur, prev)
        assert "-50.0%" in result[0]["doc_count_growth"]


# ── query_trend ──


class TestQueryTrend:

    @pytest.mark.asyncio
    async def test_success(self):
        from services.kuaimai.erp_analytics_trend import query_trend
        db = MagicMock()
        rpc_resp = MagicMock()
        rpc_resp.data = [
            {"period": "2026-04-01", "order_count": 10, "order_amount": 5000},
            {"period": "2026-04-02", "order_count": 15, "order_amount": 7500},
        ]
        db.rpc = MagicMock(return_value=MagicMock(execute=MagicMock(return_value=rpc_resp)))

        result = await query_trend(
            db, "org-1", "2026-04-01", "2026-04-03",
            time_granularity="day", metrics=["order_amount"],
        )
        assert result.status == "ok" or result.status == "success"
        assert result.data is not None
        assert result.metadata["query_type"] == "trend"

    @pytest.mark.asyncio
    async def test_empty_returns_empty_status(self):
        from services.kuaimai.erp_analytics_trend import query_trend
        db = MagicMock()
        rpc_resp = MagicMock()
        rpc_resp.data = []
        db.rpc = MagicMock(return_value=MagicMock(execute=MagicMock(return_value=rpc_resp)))

        result = await query_trend(db, "org-1", "2026-04-01", "2026-04-03")
        assert str(result.status) in ("empty", "OutputStatus.EMPTY")

    @pytest.mark.asyncio
    async def test_rpc_error(self):
        from services.kuaimai.erp_analytics_trend import query_trend
        db = MagicMock()
        rpc_resp = MagicMock()
        rpc_resp.data = {"error": "invalid parameter"}
        db.rpc = MagicMock(return_value=MagicMock(execute=MagicMock(return_value=rpc_resp)))

        result = await query_trend(db, "org-1", "2026-04-01", "2026-04-03")
        assert "error" in str(result.status).lower() or "参数" in result.summary
