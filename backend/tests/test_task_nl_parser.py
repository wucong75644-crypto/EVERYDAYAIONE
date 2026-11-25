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
from services.scheduler.task_definition_input import TaskDefinitionInputError


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
    @pytest.mark.parametrize('text', ['查销售数据','每周一推业绩','每月1号推月报','今晚10点推订单','每日推销售日报'])
    def test_no_schedule_is_fabricated_after_parse_failure(self, text):
        with pytest.raises(TaskDefinitionInputError, match='已有内容已保留'):
            _fallback(text)


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
            with pytest.raises(TaskDefinitionInputError, match="未能解析任务安排"):
                await parse_task_nl("每天9点推日报")

    @pytest.mark.asyncio
    async def test_empty_input(self):
        with pytest.raises(TaskDefinitionInputError):
            await parse_task_nl("")

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


@pytest.mark.asyncio
async def test_parser_uses_configured_gateway_and_closes_session(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from services.scheduler import task_nl_parser as mod
    seen={}
    async def stream(**kwargs):
        seen.update(kwargs)
        yield SimpleNamespace(content='{"schedule_type":"weekly",')
        yield SimpleNamespace(content='"time_str":"09:00","weekdays":[1]}')
    session=SimpleNamespace(model_id='configured-semantic-model',stream_chat=stream,close=AsyncMock())
    gateway=MagicMock(); gateway.open_chat.return_value=session
    monkeypatch.setattr(mod,'get_model_gateway',lambda:gateway)
    monkeypatch.setattr(mod.settings,'dashscope_api_key','test')
    monkeypatch.setattr(mod.settings,'intent_router_fallback_model','configured-semantic-model')
    result=await mod.parse_task_nl('每个礼拜一早上九点整理销售情况')
    assert result['weekdays']==[1] and result['time_str']=='09:00'
    request=gateway.open_chat.call_args.args[0]
    assert request.model_id=='configured-semantic-model' and request.retry_policy is None
    assert request.timeout==mod.settings.intent_router_timeout
    assert seen['thinking_mode']=='disabled' and seen['max_tokens']==300
    session.close.assert_awaited_once()

@pytest.mark.asyncio
@pytest.mark.parametrize('error',[RuntimeError('429 limit_requests'),RuntimeError('provider unavailable')])
async def test_gateway_failure_preserves_form_without_fabricating_task(monkeypatch,error):
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from services.scheduler import task_nl_parser as mod
    async def stream(**kwargs):
        raise error
        yield
    session=SimpleNamespace(model_id='configured',stream_chat=stream,close=AsyncMock())
    gateway=MagicMock(); gateway.open_chat.return_value=session
    monkeypatch.setattr(mod,'get_model_gateway',lambda:gateway)
    monkeypatch.setattr(mod.settings,'dashscope_api_key','test')
    with pytest.raises(TaskDefinitionInputError,match='已有内容已保留'):
        await mod.parse_task_nl('明早九点整理库存')
    gateway.open_chat.assert_called_once()
    session.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_parser_cancellation_is_not_converted_to_a_new_schedule(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from services.scheduler import task_nl_parser as mod
    async def stream(**kwargs):
        raise asyncio.CancelledError()
        yield
    session=SimpleNamespace(model_id='configured',stream_chat=stream,close=AsyncMock())
    gateway=MagicMock(); gateway.open_chat.return_value=session
    monkeypatch.setattr(mod,'get_model_gateway',lambda:gateway)
    monkeypatch.setattr(mod.settings,'dashscope_api_key','test')
    with pytest.raises(asyncio.CancelledError):
        await mod.parse_task_nl('明早九点整理库存')
    session.close.assert_awaited_once()
