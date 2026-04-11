"""
task_nl_parser 测试

只测纯函数 _extract_json / _fallback / parse_task_nl 主路径，
LLM 调用走 mock。
"""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from services.scheduler.task_nl_parser import (  # noqa: E402
    _extract_json,
    _fallback,
    parse_task_nl,
)


# ════════════════════════════════════════════════════════════════
# _extract_json
# ════════════════════════════════════════════════════════════════

class TestExtractJson:
    def test_pure_json(self):
        result = _extract_json('{"name": "test", "schedule_type": "daily"}')
        assert result == {"name": "test", "schedule_type": "daily"}

    def test_json_in_markdown(self):
        text = '```json\n{"name": "test"}\n```'
        result = _extract_json(text)
        assert result == {"name": "test"}

    def test_json_with_explanation_around(self):
        text = '解析结果如下:\n{"name": "test"}\n以上是结果'
        result = _extract_json(text)
        assert result == {"name": "test"}

    def test_invalid_json(self):
        assert _extract_json("not a json") is None

    def test_empty(self):
        assert _extract_json("") is None


# ════════════════════════════════════════════════════════════════
# _fallback
# ════════════════════════════════════════════════════════════════

class TestFallback:
    def test_daily_default(self):
        r = _fallback("查销售数据")
        assert r["schedule_type"] == "daily"
        assert r["time_str"] == "09:00"

    def test_weekly_keyword(self):
        r = _fallback("每周一推业绩")
        assert r["schedule_type"] == "weekly"
        assert r["name"] == "周报推送"

    def test_monthly_keyword(self):
        r = _fallback("每月1号推月报")
        assert r["schedule_type"] == "monthly"

    def test_once_keyword(self):
        r = _fallback("今晚10点推订单")
        assert r["schedule_type"] == "once"

    def test_daily_keyword(self):
        r = _fallback("每日推销售日报")
        assert r["name"] == "每日报表"


# ════════════════════════════════════════════════════════════════
# parse_task_nl 完整流程（mock LLM）
# ════════════════════════════════════════════════════════════════

class TestParseTaskNl:
    @pytest.mark.asyncio
    async def test_llm_success(self):
        fake_llm_result = {
            "name": "今日订单",
            "prompt": "汇总今日付款订单并推送",
            "schedule_type": "once",
            "time_str": "22:00",
            "run_at": "2099-04-12T22:00:00+08:00",
        }
        with patch(
            "services.scheduler.task_nl_parser._call_llm",
            new=AsyncMock(return_value=fake_llm_result),
        ):
            result = await parse_task_nl("今天晚上10点推订单情况")
        assert result["schedule_type"] == "once"
        assert result["run_at"] == "2099-04-12T22:00:00+08:00"
        assert result["name"] == "今日订单"

    @pytest.mark.asyncio
    async def test_llm_failure_falls_back(self):
        with patch(
            "services.scheduler.task_nl_parser._call_llm",
            new=AsyncMock(return_value=None),
        ):
            result = await parse_task_nl("每天9点推日报")
        # 兜底
        assert result["schedule_type"] == "daily"
        assert result["name"] == "每日报表"

    @pytest.mark.asyncio
    async def test_empty_input(self):
        result = await parse_task_nl("")
        # 不应崩溃
        assert "schedule_type" in result

    @pytest.mark.asyncio
    async def test_llm_partial_response_filled_with_defaults(self):
        """LLM 只返回部分字段 → 用 defaults 填上"""
        fake_llm_result = {
            "schedule_type": "weekly",
            "weekdays": [1],
            "time_str": "09:00",
        }
        with patch(
            "services.scheduler.task_nl_parser._call_llm",
            new=AsyncMock(return_value=fake_llm_result),
        ):
            result = await parse_task_nl("每周一开会")
        assert result["schedule_type"] == "weekly"
        assert "name" in result  # 兜底填了
        assert "prompt" in result
