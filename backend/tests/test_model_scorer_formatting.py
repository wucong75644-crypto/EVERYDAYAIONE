"""模型评分时间格式工具测试。"""

from datetime import datetime, timezone

from services.model_scorer import _format_period


def test_format_period_supports_datetime_and_string_values() -> None:
    row_dt = {
        "period_start": datetime(2026, 3, 3, tzinfo=timezone.utc),
        "period_end": datetime(2026, 3, 10, tzinfo=timezone.utc),
    }
    assert _format_period(row_dt) == ("2026-03-03", "2026-03-10")

    row_str = {
        "period_start": "2026-03-03T00:00:00+00:00",
        "period_end": "2026-03-10T00:00:00+00:00",
    }
    assert _format_period(row_str) == ("2026-03-03", "2026-03-10")
