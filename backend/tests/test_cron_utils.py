"""测试 cron_utils 模块"""
from __future__ import annotations
import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pytest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from core.exceptions import ValidationError
from services.scheduler.cron_utils import (
    calc_next_run,
    compose_cron,
    parse_cron_readable,
    validate_cron,
)


CN_TZ = ZoneInfo("Asia/Shanghai")


class TestValidateCron:
    def test_valid_5_field(self):
        assert validate_cron("0 9 * * *")
        assert validate_cron("*/30 * * * *")
        assert validate_cron("0 9 1 * *")
        assert validate_cron("0 9 * * 1")
        assert validate_cron("0 9 * * 1-5")

    def test_invalid(self):
        assert not validate_cron("invalid")
        assert not validate_cron("60 9 * * *")  # minute 越界
        assert not validate_cron("0 25 * * *")  # hour 越界
        assert not validate_cron("")


class TestParseCronReadable:
    def test_daily(self):
        assert parse_cron_readable("0 9 * * *") == "每天 09:00"
        assert parse_cron_readable("30 8 * * *") == "每天 08:30"

    def test_weekly(self):
        assert parse_cron_readable("0 9 * * 1") == "每周一 09:00"
        assert parse_cron_readable("0 9 * * 5") == "每周五 09:00"
        assert parse_cron_readable("0 18 * * 0") == "每周日 18:00"

    def test_monthly(self):
        assert parse_cron_readable("0 9 1 * *") == "每月 1 日 09:00"
        assert parse_cron_readable("0 0 15 * *") == "每月 15 日 00:00"

    def test_interval(self):
        assert parse_cron_readable("*/30 * * * *") == "每 30 分钟"
        assert parse_cron_readable("*/5 * * * *") == "每 5 分钟"

    def test_invalid(self):
        assert parse_cron_readable("invalid") == "cron: invalid"


class TestCalcNextRun:
    def test_daily_at_9am(self):
        """周三 8:00 → 下次是当天 9:00"""
        base = datetime(2026, 4, 8, 8, 0, tzinfo=CN_TZ)  # Wed 8am
        next_run = calc_next_run("0 9 * * *", "Asia/Shanghai", base)
        next_local = next_run.astimezone(CN_TZ)
        assert next_local.hour == 9
        assert next_local.minute == 0
        assert next_local.day == 8

    def test_daily_at_9am_after(self):
        """周三 10:00 → 下次是周四 9:00"""
        base = datetime(2026, 4, 8, 10, 0, tzinfo=CN_TZ)
        next_run = calc_next_run("0 9 * * *", "Asia/Shanghai", base)
        next_local = next_run.astimezone(CN_TZ)
        assert next_local.hour == 9
        assert next_local.day == 9

    def test_weekly_monday(self):
        """周三 → 下次是下周一"""
        base = datetime(2026, 4, 8, 10, 0, tzinfo=CN_TZ)  # Wed
        next_run = calc_next_run("0 9 * * 1", "Asia/Shanghai", base)
        next_local = next_run.astimezone(CN_TZ)
        assert next_local.weekday() == 0  # Monday
        assert next_local.hour == 9

    def test_returns_utc(self):
        """返回值必须是 UTC 时区"""
        base = datetime(2026, 4, 8, 8, 0, tzinfo=CN_TZ)
        next_run = calc_next_run("0 9 * * *", "Asia/Shanghai", base)
        assert next_run.tzinfo == timezone.utc

    def test_monthly(self):
        """4 月 5 日 → 下次是 5 月 1 日"""
        base = datetime(2026, 4, 5, 10, 0, tzinfo=CN_TZ)
        next_run = calc_next_run("0 9 1 * *", "Asia/Shanghai", base)
        next_local = next_run.astimezone(CN_TZ)
        assert next_local.month == 5
        assert next_local.day == 1
        assert next_local.hour == 9


# ════════════════════════════════════════════════════════════════
# compose_cron — 结构化频率组装
# ════════════════════════════════════════════════════════════════

class TestComposeCron:
    def test_once_returns_none(self):
        assert compose_cron("once", "22:00") is None

    def test_daily(self):
        assert compose_cron("daily", "09:00") == "0 9 * * *"
        assert compose_cron("daily", "22:30") == "30 22 * * *"
        assert compose_cron("daily", "00:00") == "0 0 * * *"

    def test_weekly_single_day(self):
        assert compose_cron("weekly", "09:00", weekdays=[1]) == "0 9 * * 1"

    def test_weekly_multiple_days(self):
        # 周一三五
        assert compose_cron("weekly", "09:00", weekdays=[1, 3, 5]) == "0 9 * * 1,3,5"

    def test_weekly_dedup_and_sort(self):
        # 重复 + 乱序 → 自动去重 + 排序
        assert compose_cron("weekly", "09:00", weekdays=[5, 1, 1, 3]) == "0 9 * * 1,3,5"

    def test_weekly_missing_weekdays_raises(self):
        with pytest.raises(ValidationError, match="weekdays"):
            compose_cron("weekly", "09:00")

    def test_weekly_invalid_weekday_filtered(self):
        # 7 / -1 这种非法值会被过滤掉
        with pytest.raises(ValidationError, match="weekdays"):
            compose_cron("weekly", "09:00", weekdays=[7, -1])

    def test_monthly(self):
        assert compose_cron("monthly", "09:00", day_of_month=15) == "0 9 15 * *"
        assert compose_cron("monthly", "09:00", day_of_month=1) == "0 9 1 * *"
        assert compose_cron("monthly", "09:00", day_of_month=31) == "0 9 31 * *"

    def test_monthly_missing_day_raises(self):
        with pytest.raises(ValidationError, match="day_of_month"):
            compose_cron("monthly", "09:00")

    def test_monthly_out_of_range_raises(self):
        with pytest.raises(ValidationError, match="day_of_month"):
            compose_cron("monthly", "09:00", day_of_month=32)
        with pytest.raises(ValidationError, match="day_of_month"):
            compose_cron("monthly", "09:00", day_of_month=0)

    def test_invalid_time_str(self):
        with pytest.raises(ValidationError):
            compose_cron("daily", "abc")
        with pytest.raises(ValidationError):
            compose_cron("daily", "25:00")
        with pytest.raises(ValidationError):
            compose_cron("daily", "")

    def test_cron_type_raises(self):
        # cron 类型应直接用 cron_expr，调用 compose_cron 会报错
        with pytest.raises(ValidationError, match="cron"):
            compose_cron("cron", "09:00")

    def test_unknown_type(self):
        with pytest.raises(ValidationError, match="schedule_type"):
            compose_cron("yearly", "09:00")

    def test_composed_cron_is_valid(self):
        """组装出的 cron 必须能被 croniter 接受"""
        for cron in [
            compose_cron("daily", "09:00"),
            compose_cron("weekly", "09:00", weekdays=[1, 3, 5]),
            compose_cron("monthly", "09:00", day_of_month=15),
        ]:
            assert validate_cron(cron)
