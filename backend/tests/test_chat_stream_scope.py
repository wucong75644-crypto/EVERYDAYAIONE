"""Host source scope and real incremental read boundary; no paid transports."""
from services.knowledge_workspace.answer_policy import KnowledgeAnswerPolicy
import asyncio
from types import SimpleNamespace

import pytest
from schemas.message import TextPart, ImagePart
from services.handlers.chat.execution_engine import _read_turn
from services.handlers.chat.execution_sink import CollectingExecutionSink
from services.handlers.chat.stream_session import StreamTotals
from services.agent.observability.chat_stream import ModelTurnTiming


@pytest.mark.asyncio
@pytest.mark.parametrize('held', [False, True])
async def test_real_read_loop_delivers_before_generation_finishes_only_without_gate(held):
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
                        knowledge_answer_policy=KnowledgeAnswerPolicy('audit_claims','test',object()) if held else None)
    sink, totals = CollectingExecutionSink(), StreamTotals()
    run = asyncio.create_task(_read_turn(p, [], asyncio.Event(), sink, totals, [], None))
    try:
        await asyncio.wait_for(first_received.wait(), 2)
        assert not finished.is_set()
        assert sink.text == ('' if held else '第一段')
        assert totals.text == sink.text
    finally:
        continue_generation.set()
        result = await asyncio.wait_for(run, 2)
    assert result[0] == '第一段第二段'
    assert sink.text == ('' if held else '第一段第二段')
    assert p.model_turn_timings[0]['body_chunks'] == 2
    assert p.model_turn_timings[0]['held_for_knowledge'] is held
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
    from services.knowledge_workspace.answer_policy import bind_answer_policy
    from services.knowledge_workspace.scope_selection import select
    corpus = [{'id': 'personal', 'kind': 'personal', 'title': '个人资料'}]
    assert select(corpus, question)[0] == corpus
    assert bind_answer_policy(SimpleNamespace(), SimpleNamespace()).mode == 'stream'


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

@pytest.mark.asyncio
async def test_tool_round_is_not_presented_as_answer_audit_and_final_body_stays_held(monkeypatch):
    from unittest.mock import AsyncMock
    from services.agent.execution_budget import ExecutionBudget
    from services.tools.runtime_context import catalog_context
    from services.handlers.chat.execution_engine import _run_loop
    from tests.test_chat_execution_engine import _request
    rounds=[]
    sink=CollectingExecutionSink()
    blocks=[]
    async def stream(**kwargs):
        rounds.append(len(rounds))
        calls=[SimpleNamespace(index=0,id='lookup',name='erp_agent',arguments_delta='{}')] if len(rounds)==1 else None
        yield SimpleNamespace(content='未经核验的草稿',thinking_content='内部思考',tool_calls=calls,
            prompt_tokens=3,completion_tokens=2,credits_consumed=None,finish_reason='stop')
    prepared=SimpleNamespace(execution_context=catalog_context(None),
        adapter=SimpleNamespace(stream_chat=stream),stream_kwargs={},
        budget=ExecutionBudget(max_turns=4,wrap_up_turns_reserved=0),core_tools=[],messages=[],
        tool_context=SimpleNamespace(discovered_tools=set(),build_context_prompt=lambda:''),
        permission=SimpleNamespace(mode=SimpleNamespace(value='auto'),need_exit_attachment=False,get_reminder=lambda _:''),
        knowledge_answer_policy=KnowledgeAnswerPolicy('audit_claims','test',object()))
    async def tools(**kwargs):
        assert not any(b.get('tool_name')=='knowledge_answer_verification' for b in blocks)
        assert sink.text=='' and sink.thinking==''
    async def verify(run,draft,*args,**kwargs):
        assert draft=='未经核验的草稿' and sink.text=='' and sink.thinking==''
        stages=[b for b in blocks if b.get('tool_name')=='knowledge_answer_verification']
        assert len(stages)==1 and stages[0]['status']=='running'
        return '已核验的回答'
    execute=AsyncMock(side_effect=tools)
    audit=AsyncMock(side_effect=verify)
    monkeypatch.setattr('services.handlers.chat.execution_engine._execute_tools',execute)
    monkeypatch.setattr('services.handlers.chat.execution_engine._verify_knowledge_answer',audit)
    from services.knowledge_workspace.chat import KnowledgeRun
    bound = KnowledgeRun(object(), 'snapshot', {'automatic': False})
    await _run_loop(handler=SimpleNamespace(org_id=None, _knowledge_run=bound),request=_request(),prepared=prepared,
        cancellation_event=asyncio.Event(),sink=sink,totals=StreamTotals(),blocks=blocks,runtime=None)
    assert len(rounds)==2
    execute.assert_awaited_once();audit.assert_awaited_once()
    assert sink.text=='已核验的回答'
    assert [b['status'] for b in blocks if b.get('tool_name')=='knowledge_answer_verification']==['completed']
