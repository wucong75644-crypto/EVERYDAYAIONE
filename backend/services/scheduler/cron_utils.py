"""Cron 表达式解析工具

支持标准 5 段 cron: minute hour dom month dow

设计文档: docs/document/TECH_定时任务心跳系统.md §4.5
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from core.exceptions import ValidationError

from croniter import croniter


def calc_next_run(
    cron_expr: str,
    tz: str = "Asia/Shanghai",
    base: Optional[datetime] = None,
) -> datetime:
    """计算下次执行时间（UTC）

    Args:
        cron_expr: 5 段 cron 表达式，如 "0 9 * * *"
        tz: 用户时区，默认上海
        base: 基准时间（默认当前时间）

    Returns:
        下次执行时间，UTC 时区
    """
    local_tz = ZoneInfo(tz)
    if base is None:
        base_local = datetime.now(local_tz)
    else:
        base_local = base.astimezone(local_tz) if base.tzinfo else base.replace(tzinfo=local_tz)

    cron = croniter(cron_expr, base_local)
    next_local = cron.get_next(datetime)
    return next_local.astimezone(timezone.utc)


def parse_cron_readable(cron_expr: str) -> str:
    """cron 表达式转人类可读描述

    Examples:
        "0 9 * * *"  → "每天 09:00"
        "0 9 * * 1"  → "每周一 09:00"
        "0 9 1 * *"  → "每月 1 日 09:00"
        "*/30 * * * *" → "每 30 分钟"
    """
    parts = cron_expr.split()
    if len(parts) != 5:
        return f"cron: {cron_expr}"

    minute, hour, dom, month, dow = parts

    # 时间字符串
    if minute.startswith("*/"):
        return f"每 {minute[2:]} 分钟"

    if hour == "*":
        return f"每小时 {minute.zfill(2)} 分"

    try:
        time_str = f"{int(hour):02d}:{int(minute):02d}"
    except ValueError:
        return f"cron: {cron_expr}"

    # 频率
    if dom == "*" and dow == "*":
        return f"每天 {time_str}"
    if dow != "*" and dom == "*":
        weekdays = {
            "0": "日", "1": "一", "2": "二", "3": "三",
            "4": "四", "5": "五", "6": "六", "7": "日",
        }
        return f"每周{weekdays.get(dow, dow)} {time_str}"
    if dom != "*" and dow == "*":
        return f"每月 {dom} 日 {time_str}"

    return f"cron: {cron_expr}"


def validate_cron(cron_expr: str) -> bool:
    """校验 cron 表达式合法性"""
    try:
        croniter(cron_expr)
        return True
    except (ValueError, KeyError):
        return False


def compose_cron(
    schedule_type: str,
    time_str: str,
    weekdays: Optional[list[int]] = None,
    day_of_month: Optional[int] = None,
) -> Optional[str]:
    """
    把结构化频率配置组装成 cron 表达式。

    Args:
        schedule_type: 'once' / 'daily' / 'weekly' / 'monthly' / 'cron'
        time_str: 'HH:MM' 格式（24 小时制），once 类型不需要
        weekdays: 周几列表，cron dow 语义 [0=日, 1=一, ..., 6=六]，
                  weekly 类型必填
        day_of_month: 1-31，monthly 类型必填

    Returns:
        cron 表达式字符串；'once' 类型返回 None（用 run_at 字段调度）

    Raises:
        ValueError: 参数缺失或格式不合法

    Examples:
        compose_cron('daily', '09:00') → '0 9 * * *'
        compose_cron('weekly', '09:00', weekdays=[1, 3, 5]) → '0 9 * * 1,3,5'
        compose_cron('monthly', '09:00', day_of_month=15) → '0 9 15 * *'
        compose_cron('once', ...) → None
    """
    if schedule_type == "once":
        return None

    if schedule_type == "cron":
        # 由调用方直接传 cron_expr，本函数不处理
        raise ValidationError("cron 类型应直接使用用户传入的 cron_expr，不调用 compose_cron")

    # 解析 HH:MM
    if not time_str or ":" not in time_str:
        raise ValidationError(f"time_str 必须是 HH:MM 格式，收到: {time_str}")
    try:
        hh_str, mm_str = time_str.split(":", 1)
        hh = int(hh_str)
        mm = int(mm_str)
    except (ValueError, AttributeError):
        raise ValidationError(f"time_str 解析失败: {time_str}")
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ValidationError(f"time_str 超出范围: {time_str}")

    if schedule_type == "daily":
        return f"{mm} {hh} * * *"

    if schedule_type == "weekly":
        if not weekdays:
            raise ValidationError("weekly 类型必须指定 weekdays")
        # 校验 + 排序去重
        valid = sorted({int(d) for d in weekdays if 0 <= int(d) <= 6})
        if not valid:
            raise ValidationError(f"weekdays 无有效值: {weekdays}")
        dow_str = ",".join(str(d) for d in valid)
        return f"{mm} {hh} * * {dow_str}"

    if schedule_type == "monthly":
        if day_of_month is None:
            raise ValidationError("monthly 类型必须指定 day_of_month")
        if not (1 <= day_of_month <= 31):
            raise ValidationError(f"day_of_month 超出范围: {day_of_month}")
        return f"{mm} {hh} {day_of_month} * *"

    raise ValidationError(f"未知的 schedule_type: {schedule_type}")
