"""分批导出单测

覆盖：时间切片计算 + 分批导出流程 mock
"""

import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from services.kuaimai.erp_batch_export import (
    _parse_iso,
    compute_time_slices,
)
from services.kuaimai.erp_query_preflight import BATCH_THRESHOLD


class TestComputeTimeSlices:
    """时间切片计算"""

    def test_small_data_single_slice(self):
        """行数 < BATCH_THRESHOLD → 1 个切片"""
        slices = compute_time_slices(
            "2026-04-01", "2026-04-30", BATCH_THRESHOLD - 1,
        )
        assert len(slices) == 1
        # _parse_iso 会把日期转为 datetime，isoformat 输出带 T00:00:00
        assert "2026-04-01" in slices[0][0]
        assert "2026-04-30" in slices[0][1]

    def test_two_slices(self):
        """行数略超 BATCH_THRESHOLD → 2 个切片"""
        slices = compute_time_slices(
            "2026-04-01T00:00:00", "2026-04-30T00:00:00",
            BATCH_THRESHOLD + 1,
        )
        assert len(slices) == 2
        # 最后一片结束时间对齐原始 end
        assert slices[-1][1] == "2026-04-30T00:00:00"

    def test_ten_slices(self):
        """30 万行 → 10 个切片"""
        slices = compute_time_slices(
            "2026-04-01T00:00:00", "2026-05-01T00:00:00",
            300_000,
        )
        assert len(slices) == 10

    def test_max_100_slices(self):
        """500 万行 → 上限 100 个切片"""
        slices = compute_time_slices(
            "2026-01-01T00:00:00", "2026-12-31T00:00:00",
            5_000_000,
        )
        assert len(slices) == 100

    def test_zero_duration(self):
        """start == end → 1 个切片"""
        slices = compute_time_slices(
            "2026-04-01T00:00:00", "2026-04-01T00:00:00", 100_000,
        )
        assert len(slices) == 1

    def test_slices_cover_full_range(self):
        """切片连续覆盖整个时间范围，无缝隙"""
        slices = compute_time_slices(
            "2026-04-01T00:00:00+08:00", "2026-04-30T00:00:00+08:00",
            90_000,
        )
        assert len(slices) == 3
        # 第一片从 start 开始
        assert slices[0][0] == "2026-04-01T00:00:00+08:00"
        # 最后一片到 end 结束
        assert slices[-1][1] == "2026-04-30T00:00:00+08:00"
        # 相邻片衔接
        for i in range(len(slices) - 1):
            assert slices[i][1] == slices[i + 1][0]


class TestParseIso:
    """ISO 时间解析"""

    def test_date_only(self):
        dt = _parse_iso("2026-04-01")
        assert dt.year == 2026 and dt.month == 4 and dt.day == 1

    def test_datetime(self):
        dt = _parse_iso("2026-04-01T10:30:00")
        assert dt.hour == 10 and dt.minute == 30

    def test_datetime_with_tz(self):
        dt = _parse_iso("2026-04-01T10:30:00+08:00")
        assert dt.hour == 10

    def test_space_separator(self):
        dt = _parse_iso("2026-04-01 10:30:00")
        assert dt.hour == 10
