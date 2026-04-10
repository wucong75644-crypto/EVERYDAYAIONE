"""通用工具模块。

时间事实层 SSOT：
- :mod:`utils.time_context` — RequestContext / TimePoint / DateRange / ComparePoint
- :mod:`utils.relative_label` — 中文相对时间标签（今天/昨天/上周X/N天前）
- :mod:`utils.holiday` — 中国法定节假日 + 调休 + 春节窗口判断

设计文档: ``docs/document/TECH_ERP时间准确性架构.md``
"""

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

__all__ = [
    "CN_TZ",
    "WEEKDAYS_CN",
    "ComparePoint",
    "DateRange",
    "RequestContext",
    "TimePoint",
    "format_time_header",
    "make_n_days_header",
    "now_cn",
]
