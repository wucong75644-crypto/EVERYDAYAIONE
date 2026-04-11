"""Cron 表达式解析工具

支持标准 5 段 cron: minute hour dom month dow

设计文档: docs/document/TECH_定时任务心跳系统.md §4.5
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

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
