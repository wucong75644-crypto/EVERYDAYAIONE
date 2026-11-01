"""Host source scope and real incremental read boundary; no paid transports."""
import asyncio
from types import SimpleNamespace

import pytest
from schemas.message import TextPart, ImagePart
from services.handlers.chat.execution_engine import _read_turn
from services.handlers.chat.execution_sink import CollectingExecutionSink
from services.handlers.chat.stream_session import StreamTotals
from services.agent.observability.chat_stream import ModelTurnTiming


@pytest.mark.asyncio
@pytest.mark.parametrize('legacy_gate', [False, True])
async def test_real_read_loop_delivers_before_generation_finishes_even_with_stale_policy(legacy_gate):
    first_received = asyncio.Event()
    continue_generation = asyncio.Event()
    finished = asyncio.Event()

    async def stream(**kwargs):
        yield SimpleNamespace(content='第一段', thinking_content='', tool_calls=None,
                              prompt_tokens=0, completion_tokens=0, credits_consumed=None, finish_reason=None)
        first_received.set()
        await continue_generation.wait()
        yield SimpleNamespace(content='第二段', thinking_content='', tool_calls=None,
                              prompt_tokens=0, completion_tokens=0, credits_consumed=None, finish_reason=None)
        finished.set()

    p = SimpleNamespace(adapter=SimpleNamespace(stream_chat=stream), messages=[], stream_kwargs={},
                        knowledge_answer_policy=SimpleNamespace(hold_body=True,verifier=object()) if legacy_gate else None)
    sink, totals = CollectingExecutionSink(), StreamTotals()
    run = asyncio.create_task(_read_turn(p, [], asyncio.Event(), sink, totals, [], None))
    try:
        await asyncio.wait_for(first_received.wait(), 2)
        assert not finished.is_set()
        assert sink.text == '第一段'
        assert totals.text == sink.text
    finally:
        continue_generation.set()
        result = await asyncio.wait_for(run, 2)
    assert result[0] == '第一段第二段'
    assert sink.text == '第一段第二段'
    assert p.model_turn_timings[0]['body_chunks'] == 2
    assert p.model_turn_timings[0]['held_for_knowledge'] is False
    assert 'model_turn_timings' not in totals.usage  # Billing contract is unchanged.


def test_model_timing_uses_one_clock_and_fixed_scalar_state():
    clock = SimpleNamespace(now=0.0)
    timing = ModelTurnTiming(clock=lambda: clock.now)
    clock.now = .7
    timing.on_text()
    clock.now = .9
    timing.on_text()
    clock.now = 1.2
    assert timing.summary(held=False, review=False) == {
        'first_body_ms': 700.0, 'elapsed_ms': 1200.0, 'body_chunks': 2,
        'max_body_gap_ms': 200.0, 'held_for_knowledge': False, 'review': False,
    }


@pytest.mark.parametrize('question', ['你好', '帮我分析一下', '换一种表达方式', '公司规则是什么', '写个程序查询库存'])
def test_wording_does_not_create_an_answer_gate_or_hide_model_requested_roots(question):
    from services.knowledge_workspace.scope_selection import select
    corpus = [{'id': 'personal', 'kind': 'personal', 'title': '个人资料'}]
    assert select(corpus, question)[0] == corpus


@pytest.mark.asyncio
async def test_authenticated_steer_changes_question_without_automatic_retrieval(monkeypatch):
    from unittest.mock import AsyncMock
    from services.handlers.chat.execution_engine import _inject_steer_messages, _refresh_knowledge_after_steer
    from tests.test_chat_execution_engine import _request
    prepared = SimpleNamespace(messages=[], text_content='原任务')
    bound = object()
    handler = SimpleNamespace(_knowledge_run=bound)
    prepare = AsyncMock()
    monkeypatch.setattr('services.knowledge_workspace.runtime.prepare_knowledge_run', prepare)
    _inject_steer_messages(prepared.messages, ['按公司规范改写'], prepared=prepared)
    await _refresh_knowledge_after_steer(handler, _request(), prepared)
    assert prepared.knowledge_question == '按公司规范改写'
    assert handler._knowledge_run is bound
    prepare.assert_not_awaited()
