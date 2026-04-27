"""
趋势分析 + 对比分析单元测试

覆盖: erp_analytics_trend.py
设计文档: docs/document/TECH_ERP查询架构重构.md §5.3, §5.4
"""

import asyncio
import sys
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

_tests_dir = Path(__file__).parent
if str(_tests_dir) not in sys.path:
    sys.path.insert(0, str(_tests_dir))
_backend_dir = _tests_dir.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))


from services.agent.tool_output import OutputFormat, OutputStatus
from services.kuaimai.erp_analytics_trend import (
    _auto_adjust_granularity,
    _fill_zero_periods,
    _generate_periods,
    _parse_date,
    _sanitize_metrics,
    _to_float,
    compute_comparison,
    format_compare_summary,
    format_trend_summary,
    query_compare,
    query_trend,
    shift_time_range,
)


# ══════════════════════════════════════════════════════════
# shift_time_range
# ══════════════════════════════════════════════════════════


class TestShiftTimeRange:

    def test_mom_basic(self):
        s, e = shift_time_range(date(2026, 4, 1), date(2026, 5, 1), "mom")
        assert s == date(2026, 3, 1)
        assert e == date(2026, 4, 1)

    def test_yoy_basic(self):
        s, e = shift_time_range(date(2026, 4, 1), date(2026, 5, 1), "yoy")
        assert s == date(2025, 4, 1)
        assert e == date(2025, 5, 1)

    def test_wow_basic(self):
        s, e = shift_time_range(date(2026, 4, 14), date(2026, 4, 21), "wow")
        assert s == date(2026, 4, 7)
        assert e == date(2026, 4, 14)

    def test_mom_jan_to_dec(self):
        """1月环比→12月（跨年）"""
        s, e = shift_time_range(date(2026, 1, 1), date(2026, 2, 1), "mom")
        assert s == date(2025, 12, 1)
        assert e == date(2026, 1, 1)

    def test_mom_march31_to_feb(self):
        """3月31日环比→2月28日（月末降级）"""
        s, e = shift_time_range(date(2026, 3, 31), date(2026, 4, 30), "mom")
        assert s == date(2026, 2, 28)  # 非闰年
        assert e == date(2026, 3, 30)

    def test_yoy_leap_year(self):
        """闰年 2月29日同比→非闰年 2月28日"""
        s, e = shift_time_range(date(2024, 2, 29), date(2024, 3, 1), "yoy")
        assert s == date(2023, 2, 28)  # 降级
        assert e == date(2023, 3, 1)

    def test_unknown_compare_range(self):
        with pytest.raises(ValueError, match="未知 compare_range"):
            shift_time_range(date(2026, 4, 1), date(2026, 5, 1), "xyz")


# ══════════════════════════════════════════════════════════
# _auto_adjust_granularity
# ══════════════════════════════════════════════════════════


class TestAutoAdjustGranularity:

    def test_day_over_1_year_becomes_month(self):
        g = _auto_adjust_granularity("day", "2025-01-01", "2026-06-01")
        assert g == "month"

    def test_day_within_1_year_stays(self):
        g = _auto_adjust_granularity("day", "2026-01-01", "2026-12-01")
        assert g == "day"

    def test_week_under_7_days_becomes_day(self):
        g = _auto_adjust_granularity("week", "2026-04-25", "2026-04-27")
        assert g == "day"

    def test_week_over_7_days_stays(self):
        g = _auto_adjust_granularity("week", "2026-04-01", "2026-04-30")
        assert g == "week"

    def test_month_stays(self):
        g = _auto_adjust_granularity("month", "2025-01-01", "2026-12-31")
        assert g == "month"

    def test_invalid_granularity_defaults_to_day(self):
        g = _auto_adjust_granularity("quarter", "2026-04-01", "2026-04-30")
        assert g == "day"

    def test_invalid_dates_returns_original(self):
        g = _auto_adjust_granularity("day", "not-a-date", "2026-04-30")
        assert g == "day"


# ══════════════════════════════════════════════════════════
# _sanitize_metrics
# ══════════════════════════════════════════════════════════


class TestSanitizeMetrics:

    def test_none_returns_defaults(self):
        m = _sanitize_metrics(None)
        assert m == ["order_count", "order_amount"]

    def test_empty_returns_defaults(self):
        m = _sanitize_metrics([])
        assert m == ["order_count", "order_amount"]

    def test_valid_metrics_pass(self):
        m = _sanitize_metrics(["order_count", "purchase_amount"])
        assert m == ["order_count", "purchase_amount"]

    def test_invalid_filtered_out(self):
        m = _sanitize_metrics(["order_count", "INVALID", "order_qty"])
        assert m == ["order_count", "order_qty"]

    def test_all_invalid_returns_defaults(self):
        m = _sanitize_metrics(["bad1", "bad2"])
        assert m == ["order_count", "order_amount"]


# ══════════════════════════════════════════════════════════
# _fill_zero_periods
# ══════════════════════════════════════════════════════════


class TestFillZeroPeriods:

    def test_day_fills_gaps(self):
        rows = [{"period": "2026-04-01", "order_count": 10}]
        filled = _fill_zero_periods(
            rows, "2026-04-01", "2026-04-04", "day", ["order_count"],
        )
        assert len(filled) == 3
        periods = [r["period"] for r in filled]
        assert "2026-04-01" in periods
        assert "2026-04-02" in periods
        assert "2026-04-03" in periods
        # 原数据保留
        orig = next(r for r in filled if r["period"] == "2026-04-01")
        assert orig["order_count"] == 10
        # 补零
        gap = next(r for r in filled if r["period"] == "2026-04-02")
        assert gap["order_count"] == 0

    def test_all_present_no_change(self):
        rows = [
            {"period": "2026-04-01", "order_count": 10},
            {"period": "2026-04-02", "order_count": 20},
        ]
        filled = _fill_zero_periods(
            rows, "2026-04-01", "2026-04-03", "day", ["order_count"],
        )
        assert len(filled) == 2

    def test_month_fills(self):
        rows = [{"period": "2026-01-01", "val": 100}]
        filled = _fill_zero_periods(
            rows, "2026-01-01", "2026-04-01", "month", ["val"],
        )
        assert len(filled) == 3  # Jan, Feb, Mar
        periods = sorted(r["period"] for r in filled)
        assert periods == ["2026-01-01", "2026-02-01", "2026-03-01"]

    def test_week_fills(self):
        rows = [{"period": "2026-04-06", "x": 1}]  # Monday
        filled = _fill_zero_periods(
            rows, "2026-04-06", "2026-04-21", "week", ["x"],
        )
        # 2 full weeks: Apr 6, Apr 13
        assert len(filled) >= 2

    def test_multiple_metrics(self):
        rows = [{"period": "2026-04-01", "a": 1, "b": 2}]
        filled = _fill_zero_periods(
            rows, "2026-04-01", "2026-04-03", "day", ["a", "b"],
        )
        gap = next(r for r in filled if r["period"] == "2026-04-02")
        assert gap["a"] == 0
        assert gap["b"] == 0

    def test_sorted_output(self):
        rows = [
            {"period": "2026-04-03", "v": 3},
            {"period": "2026-04-01", "v": 1},
        ]
        filled = _fill_zero_periods(
            rows, "2026-04-01", "2026-04-04", "day", ["v"],
        )
        periods = [r["period"] for r in filled]
        assert periods == sorted(periods)


# ══════════════════════════════════════════════════════════
# _generate_periods
# ══════════════════════════════════════════════════════════


class TestGeneratePeriods:

    def test_day_periods(self):
        p = _generate_periods("2026-04-01", "2026-04-04", "day")
        assert p == [date(2026, 4, 1), date(2026, 4, 2), date(2026, 4, 3)]

    def test_month_periods(self):
        p = _generate_periods("2026-01-01", "2026-04-01", "month")
        assert p == [date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)]

    def test_month_cross_year(self):
        p = _generate_periods("2025-11-01", "2026-02-01", "month")
        assert p == [
            date(2025, 11, 1), date(2025, 12, 1),
            date(2026, 1, 1),
        ]

    def test_single_day(self):
        p = _generate_periods("2026-04-01", "2026-04-02", "day")
        assert p == [date(2026, 4, 1)]

    def test_empty_range(self):
        p = _generate_periods("2026-04-01", "2026-04-01", "day")
        assert p == []


# ══════════════════════════════════════════════════════════
# compute_comparison
# ══════════════════════════════════════════════════════════


class TestComputeComparison:

    def test_no_group_basic(self):
        result = compute_comparison(
            {"doc_count": 150, "total_qty": 300, "total_amount": 50000},
            {"doc_count": 120, "total_qty": 250, "total_amount": 42000},
            None,
        )
        assert len(result) == 1
        row = result[0]
        assert row["current_doc_count"] == 150.0
        assert row["prev_doc_count"] == 120.0
        assert row["doc_count_change"] == 30.0
        assert row["doc_count_growth"] == "+25.0%"
        assert row["total_amount_growth"] == "+19.0%"

    def test_no_group_decrease(self):
        result = compute_comparison(
            {"doc_count": 80, "total_qty": 100, "total_amount": 30000},
            {"doc_count": 100, "total_qty": 200, "total_amount": 50000},
            None,
        )
        row = result[0]
        assert row["doc_count_change"] == -20.0
        assert row["doc_count_growth"] == "-20.0%"
        assert row["total_amount_growth"] == "-40.0%"

    def test_prev_zero_infinite_growth(self):
        result = compute_comparison(
            {"doc_count": 10, "total_qty": 0, "total_amount": 1000},
            {"doc_count": 0, "total_qty": 0, "total_amount": 0},
            None,
        )
        row = result[0]
        assert row["doc_count_growth"] == "+∞%"
        assert row["total_qty_growth"] == "0.0%"
        assert row["total_amount_growth"] == "+∞%"

    def test_both_zero_returns_empty(self):
        result = compute_comparison(
            {"doc_count": 0, "total_qty": 0, "total_amount": 0},
            {"doc_count": 0, "total_qty": 0, "total_amount": 0},
            None,
        )
        assert result == []

    def test_grouped(self):
        cur = [
            {"group_key": "淘宝", "doc_count": 100, "total_qty": 200, "total_amount": 50000},
            {"group_key": "抖音", "doc_count": 80, "total_qty": 150, "total_amount": 32000},
        ]
        prev = [
            {"group_key": "淘宝", "doc_count": 90, "total_qty": 180, "total_amount": 42000},
            {"group_key": "拼多多", "doc_count": 30, "total_qty": 60, "total_amount": 15000},
        ]
        result = compute_comparison(cur, prev, "platform")
        keys = [r["group_key"] for r in result]
        assert "淘宝" in keys
        assert "抖音" in keys
        assert "拼多多" in keys

        # 淘宝有两期数据
        tb = next(r for r in result if r["group_key"] == "淘宝")
        assert tb["current_total_amount"] == 50000.0
        assert tb["prev_total_amount"] == 42000.0

        # 抖音只有当前期
        dy = next(r for r in result if r["group_key"] == "抖音")
        assert dy["current_total_amount"] == 32000.0
        assert dy["prev_total_amount"] == 0.0
        assert dy["total_amount_growth"] == "+∞%"

        # 拼多多只有基线期
        pdd = next(r for r in result if r["group_key"] == "拼多多")
        assert pdd["current_total_amount"] == 0.0
        assert pdd["prev_total_amount"] == 15000.0
        assert pdd["total_amount_growth"] == "-100.0%"

    def test_grouped_sorted_by_current_amount(self):
        cur = [
            {"group_key": "A", "doc_count": 1, "total_qty": 1, "total_amount": 100},
            {"group_key": "B", "doc_count": 1, "total_qty": 1, "total_amount": 500},
        ]
        prev = [
            {"group_key": "A", "doc_count": 1, "total_qty": 1, "total_amount": 100},
            {"group_key": "B", "doc_count": 1, "total_qty": 1, "total_amount": 100},
        ]
        result = compute_comparison(cur, prev, "platform")
        assert result[0]["group_key"] == "B"

    def test_empty_cur_data(self):
        result = compute_comparison([], {"doc_count": 10, "total_qty": 20, "total_amount": 1000}, None)
        row = result[0]
        assert row["current_doc_count"] == 0.0
        assert row["prev_doc_count"] == 10.0

    def test_list_single_item_as_summary(self):
        """单元素 list 应当正常处理为汇总。"""
        result = compute_comparison(
            [{"doc_count": 50, "total_qty": 100, "total_amount": 20000}],
            [{"doc_count": 40, "total_qty": 80, "total_amount": 15000}],
            None,
        )
        assert len(result) == 1
        assert result[0]["doc_count_growth"] == "+25.0%"


# ══════════════════════════════════════════════════════════
# _to_float
# ══════════════════════════════════════════════════════════


class TestToFloat:

    def test_int(self):
        assert _to_float(42) == 42.0

    def test_float(self):
        assert _to_float(3.14) == 3.14

    def test_string(self):
        assert _to_float("123.45") == 123.45

    def test_none(self):
        assert _to_float(None) == 0.0

    def test_invalid(self):
        assert _to_float("abc") == 0.0


# ══════════════════════════════════════════════════════════
# format_trend_summary
# ══════════════════════════════════════════════════════════


class TestFormatTrendSummary:

    def test_basic(self):
        rows = [
            {"period": "2026-04-01", "order_count": 10, "order_amount": 5000},
            {"period": "2026-04-02", "order_count": 15, "order_amount": 7500},
        ]
        s = format_trend_summary(rows, "day", ["order_count", "order_amount"], None)
        assert "日" in s
        assert "2 个时间点" in s
        assert "订单数" in s

    def test_with_group(self):
        rows = [
            {"period": "2026-04-01", "group_key": "淘宝", "order_count": 10},
            {"period": "2026-04-01", "group_key": "抖音", "order_count": 5},
        ]
        s = format_trend_summary(rows, "day", ["order_count"], "platform")
        assert "分组" in s
        assert "2 组" in s

    def test_amount_format(self):
        rows = [{"period": "2026-04-01", "order_amount": 12345.67}]
        s = format_trend_summary(rows, "month", ["order_amount"], None)
        assert "¥" in s


# ══════════════════════════════════════════════════════════
# format_compare_summary
# ══════════════════════════════════════════════════════════


class TestFormatCompareSummary:

    def test_no_group(self):
        compared = [{
            "current_doc_count": 150.0, "prev_doc_count": 120.0,
            "doc_count_change": 30.0, "doc_count_growth": "+25.0%",
            "current_total_qty": 300.0, "prev_total_qty": 250.0,
            "total_qty_change": 50.0, "total_qty_growth": "+20.0%",
            "current_total_amount": 50000.0, "prev_total_amount": 42000.0,
            "total_amount_change": 8000.0, "total_amount_growth": "+19.0%",
        }]
        s = format_compare_summary(compared, "mom", "04-01~04-30", "03-01~03-31")
        assert "环比" in s
        assert "+25.0%" in s
        assert "¥" in s

    def test_grouped(self):
        compared = [
            {"group_key": "淘宝", "current_total_amount": 50000, "total_amount_growth": "+19.0%"},
            {"group_key": "抖音", "current_total_amount": 32000, "total_amount_growth": "-8.6%"},
        ]
        s = format_compare_summary(compared, "yoy", "2026-04", "2025-04")
        assert "同比" in s
        assert "淘宝" in s
        assert "2 组" in s


# ══════════════════════════════════════════════════════════
# query_trend (async, mocked RPC)
# ══════════════════════════════════════════════════════════


class TestQueryTrend:

    @pytest.fixture
    def mock_db(self):
        db = MagicMock()
        return db

    def _setup_rpc(self, db, data):
        mock_result = MagicMock()
        mock_result.data = data
        mock_execute = MagicMock(return_value=mock_result)
        mock_rpc = MagicMock(return_value=MagicMock(execute=mock_execute))
        db.rpc = mock_rpc
        return mock_rpc

    @pytest.mark.asyncio
    async def test_basic_trend(self, mock_db):
        self._setup_rpc(mock_db, [
            {"period": "2026-04-01", "order_count": 10, "order_amount": 5000},
            {"period": "2026-04-02", "order_count": 15, "order_amount": 7500},
            {"period": "2026-04-03", "order_count": 12, "order_amount": 6000},
        ])

        result = await query_trend(
            mock_db, "org-1", "2026-04-01", "2026-04-04",
            time_granularity="day", metrics=["order_count", "order_amount"],
        )
        assert result.status != "error"
        assert len(result.data) == 3
        assert result.metadata["query_type"] == "trend"
        assert result.metadata["granularity"] == "day"

    @pytest.mark.asyncio
    async def test_trend_fills_zero(self, mock_db):
        """无分组时自动补零。"""
        self._setup_rpc(mock_db, [
            {"period": "2026-04-01", "order_count": 10},
        ])

        result = await query_trend(
            mock_db, "org-1", "2026-04-01", "2026-04-04",
            time_granularity="day", metrics=["order_count"],
        )
        assert len(result.data) == 3  # 3 days filled
        periods = [r["period"] for r in result.data]
        assert "2026-04-02" in periods
        assert "2026-04-03" in periods

    @pytest.mark.asyncio
    async def test_trend_empty(self, mock_db):
        self._setup_rpc(mock_db, [])

        result = await query_trend(
            mock_db, "org-1", "2026-04-01", "2026-04-04",
        )
        assert result.status == "empty"

    @pytest.mark.asyncio
    async def test_trend_rpc_error(self, mock_db):
        mock_db.rpc.side_effect = Exception("connection failed")

        result = await query_trend(
            mock_db, "org-1", "2026-04-01", "2026-04-04",
        )
        assert result.status == "error"
        assert "connection failed" in result.error_message

    @pytest.mark.asyncio
    async def test_trend_rpc_returns_error_dict(self, mock_db):
        self._setup_rpc(mock_db, {"error": "p_start must <= p_end"})

        result = await query_trend(
            mock_db, "org-1", "2026-04-10", "2026-04-01",
        )
        assert result.status == "error"

    @pytest.mark.asyncio
    async def test_trend_with_group_no_fill(self, mock_db):
        """有分组时不补零（不同分组的时间点不同）。"""
        self._setup_rpc(mock_db, [
            {"period": "2026-04-01", "group_key": "淘宝", "order_count": 10},
            {"period": "2026-04-02", "group_key": "淘宝", "order_count": 20},
        ])

        result = await query_trend(
            mock_db, "org-1", "2026-04-01", "2026-04-04",
            time_granularity="day", metrics=["order_count"],
            group_by="platform",
        )
        # 不补零，保持原样
        assert len(result.data) == 2

    @pytest.mark.asyncio
    async def test_trend_auto_adjust_granularity(self, mock_db):
        """跨度>1年时自动降为 month。"""
        self._setup_rpc(mock_db, [
            {"period": "2025-01-01", "order_count": 100},
        ])

        result = await query_trend(
            mock_db, "org-1", "2025-01-01", "2026-06-01",
            time_granularity="day",
        )
        # 应已自动调整为 month
        assert result.metadata["granularity"] == "month"
        # RPC 调用参数验证
        call_args = mock_db.rpc.call_args[0][1]
        assert call_args["p_granularity"] == "month"

    @pytest.mark.asyncio
    async def test_trend_translates_platform(self, mock_db):
        """platform 编码应翻译为中文。"""
        self._setup_rpc(mock_db, [
            {"period": "2026-04-01", "group_key": "tb", "order_count": 10},
            {"period": "2026-04-01", "group_key": "fxg", "order_count": 5},
        ])

        result = await query_trend(
            mock_db, "org-1", "2026-04-01", "2026-04-02",
            group_by="platform",
        )
        keys = [r["group_key"] for r in result.data]
        assert "淘宝" in keys
        assert "抖音" in keys


# ══════════════════════════════════════════════════════════
# query_compare (async, mocked RPC)
# ══════════════════════════════════════════════════════════


class TestQueryCompare:

    @pytest.fixture
    def mock_db(self):
        db = MagicMock()
        return db

    def _setup_rpc(self, db, cur_data, prev_data):
        """Mock erp_global_stats_query 返回两次不同数据。"""
        results = iter([cur_data, prev_data])

        def fake_rpc(name, params):
            mock_result = MagicMock()
            mock_result.data = next(results)
            return MagicMock(execute=MagicMock(return_value=mock_result))

        db.rpc = MagicMock(side_effect=fake_rpc)

    @pytest.mark.asyncio
    async def test_basic_mom(self, mock_db):
        self._setup_rpc(
            mock_db,
            {"doc_count": 150, "total_qty": 300, "total_amount": 50000},
            {"doc_count": 120, "total_qty": 250, "total_amount": 42000},
        )

        result = await query_compare(
            mock_db, "org-1", "order",
            "2026-04-01", "2026-05-01",
            compare_range="mom",
        )
        assert result.status != "error"
        assert result.metadata["query_type"] == "compare"
        assert result.metadata["compare_range"] == "mom"
        assert "2026-03-01" in result.metadata["prev_period"]
        assert len(result.data) == 1
        assert result.data[0]["doc_count_growth"] == "+25.0%"

    @pytest.mark.asyncio
    async def test_yoy(self, mock_db):
        self._setup_rpc(
            mock_db,
            {"doc_count": 200, "total_qty": 400, "total_amount": 80000},
            {"doc_count": 100, "total_qty": 200, "total_amount": 40000},
        )

        result = await query_compare(
            mock_db, "org-1", "order",
            "2026-04-01", "2026-05-01",
            compare_range="yoy",
        )
        assert "2025-04-01" in result.metadata["prev_period"]
        assert result.data[0]["total_amount_growth"] == "+100.0%"

    @pytest.mark.asyncio
    async def test_grouped_compare(self, mock_db):
        self._setup_rpc(
            mock_db,
            [
                {"group_key": "tb", "doc_count": 100, "total_qty": 200, "total_amount": 50000},
                {"group_key": "fxg", "doc_count": 80, "total_qty": 150, "total_amount": 32000},
            ],
            [
                {"group_key": "tb", "doc_count": 90, "total_qty": 180, "total_amount": 42000},
            ],
        )

        result = await query_compare(
            mock_db, "org-1", "order",
            "2026-04-01", "2026-05-01",
            compare_range="mom", group_by="platform",
        )
        assert len(result.data) == 2
        # platform 编码应被翻译
        keys = [r["group_key"] for r in result.data]
        assert "淘宝" in keys
        assert "抖音" in keys

    @pytest.mark.asyncio
    async def test_compare_rpc_error(self, mock_db):
        mock_db.rpc.side_effect = Exception("timeout")

        result = await query_compare(
            mock_db, "org-1", "order",
            "2026-04-01", "2026-05-01",
        )
        assert result.status == "error"
        assert "timeout" in result.error_message

    @pytest.mark.asyncio
    async def test_compare_both_empty(self, mock_db):
        self._setup_rpc(
            mock_db,
            {"doc_count": 0, "total_qty": 0, "total_amount": 0},
            {"doc_count": 0, "total_qty": 0, "total_amount": 0},
        )

        result = await query_compare(
            mock_db, "org-1", "order",
            "2026-04-01", "2026-05-01",
        )
        assert result.status == "empty"

    @pytest.mark.asyncio
    async def test_invalid_compare_range(self, mock_db):
        result = await query_compare(
            mock_db, "org-1", "order",
            "2026-04-01", "2026-05-01",
            compare_range="invalid",
        )
        assert result.status == "error"
        assert "未知 compare_range" in result.error_message


# ══════════════════════════════════════════════════════════
# _parse_date
# ══════════════════════════════════════════════════════════


class TestParseDate:

    def test_date_string(self):
        assert _parse_date("2026-04-27") == date(2026, 4, 27)

    def test_datetime_string(self):
        assert _parse_date("2026-04-27T10:30:00") == date(2026, 4, 27)

    def test_with_spaces(self):
        assert _parse_date("  2026-04-27  ") == date(2026, 4, 27)
