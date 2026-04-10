"""中国法定节假日 + 调休 + 春节窗口判断。

依赖 ``chinese-calendar`` 库（含国务院发布的调休/补班日）。

启动时检查：库覆盖年份 < 当前+1 时打 warning 日志，提醒升级。
设计文档: ``docs/document/TECH_ERP时间准确性架构.md`` §5/§14
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from loguru import logger

try:
    import chinese_calendar  # type: ignore
    _CC_AVAILABLE = True
except ImportError:  # pragma: no cover
    _CC_AVAILABLE = False
    logger.error(
        "chinese-calendar not installed | "
        "节假日判断将降级到周一-周五是工作日的简化规则。"
        "请运行 pip install chinese-calendar==1.11.0",
    )


# 春节日期表（公历）— chinese-calendar 不直接暴露春节查询，
# 用一张小表覆盖近 10 年。日期以正月初一公历对应日期为准。
_SPRING_FESTIVAL_DATES: dict[int, date] = {
    2020: date(2020, 1, 25),
    2021: date(2021, 2, 12),
    2022: date(2022, 2, 1),
    2023: date(2023, 1, 22),
    2024: date(2024, 2, 10),
    2025: date(2025, 1, 29),
    2026: date(2026, 2, 17),
    2027: date(2027, 2, 6),
    2028: date(2028, 1, 26),
    2029: date(2029, 2, 13),
    2030: date(2030, 2, 3),
}

# 春节窗口宽度（春节前后 ±N 天视为春节窗口）
_SPRING_WINDOW_DAYS = 15


def is_workday(d: date) -> bool:
    """是否工作日（含调休补班日；排除法定假日和周末）。"""
    if not _CC_AVAILABLE:
        return d.weekday() < 5  # 降级：周一-周五
    try:
        return chinese_calendar.is_workday(d)
    except NotImplementedError:
        # 库未覆盖该年份
        return d.weekday() < 5


def is_holiday(d: date) -> bool:
    """是否法定节假日（含周末扩展为假期的情况；不含普通无名号周末）。

    chinese-calendar 的 is_holiday 包含所有非工作日，包括普通周末。
    本函数仅返回"被国务院命名的法定假日"（清明/春节/国庆等），
    不包含未被命名的普通周末。
    """
    if not _CC_AVAILABLE:
        return False
    try:
        on_holiday, name = chinese_calendar.get_holiday_detail(d)
        return bool(on_holiday and name)
    except (NotImplementedError, Exception):
        return False


def get_holiday_name(d: date) -> Optional[str]:
    """获取法定节假日中文名（如"清明节"）。

    返回 None 表示该日不是法定假日。
    """
    if not _CC_AVAILABLE:
        return None
    try:
        on_holiday, name = chinese_calendar.get_holiday_detail(d)
        if on_holiday and name:
            # chinese-calendar 返回英文枚举名，做简单中文映射
            return _HOLIDAY_NAME_CN.get(name, name)
        return None
    except (NotImplementedError, Exception):
        return None


def is_lieu_workday(d: date) -> bool:
    """是否调休补班日（周末但被国务院规定为工作日）。"""
    if not _CC_AVAILABLE:
        return False
    if d.weekday() < 5:
        return False  # 工作日不算调休
    try:
        return chinese_calendar.is_workday(d)
    except (NotImplementedError, Exception):
        return False


def is_spring_festival_window(d: date, window_days: int = _SPRING_WINDOW_DAYS) -> bool:
    """是否在春节前后 ±N 天窗口内（电商业务专用）。"""
    sf = _SPRING_FESTIVAL_DATES.get(d.year)
    if sf is None:
        return False
    delta = abs((d - sf).days)
    return delta <= window_days


def get_spring_festival_date(year: int) -> Optional[date]:
    """获取指定年份的春节公历日期（正月初一）。"""
    return _SPRING_FESTIVAL_DATES.get(year)


def check_coverage_at_startup() -> None:
    """启动时检查 chinese-calendar 库覆盖年份是否够用。

    库覆盖年份 < 当前年份 + 1 时打 warning 日志，
    提醒维护者在每年 12 月升级 chinese-calendar 版本。
    """
    if not _CC_AVAILABLE:
        logger.warning(
            "[time-arch] chinese-calendar 未安装，节假日判断降级到 mon-fri",
        )
        return
    from utils.time_context import now_cn
    current_year = now_cn().year
    needed_year = current_year + 1
    try:
        # 用 12 月 31 日探测下一年是否被覆盖
        chinese_calendar.is_workday(date(needed_year, 12, 31))
    except (NotImplementedError, Exception) as e:  # noqa: BLE001
        logger.warning(
            f"[time-arch] chinese-calendar 未覆盖 {needed_year} 年（{e}），"
            f"请升级到最新版本：pip install -U chinese-calendar",
        )
        return
    logger.info(
        f"[time-arch] chinese-calendar 覆盖年份 OK | "
        f"current={current_year} needed={needed_year}",
    )


# chinese-calendar 内部 holiday name → 中文映射
_HOLIDAY_NAME_CN: dict[str, str] = {
    "New Year's Day": "元旦",
    "Spring Festival": "春节",
    "Tomb-sweeping Day": "清明节",
    "Labour Day": "劳动节",
    "Dragon Boat Festival": "端午节",
    "National Day": "国庆节",
    "Mid-autumn Festival": "中秋节",
}
