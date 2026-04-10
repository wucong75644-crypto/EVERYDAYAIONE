"""时间事实层 SSOT 单元测试。

设计文档：docs/document/TECH_ERP时间准确性架构.md §8
"""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
import time_machine

from utils.relative_label import compute_relative_label
from utils.time_context import (
    CN_TZ,
    WEEKDAYS_CN,
    ComparePoint,
    DateRange,
    RequestContext,
    TimePoint,
    format_time_header,
    make_n_days_header,
    now_cn,
)


CN = ZoneInfo("Asia/Shanghai")
FRI_4_10 = datetime(2026, 4, 10, 13, 5, tzinfo=CN)


# ────────────────────────────────────────────────────────────────────
# CN_TZ 单一来源
# ────────────────────────────────────────────────────────────────────


def test_cn_tz_is_zoneinfo_asia_shanghai():
    assert CN_TZ.key == "Asia/Shanghai"


def test_now_cn_is_aware_and_in_cn_tz():
    n = now_cn()
    assert n.tzinfo is not None
    # offset 应该是 +08:00
    assert n.utcoffset() == timedelta(hours=8)


# ────────────────────────────────────────────────────────────────────
# TimePoint
# ────────────────────────────────────────────────────────────────────


@time_machine.travel(FRI_4_10, tick=False)
def test_timepoint_4_10_is_friday():
    """4-10 必须是周五（weekday_cn 不能错）— 这是 4-10 bug 的核心修复。"""
    tp = TimePoint.from_datetime(now_cn())
    assert tp.weekday == 4  # Mon=0, Fri=4
    assert tp.weekday_cn == "周五"
    assert tp.iso_week == 15
    assert tp.iso_year == 2026
    assert tp.is_workday is True
    assert tp.relative_label == "今天"
    assert "周五" in tp.display_cn


def test_timepoint_4_3_is_friday_too():
    """4-3 也是周五（4-10 bug 中错标为周四的那天）。"""
    tp = TimePoint.from_date(date(2026, 4, 3), reference=FRI_4_10)
    assert tp.weekday_cn == "周五"
    assert tp.relative_label == "上周五"  # 不能是「上周四」


def test_timepoint_holiday_recognition():
    """法定节假日识别（chinese-calendar 1.11.0 已覆盖 2026）。"""
    tp = TimePoint.from_date(date(2026, 1, 1))
    assert tp.is_holiday is True
    assert tp.holiday_name == "元旦"

    tp2 = TimePoint.from_date(date(2026, 4, 5))
    assert tp2.is_holiday is True
    assert tp2.holiday_name == "清明节"


# ────────────────────────────────────────────────────────────────────
# DateRange
# ────────────────────────────────────────────────────────────────────


@time_machine.travel(FRI_4_10, tick=False)
def test_date_range_for_today():
    ctx = RequestContext.build(user_id="u")
    dr = DateRange.for_today(ctx)
    assert dr.start.date_str == "2026-04-10"
    assert dr.start.weekday_cn == "周五"
    assert dr.span_days == 1
    assert dr.workday_count == 1


@time_machine.travel(FRI_4_10, tick=False)
def test_date_range_for_this_week_iso_monday():
    """本周 = ISO 周（周一为始）。4-10 是周五，本周 = 4-6 ~ 4-12。"""
    ctx = RequestContext.build(user_id="u")
    dr = DateRange.for_this_week(ctx)
    assert dr.start.date_str == "2026-04-06"
    assert dr.start.weekday_cn == "周一"
    assert dr.end.date_str == "2026-04-12"
    assert dr.end.weekday_cn == "周日"


@time_machine.travel(FRI_4_10, tick=False)
def test_date_range_for_last_week():
    ctx = RequestContext.build(user_id="u")
    dr = DateRange.for_last_week(ctx)
    assert dr.start.date_str == "2026-03-30"
    assert dr.start.weekday_cn == "周一"
    assert dr.end.date_str == "2026-04-05"
    assert dr.end.weekday_cn == "周日"


@time_machine.travel(FRI_4_10, tick=False)
def test_date_range_for_yesterday():
    ctx = RequestContext.build(user_id="u")
    dr = DateRange.for_yesterday(ctx)
    assert dr.start.date_str == "2026-04-09"
    assert dr.start.weekday_cn == "周四"


@time_machine.travel(FRI_4_10, tick=False)
def test_date_range_workday_count_excludes_qingming():
    """4-1 ~ 4-12 应该有 8 个工作日（清明 4-4/4-5/4-6 周末+假期）。"""
    ctx = RequestContext.build(user_id="u")
    s = datetime(2026, 4, 1, tzinfo=CN)
    e = datetime(2026, 4, 12, 23, 59, 59, tzinfo=CN)
    dr = DateRange.custom(s, e, reference=ctx.now)
    # 4/1(三) 4/2(四) 4/3(五) 工作日 3
    # 4/4(六) 4/5(日清明) 4/6(一清明) 节假日/周末 0
    # 4/7(二) 4/8(三) 4/9(四) 4/10(五) 工作日 4
    # 4/11(六) 4/12(日) 周末 0
    assert dr.workday_count == 7  # 3 + 4 = 7
    assert dr.span_days == 12


# ────────────────────────────────────────────────────────────────────
# 跨午夜 / 跨周 / 跨月 边界
# ────────────────────────────────────────────────────────────────────


def test_request_context_frozen_now_does_not_drift():
    """RequestContext.now 是 frozen，不会跨午夜漂移。"""
    n = datetime(2026, 4, 10, 23, 59, tzinfo=CN)
    ctx = RequestContext(
        now=n,
        today=TimePoint.from_datetime(n, reference=n),
        user_id="u",
    )
    # 即使睡觉跨午夜，ctx.now 不变
    assert ctx.now.day == 10
    assert ctx.today.weekday_cn == "周五"


# ────────────────────────────────────────────────────────────────────
# 相对时间标签
# ────────────────────────────────────────────────────────────────────


def test_relative_label_today_yesterday():
    ref = date(2026, 4, 10)
    assert compute_relative_label(ref, ref) == "今天"
    assert compute_relative_label(date(2026, 4, 9), ref) == "昨天"
    assert compute_relative_label(date(2026, 4, 8), ref) == "前天"
    assert compute_relative_label(date(2026, 4, 11), ref) == "明天"


def test_relative_label_no_double_zhou():
    """禁止出现「上周周五」这种双"周"字 bug。"""
    ref = date(2026, 4, 10)
    label = compute_relative_label(date(2026, 4, 3), ref)
    assert "周周" not in label
    assert label == "上周五"


def test_relative_label_this_week():
    ref = date(2026, 4, 10)
    assert compute_relative_label(date(2026, 4, 7), ref) == "本周二"
    assert compute_relative_label(date(2026, 4, 6), ref) == "本周一"


def test_relative_label_year_ago():
    ref = date(2026, 4, 10)
    assert compute_relative_label(date(2025, 4, 10), ref) == "去年4月10日"


# ────────────────────────────────────────────────────────────────────
# Format time header
# ────────────────────────────────────────────────────────────────────


@time_machine.travel(FRI_4_10, tick=False)
def test_format_time_header_today():
    ctx = RequestContext.build(user_id="u")
    dr = DateRange.for_today(ctx)
    header = format_time_header(ctx=ctx, range_=dr, kind="统计区间")
    assert "2026-04-10" in header
    assert "周五" in header
    assert "今天" in header
    assert "[统计区间]" in header


@time_machine.travel(FRI_4_10, tick=False)
def test_make_n_days_header():
    ctx = RequestContext.build(user_id="u")
    h = make_n_days_header(ctx=ctx, days=7, kind="查询窗口")
    assert "[查询窗口]" in h
    # 4-10 往前 7 天 = 4-4 ~ 4-10 (含)
    assert "2026-04-04" in h
    assert "2026-04-10" in h


# ────────────────────────────────────────────────────────────────────
# Prompt 注入
# ────────────────────────────────────────────────────────────────────


@time_machine.travel(FRI_4_10, tick=False)
def test_prompt_injection_contains_chinese_weekday():
    """prompt 注入必须含中文星期 + ISO week + 硬规则。"""
    ctx = RequestContext.build(user_id="u")
    s = ctx.for_prompt_injection()
    assert "2026-04-10" in s
    assert "周五" in s
    assert "ISO 第 15 周" in s
    assert "禁止自行推算" in s


# ────────────────────────────────────────────────────────────────────
# 边界场景 — 跨月/跨年/闰年/月末日不存在
# ────────────────────────────────────────────────────────────────────


@time_machine.travel(datetime(2026, 1, 1, 9, 0, tzinfo=CN), tick=False)
def test_for_last_month_from_january():
    """1 月的"上月" = 上一年 12 月。"""
    ctx = RequestContext.build(user_id="u")
    dr = DateRange.for_last_month(ctx)
    assert dr.start.date_str == "2025-12-01"
    assert dr.end.date_str == "2025-12-31"


@time_machine.travel(datetime(2026, 12, 31, 23, 59, tzinfo=CN), tick=False)
def test_for_today_at_year_end():
    """12-31 23:59 的"今天"必须是 12-31，不会跨年。"""
    ctx = RequestContext.build(user_id="u")
    dr = DateRange.for_today(ctx)
    assert dr.start.date_str == "2026-12-31"
    assert dr.end.date_str == "2026-12-31"
    assert dr.start.weekday_cn == "周四"


def test_compare_stats_yoy_leap_year_29_feb():
    """02-29 同比上一年应该降级到 02-28（不是闰年）。"""
    from services.kuaimai.erp_local_compare_stats import _shift_years
    leap_day = datetime(2024, 2, 29, 12, 0, tzinfo=CN)
    shifted = _shift_years(leap_day, -1)
    assert shifted.year == 2023
    assert shifted.month == 2
    assert shifted.day == 28


def test_compare_stats_mom_month_end_day_does_not_exist():
    """5-31 月环比上月：4 月没有 31 日 → 降级到 4-30。"""
    from services.kuaimai.erp_local_compare_stats import _shift_months
    may_31 = datetime(2026, 5, 31, 13, 0, tzinfo=CN)
    shifted = _shift_months(may_31, -1)
    assert shifted.year == 2026
    assert shifted.month == 4
    assert shifted.day == 30  # 4 月只有 30 天


def test_compare_stats_mom_january_to_december():
    """1 月环比上月：跨年到上一年 12 月。"""
    from services.kuaimai.erp_local_compare_stats import _shift_months
    jan = datetime(2026, 1, 15, 13, 0, tzinfo=CN)
    shifted = _shift_months(jan, -1)
    assert shifted.year == 2025
    assert shifted.month == 12
    assert shifted.day == 15


def test_relative_label_cross_iso_year_boundary():
    """跨 ISO 年的"上周"判断（2026 第 1 周相对 2025 第 53 周）。"""
    # 2025-12-29 是周一，2025 ISO 第 53 周开始
    # 2026-01-05 是周一，2026 ISO 第 2 周开始
    # 2026-01-01 (周四) 仍然是 ISO 2026-W01
    # 2025-12-29 (周一) 是 ISO 2026-W01 的上一周（实际上 W53 of 2025）
    ref = date(2026, 1, 5)  # 2026-W2 周一
    target = date(2025, 12, 29)  # 2026-W1 周一（注意 isocalendar 跨年）
    label = compute_relative_label(target, ref)
    # 至少不能崩溃；具体语义可接受 "上周一" 或 "8天前"
    assert label is not None
    assert "周周" not in label


@time_machine.travel(FRI_4_10, tick=False)
def test_format_time_header_no_ctx_no_range_returns_empty():
    """边界：无 ctx 无 range 返回空字符串。"""
    h = format_time_header()
    assert h == ""


@time_machine.travel(FRI_4_10, tick=False)
def test_make_n_days_header_n_equals_1():
    """近 1 天 = 仅今天。"""
    ctx = RequestContext.build(user_id="u")
    h = make_n_days_header(ctx=ctx, days=1, kind="X")
    assert "2026-04-10" in h
    assert "[X]" in h


@time_machine.travel(FRI_4_10, tick=False)
def test_date_range_custom_start_must_be_less_than_end():
    """custom range 必须 start < end，否则抛 ValueError。"""
    ctx = RequestContext.build(user_id="u")
    same = ctx.now
    with pytest.raises(ValueError, match="start.*must be < end"):
        DateRange.custom(same, same, reference=ctx.now)

