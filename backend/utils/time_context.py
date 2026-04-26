"""时间事实层 SSOT。

铁律：凡是有唯一正确答案的事实（日期/星期/相对时间/工作日），
必须由代码计算；模型只负责语言表达。

设计文档: ``docs/document/TECH_ERP时间准确性架构.md`` §5
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Literal, Optional, Union
from zoneinfo import ZoneInfo

# IANA 标准时区，替代 timezone(timedelta(hours=8)) 工程坏味道
CN_TZ = ZoneInfo("Asia/Shanghai")

# 中文星期映射，禁用 locale.setlocale (全局非线程安全)
WEEKDAYS_CN: tuple[str, ...] = (
    "周一", "周二", "周三", "周四", "周五", "周六", "周日",
)


def now_cn() -> datetime:
    """获取当前北京时间（aware datetime）。

    全后端唯一允许的"获取当前时间"入口，禁止裸调 datetime.now()。
    """
    return datetime.now(CN_TZ)


def _to_cn_aware(dt: datetime) -> datetime:
    """统一为 CN_TZ aware datetime。

    naive datetime 假定为 CN_TZ；其他时区转换到 CN_TZ。
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=CN_TZ)
    return dt.astimezone(CN_TZ)


def _parse_iso_to_cn(value: Optional[Union[str, datetime]]) -> Optional[datetime]:
    """将 DB 返回的 ISO 时间字符串/datetime 转为北京时间，解析失败返回 None。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return _to_cn_aware(value)
    try:
        from dateutil.parser import isoparse
        return _to_cn_aware(isoparse(str(value)))
    except Exception:
        return None


# ────────────────────────────────────────────────────────────────────
# TimePoint — 单个时间点的结构化表示
# ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TimePoint:
    """单个时间点的结构化表示。

    LLM 直接使用 weekday_cn / display_cn / relative_label，
    禁止自己推算 weekday/相对日期。
    """

    iso: str                          # "2026-04-10T13:05:00+08:00"
    date_str: str                     # "2026-04-10"
    weekday: int                      # 0=周一, 6=周日
    weekday_cn: str                   # "周五"
    iso_week: int                     # 15
    iso_year: int                     # 2026
    is_workday: bool                  # 节假日感知
    is_holiday: bool                  # 法定假日
    holiday_name: Optional[str]       # "清明节" / None
    is_lieu: bool                     # 调休补班日
    is_spring_festival_window: bool   # 春节前后 ±15 天
    display_cn: str                   # "2026年4月10日 周五"
    relative_label: str               # "今天" / "昨天" / "上周五" / ...

    @classmethod
    def from_datetime(
        cls,
        dt: datetime,
        *,
        reference: Optional[datetime] = None,
    ) -> "TimePoint":
        """从 datetime 构造 TimePoint。

        Args:
            dt: 目标时间点（naive datetime 假定为 CN_TZ）
            reference: 用于计算相对时间标签的参考"今天"，默认 = dt
        """
        from utils.holiday import (
            get_holiday_name,
            is_holiday,
            is_lieu_workday,
            is_spring_festival_window,
            is_workday,
        )
        from utils.relative_label import compute_relative_label

        dt = _to_cn_aware(dt)
        d = dt.date()
        iso_year, iso_week, _ = d.isocalendar()
        ref = _to_cn_aware(reference) if reference else dt

        return cls(
            iso=dt.isoformat(),
            date_str=d.strftime("%Y-%m-%d"),
            weekday=d.weekday(),
            weekday_cn=WEEKDAYS_CN[d.weekday()],
            iso_week=iso_week,
            iso_year=iso_year,
            is_workday=is_workday(d),
            is_holiday=is_holiday(d),
            holiday_name=get_holiday_name(d),
            is_lieu=is_lieu_workday(d),
            is_spring_festival_window=is_spring_festival_window(d),
            display_cn=f"{d.year}年{d.month}月{d.day}日 {WEEKDAYS_CN[d.weekday()]}",
            relative_label=compute_relative_label(d, ref.date()),
        )

    @classmethod
    def from_date(
        cls,
        d: date,
        *,
        reference: Optional[datetime] = None,
    ) -> "TimePoint":
        """从 date 构造 TimePoint（时间设为当天 00:00 北京时间）。"""
        dt = datetime(d.year, d.month, d.day, tzinfo=CN_TZ)
        return cls.from_datetime(dt, reference=reference)


# ────────────────────────────────────────────────────────────────────
# DateRange — 时间范围的结构化表示
# ────────────────────────────────────────────────────────────────────


PeriodKind = Literal[
    "day", "week", "month", "quarter", "year",
    "last_n_days", "yesterday", "custom",
]


@dataclass(frozen=True)
class DateRange:
    """时间范围的结构化表示。

    工具内部计算时间范围后，返回 DateRange 供格式化器渲染时间块。
    """

    start: TimePoint
    end: TimePoint
    period_kind: PeriodKind
    period_label: str    # "2026-04-10 周五" / "本周（4-06~4-12）"
    span_days: int       # 范围跨度（天，含头尾）
    workday_count: int   # 范围内工作日数

    @staticmethod
    def _workday_count(start_d: date, end_d: date) -> int:
        from utils.holiday import is_workday
        cnt = 0
        d = start_d
        while d <= end_d:
            if is_workday(d):
                cnt += 1
            d += timedelta(days=1)
        return cnt

    @classmethod
    def _build(
        cls,
        start: datetime,
        end: datetime,
        *,
        period_kind: PeriodKind,
        period_label: str,
        reference: datetime,
    ) -> "DateRange":
        start = _to_cn_aware(start)
        end = _to_cn_aware(end)
        return cls(
            start=TimePoint.from_datetime(start, reference=reference),
            end=TimePoint.from_datetime(end, reference=reference),
            period_kind=period_kind,
            period_label=period_label,
            span_days=(end.date() - start.date()).days + 1,
            workday_count=cls._workday_count(start.date(), end.date()),
        )

    # ── 工厂方法 ────────────────────────────────────────────

    @classmethod
    def for_today(cls, ctx: "RequestContext") -> "DateRange":
        now = ctx.now
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = now
        label = f"今天（{start.strftime('%Y-%m-%d')} {WEEKDAYS_CN[start.weekday()]}）"
        return cls._build(
            start, end,
            period_kind="day", period_label=label, reference=ctx.now,
        )

    @classmethod
    def for_yesterday(cls, ctx: "RequestContext") -> "DateRange":
        now = ctx.now
        yesterday = (now - timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0,
        )
        end = yesterday.replace(hour=23, minute=59, second=59)
        label = f"昨天（{yesterday.strftime('%Y-%m-%d')} {WEEKDAYS_CN[yesterday.weekday()]}）"
        return cls._build(
            yesterday, end,
            period_kind="yesterday", period_label=label, reference=ctx.now,
        )

    @classmethod
    def for_this_week(cls, ctx: "RequestContext") -> "DateRange":
        """ISO 周（周一到周日）。"""
        now = ctx.now
        monday = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0,
        )
        sunday = monday + timedelta(days=6, hours=23, minutes=59, seconds=59)
        label = (
            f"本周（{monday.strftime('%Y-%m-%d')} 周一 ~ "
            f"{sunday.strftime('%Y-%m-%d')} 周日）"
        )
        return cls._build(
            monday, sunday,
            period_kind="week", period_label=label, reference=ctx.now,
        )

    @classmethod
    def for_last_week(cls, ctx: "RequestContext") -> "DateRange":
        """上一周（ISO 周，周一为始）。"""
        now = ctx.now
        this_monday = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0,
        )
        last_monday = this_monday - timedelta(days=7)
        last_sunday = last_monday + timedelta(
            days=6, hours=23, minutes=59, seconds=59,
        )
        label = (
            f"上周（{last_monday.strftime('%Y-%m-%d')} 周一 ~ "
            f"{last_sunday.strftime('%Y-%m-%d')} 周日）"
        )
        return cls._build(
            last_monday, last_sunday,
            period_kind="week", period_label=label, reference=ctx.now,
        )

    @classmethod
    def for_this_month(cls, ctx: "RequestContext") -> "DateRange":
        now = ctx.now
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        # 下个月 1 号 - 1 秒
        if start.month == 12:
            next_month = start.replace(year=start.year + 1, month=1)
        else:
            next_month = start.replace(month=start.month + 1)
        end = next_month - timedelta(seconds=1)
        label = f"本月（{start.strftime('%Y年%m月')}）"
        return cls._build(
            start, end,
            period_kind="month", period_label=label, reference=ctx.now,
        )

    @classmethod
    def for_last_month(cls, ctx: "RequestContext") -> "DateRange":
        now = ctx.now
        this_first = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        last_end = this_first - timedelta(seconds=1)
        last_first = last_end.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        label = f"上月（{last_first.strftime('%Y年%m月')}）"
        return cls._build(
            last_first, last_end,
            period_kind="month", period_label=label, reference=ctx.now,
        )

    @classmethod
    def for_last_n_days(cls, ctx: "RequestContext", n: int) -> "DateRange":
        if n < 1:
            raise ValueError(f"n must be >= 1, got {n}")
        now = ctx.now
        start = (now - timedelta(days=n - 1)).replace(
            hour=0, minute=0, second=0, microsecond=0,
        )
        end = now
        label = f"近 {n} 天（{start.strftime('%Y-%m-%d')} ~ {now.strftime('%Y-%m-%d')}）"
        return cls._build(
            start, end,
            period_kind="last_n_days", period_label=label, reference=ctx.now,
        )

    @classmethod
    def custom(
        cls,
        start: datetime,
        end: datetime,
        *,
        reference: Optional[datetime] = None,
    ) -> "DateRange":
        if start >= end:
            raise ValueError(f"start ({start}) must be < end ({end})")
        ref = reference or now_cn()
        s_aw = _to_cn_aware(start)
        e_aw = _to_cn_aware(end)
        label = (
            f"自定义区间（{s_aw.strftime('%Y-%m-%d %H:%M')} ~ "
            f"{e_aw.strftime('%Y-%m-%d %H:%M')}）"
        )
        return cls._build(
            s_aw, e_aw,
            period_kind="custom", period_label=label, reference=ref,
        )


# ────────────────────────────────────────────────────────────────────
# ComparePoint — 同比/环比对比的结构化表示
# ────────────────────────────────────────────────────────────────────


CompareKind = Literal["wow", "mom", "yoy", "spring_aligned", "custom"]


_COMPARE_LABELS: dict[str, tuple[str, str]] = {
    # kind -> (compare_label, semantic_note)
    "wow": (
        "环比上周同期",
        '本系统 "上周" = ISO 周（周一为始）的上一周',
    ),
    "mom": (
        "环比上月同期",
        '"上月同期" = 上个月的同一日；月末日不存在时降级到上月最后一天',
    ),
    "yoy": (
        "同比去年同期",
        '"去年同期" = 去年同月同日；闰年 02-29 在非闰年降级到 02-28',
    ),
    "spring_aligned": (
        "春节对齐同比",
        '以春节为锚点对齐去年同位置（电商专用，默认不启用）',
    ),
    "custom": (
        "自定义区间对比",
        "用户自行指定对比基线",
    ),
}


@dataclass(frozen=True)
class ComparePoint:
    """同比/环比对比的结构化表示。"""

    current: DateRange
    baseline: DateRange
    compare_kind: CompareKind
    compare_label: str
    semantic_note: str

    @classmethod
    def build(
        cls,
        *,
        current: DateRange,
        baseline: DateRange,
        compare_kind: CompareKind,
    ) -> "ComparePoint":
        label, note = _COMPARE_LABELS.get(compare_kind, ("对比", ""))
        return cls(
            current=current,
            baseline=baseline,
            compare_kind=compare_kind,
            compare_label=label,
            semantic_note=note,
        )


# ────────────────────────────────────────────────────────────────────
# RequestContext — 每个请求生命周期内的不可变时间事实
# ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RequestContext:
    """每个请求生命周期内的不可变时间事实。

    在 HTTP/WS handler 入口构造一次，全链路传递，
    禁止下游重新计算 now（避免跨午夜漂移）。
    """

    now: datetime              # aware, ZoneInfo("Asia/Shanghai")
    today: TimePoint           # 即 now 对应的 TimePoint
    user_id: str
    org_id: Optional[str] = None
    tz_name: str = "Asia/Shanghai"
    locale: str = "zh-CN"
    request_id: str = ""

    @classmethod
    def build(
        cls,
        user_id: str,
        org_id: Optional[str] = None,
        request_id: str = "",
    ) -> "RequestContext":
        """入口工厂方法。"""
        n = now_cn()
        return cls(
            now=n,
            today=TimePoint.from_datetime(n, reference=n),
            user_id=user_id,
            org_id=org_id,
            request_id=request_id,
        )

    def for_prompt_injection(self) -> str:
        """生成 system prompt 时间注入字符串。

        格式示例:
            "当前时间：2026-04-10 13:05 周五（中国时区 UTC+8，ISO 第 15 周）"
        """
        return (
            f"当前时间：{self.now.strftime('%Y-%m-%d %H:%M')} "
            f"{self.today.weekday_cn}（UTC+8）"
        )


# ────────────────────────────────────────────────────────────────────
# format_time_header — 工具返回顶部时间块的统一渲染
# ────────────────────────────────────────────────────────────────────


def make_n_days_header(
    *,
    ctx: Optional[RequestContext] = None,
    days: int,
    kind: str = "查询窗口",
) -> str:
    """生成"近 N 天查询"的时间头（供 ERP 本地查询工具复用）。

    缺 ctx 时自动构造一个新的（向后兼容，但会丢失"请求级一致性"）。
    """
    if ctx is None:
        ctx = RequestContext.build(user_id="anonymous")
    range_ = DateRange.for_last_n_days(ctx, days)
    return format_time_header(ctx=ctx, range_=range_, kind=kind)


def format_time_header(
    *,
    ctx: Optional[RequestContext] = None,
    range_: Optional[DateRange] = None,
    kind: str = "查询时间",
) -> str:
    """生成工具返回字符串顶部的结构化时间块。

    Args:
        ctx: 请求上下文（提供"今天"作为锚点）
        range_: 数据时间范围；为 None 时只显示当前时间
        kind: 块名（"查询时间" / "统计区间" / "导出时间" 等）

    Returns:
        多行字符串，例: ::

            [统计区间] 2026-04-10 周五（今天） 00:00–13:05 北京时间
    """
    lines: list[str] = []
    if range_ is not None:
        # 范围块
        s = range_.start
        e = range_.end
        same_day = s.date_str == e.date_str
        if same_day:
            time_range = (
                f"{s.iso[11:16]}–{e.iso[11:16]}"
                if s.iso[11:16] != e.iso[11:16]
                else s.iso[11:16]
            )
            lines.append(
                f"[{kind}] {s.date_str} {s.weekday_cn}"
                f"（{s.relative_label}） {time_range} 北京时间"
            )
        else:
            lines.append(
                f"[{kind}] {s.date_str} {s.weekday_cn} ~ "
                f"{e.date_str} {e.weekday_cn} "
                f"（共 {range_.span_days} 天，工作日 {range_.workday_count} 天）北京时间"
            )
        lines.append(f"[周期类型] {range_.period_kind} · {range_.period_label}")
    elif ctx is not None:
        # 仅当前时间块
        lines.append(
            f"[{kind}] {ctx.now.strftime('%Y-%m-%d %H:%M')} "
            f"{ctx.today.weekday_cn}（今天）北京时间"
        )
    return "\n".join(lines)
