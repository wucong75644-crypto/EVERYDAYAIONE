"""中文相对时间标签计算。

把日期转换成"今天/昨天/前天/N天前/上周X/上月/去年"等相对描述，
让 LLM 直接复述，禁止自己推算。

设计文档: ``docs/document/TECH_ERP时间准确性架构.md`` §5
"""

from __future__ import annotations

from datetime import date

from utils.time_context import WEEKDAYS_CN  # noqa: E402  (循环导入由工厂方法保证)


def compute_relative_label(target: date, reference: date) -> str:
    """计算 ``target`` 相对于 ``reference``（"今天"）的中文标签。

    规则（按优先级匹配）：

    - 同一天 → "今天"
    - 前 1 天 → "昨天"
    - 前 2 天 → "前天"
    - 后 1 天 → "明天"
    - 后 2 天 → "后天"
    - 同一 ISO 周内（前/后 6 天内同周）→ "本周X"
    - 上一 ISO 周 → "上周X"
    - 下一 ISO 周 → "下周X"
    - 同月不同周（前 7-30 天）→ "N天前"
    - 上月同月 → "上月X日"
    - 同年内更远 → "Y月X日"
    - 去年 → "去年Y月X日"
    - 更早 → "YYYY年Y月X日"
    """
    if target == reference:
        return "今天"

    delta_days = (target - reference).days
    if delta_days == -1:
        return "昨天"
    if delta_days == -2:
        return "前天"
    if delta_days == 1:
        return "明天"
    if delta_days == 2:
        return "后天"

    # 短星期名（去掉 WEEKDAYS_CN 的"周"前缀），用于"上周X / 下周X / 本周X"拼接
    def _short(wd: int) -> str:
        return WEEKDAYS_CN[wd][1:]  # "周五" → "五"

    target_iso = target.isocalendar()
    ref_iso = reference.isocalendar()

    # 同一 ISO 周
    if (target_iso.year, target_iso.week) == (ref_iso.year, ref_iso.week):
        return f"本周{_short(target.weekday())}"

    # 上一/下一 ISO 周（同 ISO 年，week 差 1）
    if target_iso.year == ref_iso.year:
        wd = target.weekday()
        if target_iso.week == ref_iso.week - 1:
            return f"上周{_short(wd)}"
        if target_iso.week == ref_iso.week + 1:
            return f"下周{_short(wd)}"

    # 跨 ISO 年但 week 相邻（年末/年初的特殊情况）
    if target_iso.year == ref_iso.year - 1 and ref_iso.week == 1:
        # 上一 ISO 年的最后一周
        return f"上周{_short(target.weekday())}"
    if target_iso.year == ref_iso.year + 1 and target_iso.week == 1:
        return f"下周{_short(target.weekday())}"

    # N 天前/后（30 天内）
    if -30 <= delta_days < 0:
        return f"{-delta_days}天前"
    if 0 < delta_days <= 30:
        return f"{delta_days}天后"

    # 同年同月（罕见但保留）
    if target.year == reference.year and target.month == reference.month:
        return f"{target.day}日"

    # 上月 / 同年其他月
    if target.year == reference.year:
        if target.month == reference.month - 1:
            return f"上月{target.day}日"
        if target.month == reference.month + 1:
            return f"下月{target.day}日"
        return f"{target.month}月{target.day}日"

    # 去年
    if target.year == reference.year - 1:
        return f"去年{target.month}月{target.day}日"
    if target.year == reference.year + 1:
        return f"明年{target.month}月{target.day}日"

    # 更早/更远
    return f"{target.year}年{target.month}月{target.day}日"
