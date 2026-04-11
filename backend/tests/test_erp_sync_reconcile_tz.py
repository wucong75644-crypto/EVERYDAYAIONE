"""ERP 同步对账时区修复测试 (PR3 C1)。

回归：旧实现 _yesterday_range 用 datetime.now() 无时区，
容器 TZ=UTC 时凌晨对账查 UTC 昨天 = 北京时间 8:00–32:00 → 丢数据。

修复：统一改用 utils.time_context.now_cn()。

注意：用 unittest.mock.patch 替代 time-machine，避免 macOS + Python 3.12 +
pandas/pyarrow C 扩展联跑时的 segfault。

设计文档：docs/document/TECH_ERP时间准确性架构.md §14.7
"""

from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from services.kuaimai.erp_sync_reconcile import _yesterday_range
from utils.time_context import CN_TZ

CN = ZoneInfo("Asia/Shanghai")


class TestYesterdayRange:
    """_yesterday_range 时区正确性。"""

    def test_returns_aware_datetime(self):
        """返回值必须带时区（aware datetime）。"""
        y, t = _yesterday_range()
        assert y.tzinfo is not None, "yesterday must be aware"
        assert t.tzinfo is not None, "today must be aware"
        assert y.tzinfo == CN_TZ
        assert t.tzinfo == CN_TZ

    @patch("services.kuaimai.erp_sync_reconcile.now_cn")
    def test_at_03_00_returns_yesterday_full_day(self, mock_now):
        """凌晨 3:00 跑对账时返回 2026-04-10 全天。"""
        mock_now.return_value = datetime(2026, 4, 11, 3, 0, tzinfo=CN)
        y, t = _yesterday_range()
        assert y == datetime(2026, 4, 10, 0, 0, tzinfo=CN)
        assert t == datetime(2026, 4, 11, 0, 0, tzinfo=CN)

    @patch("services.kuaimai.erp_sync_reconcile.now_cn")
    def test_at_midnight_returns_previous_full_day(self, mock_now):
        """凌晨 00:00:01 返回的"昨天"是 4-10。"""
        mock_now.return_value = datetime(2026, 4, 11, 0, 0, 1, tzinfo=CN)
        y, t = _yesterday_range()
        assert y.date() == datetime(2026, 4, 10).date()
        assert t.date() == datetime(2026, 4, 11).date()

    @patch("services.kuaimai.erp_sync_reconcile.now_cn")
    def test_at_late_night_still_returns_yesterday(self, mock_now):
        """23:59:59 仍返回当天的前一天（4-10）。"""
        mock_now.return_value = datetime(2026, 4, 11, 23, 59, 59, tzinfo=CN)
        y, t = _yesterday_range()
        assert y.date() == datetime(2026, 4, 10).date()
        assert t.date() == datetime(2026, 4, 11).date()

    @patch("services.kuaimai.erp_sync_reconcile.now_cn")
    def test_year_boundary(self, mock_now):
        """跨年场景：2026-01-01 凌晨返回 2025-12-31。"""
        mock_now.return_value = datetime(2026, 1, 1, 3, 0, tzinfo=CN)
        y, t = _yesterday_range()
        assert y == datetime(2025, 12, 31, 0, 0, tzinfo=CN)
        assert t == datetime(2026, 1, 1, 0, 0, tzinfo=CN)

    @patch("services.kuaimai.erp_sync_reconcile.now_cn")
    def test_month_boundary(self, mock_now):
        """跨月场景：3-01 凌晨返回 2-28（2026 非闰年）。"""
        mock_now.return_value = datetime(2026, 3, 1, 3, 0, tzinfo=CN)
        y, t = _yesterday_range()
        assert y == datetime(2026, 2, 28, 0, 0, tzinfo=CN)
        assert t == datetime(2026, 3, 1, 0, 0, tzinfo=CN)

    def test_24_hour_span(self):
        """yesterday 到 today 跨度恰好 24 小时。"""
        y, t = _yesterday_range()
        delta = t - y
        assert delta.total_seconds() == 86400  # 24 * 3600
