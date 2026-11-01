"""Knowledge tool -> normal model delivery; no independent answer reviewer."""
import asyncio
import copy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from services.knowledge_workspace import grounding
from services.knowledge_workspace.chat import KnowledgeRun
from services.handlers.chat.execution_engine import _run_loop, _finalize_output_parts
from services.handlers.chat.execution_sink import CollectingExecutionSink
from services.handlers.chat.stream_session import StreamTotals
from services.agent.execution_budget import ExecutionBudget
from services.tools.runtime_context import catalog_context
from tests.test_chat_execution_engine import _request


def prepared(steps, max_turns=6):
    calls=[]
    async def stream(**kwargs):
        index=len(calls);calls.append(copy.deepcopy(kwargs));text,tool=steps[index]
        yield SimpleNamespace(content=text,thinking_content='模型思考',
            tool_calls=[SimpleNamespace(index=0,id='discover',name=tool,arguments_delta='{"query":"内部规则"}')] if tool else None,
            prompt_tokens=3,completion_tokens=2,credits_consumed=None,finish_reason='stop')
    context=catalog_context(None)
    context=replace(context,feature_flags={**context.feature_flags,'knowledge_auto_discovery':True})
    p=SimpleNamespace(execution_context=context,adapter=SimpleNamespace(stream_chat=stream),stream_kwargs={},
        budget=ExecutionBudget(max_turns=max_turns,wrap_up_turns_reserved=0),core_tools=[],
        messages=[{'role':'user','content':'请按照内部规则处理'}],
        tool_context=SimpleNamespace(discovered_tools=set(),build_context_prompt=lambda:''),
        permission=SimpleNamespace(mode=SimpleNamespace(value='auto'),need_exit_attachment=False,get_reminder=lambda _:''))
    return p,calls


@pytest.mark.asyncio
async def test_model_requested_knowledge_does_not_switch_delivery_into_review(monkeypatch):
    p,calls=prepared([('', 'knowledge_discover'),('仅限未拆封。',None)])
    h=SimpleNamespace(org_id=None,_knowledge_run=None)
    sink=CollectingExecutionSink();blocks=[];totals=StreamTotals()
    actual=KnowledgeRun(object(),'snapshot',{'automatic':True})
    actual.revalidate=lambda:None
    actual.grounding_sources=lambda:pytest.fail('answer must not collect verifier evidence')
    async def discover(**kwargs):
        assert kwargs['calls'][0]['name']=='knowledge_discover'
        h._knowledge_run=actual
    business=AsyncMock(side_effect=discover)
    audit=AsyncMock(side_effect=AssertionError('answer reviewer removed'))
    monkeypatch.setattr('services.handlers.chat.execution_engine._execute_tools',business)
    monkeypatch.setattr(grounding,'request',audit)
    await _run_loop(handler=h,request=_request(),prepared=p,cancellation_event=asyncio.Event(),
        sink=sink,totals=totals,blocks=blocks,runtime=None)
    assert len(calls)==2 and business.await_count==1
    assert h._knowledge_run is actual and sink.text=='仅限未拆封。'
    assert sink.thinking=='模型思考模型思考'
    assert [t['held_for_knowledge'] for t in p.model_turn_timings]==[False,False]
    assert not any(b.get('tool_name')=='knowledge_answer_verification' for b in blocks)
    audit.assert_not_awaited()
    assert not totals.usage.get('grounding_calls')


@pytest.mark.asyncio
@pytest.mark.parametrize('automatic', [False, True])
@pytest.mark.parametrize('legacy_marker', [False, True])
async def test_restored_scope_and_legacy_repair_marker_do_not_reenable_review(monkeypatch,automatic,legacy_marker):
    actual=KnowledgeRun(object(),'snapshot',{'automatic':automatic})
    actual.revalidate=lambda:None
    actual.grounding_sources=lambda:pytest.fail('no answer release gate')
    p,calls=prepared([('本次未找到相关原文，请补充范围。',None)])
    if legacy_marker:
        p.messages.insert(0,{'role':'system','content':'[Knowledge evidence repair]\n旧检查点记录'})
    # Unknown legacy in-memory metadata is inert; restored history is retained.
    p.knowledge_answer_policy=SimpleNamespace(hold_body=True,verifier=actual)
    h=SimpleNamespace(org_id=None,_knowledge_run=actual)
    sink=CollectingExecutionSink();blocks=[]
    audit=AsyncMock(side_effect=AssertionError('no classification/correction call'))
    monkeypatch.setattr(grounding,'request',audit)
    await _run_loop(handler=h,request=_request(),prepared=p,cancellation_event=asyncio.Event(),
        sink=sink,totals=StreamTotals(),blocks=blocks,runtime=None)
    assert len(calls)==1 and sink.text=='本次未找到相关原文，请补充范围。'
    assert sink.thinking=='模型思考'
    if legacy_marker:assert p.messages[0]['content'].startswith('[Knowledge evidence repair]')
    audit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('control',['cancel','pause'])
async def test_control_during_knowledge_answer_stream_keeps_delivered_partial(monkeypatch,control):
    from services.conversation_commands import ConversationCommand,CommandType
    from services.conversation_state import ConversationPauseRequested
    from tests.test_task_execution_model_bridge import setup_case
    actual=KnowledgeRun(object(),'snapshot',{'automatic':False})
    actual.revalidate=lambda:None
    p,calls=prepared([])
    runtime,_,h,_=setup_case();runtime.task_execution_state=None
    h.org_id=None;h._knowledge_run=actual
    started=asyncio.Event();closed=asyncio.Event();event=runtime.cancellation_event
    async def stream(**kwargs):
        try:
            yield SimpleNamespace(content='已读取的内容如下。',thinking_content='',tool_calls=None,
                prompt_tokens=3,completion_tokens=2,credits_consumed=None,finish_reason=None)
            started.set()
            await asyncio.Event().wait()
        finally:closed.set()
    p.adapter.stream_chat=stream
    sink=CollectingExecutionSink();blocks=[];totals=StreamTotals()
    task=asyncio.create_task(_run_loop(handler=h,request=_request(),prepared=p,cancellation_event=event,
        sink=sink,totals=totals,blocks=blocks,runtime=runtime))
    await asyncio.wait_for(started.wait(),2)
    assert sink.text==totals.text=='已读取的内容如下。'
    if control=='cancel':event.set()
    else:runtime.push(ConversationCommand(command_id='pause',command_type=CommandType.PAUSE,
        conversation_id=runtime.conversation_id,task_id=runtime.task_id))
    with pytest.raises(asyncio.CancelledError if control=='cancel' else ConversationPauseRequested):
        await asyncio.wait_for(task,2)
    assert closed.is_set() and sink.text=='已读取的内容如下。'


@pytest.mark.asyncio
async def test_budget_wrap_up_is_not_sent_to_answer_reviewer(monkeypatch):
    actual=KnowledgeRun(object(),'snapshot',{'automatic':True})
    actual.revalidate=lambda:None
    actual.normalize_citations=lambda text:text
    actual.evidence_footer=lambda:''
    actual.grounding_sources=lambda:pytest.fail('wrap-up must not start answer verification')
    h=SimpleNamespace(_knowledge_run=actual,_pending_emit_payloads=[])
    p=SimpleNamespace(budget=SimpleNamespace(stop_reason='max_turns'),adapter=object(),messages=[],text_content='任务')
    sink=CollectingExecutionSink();blocks=[]
    synthesis=AsyncMock(return_value='已完成查询，剩余工作尚未完成。')
    monkeypatch.setattr('services.agent.stop_policy.synthesize_wrap_up',synthesis)
    audit=AsyncMock(side_effect=AssertionError('no answer review in finalization'))
    monkeypatch.setattr(grounding,'request',audit)
    parts=await _finalize_output_parts(handler=h,prepared=p,totals=StreamTotals(),blocks=blocks,
        sink=sink,cancellation_event=asyncio.Event(),runtime=None)
    synthesis.assert_awaited_once();audit.assert_not_awaited()
    assert parts[0].text=='已完成查询，剩余工作尚未完成。'
    assert sink.blocks[0]['text']==parts[0].text


@pytest.mark.asyncio
async def test_revoked_knowledge_stops_before_next_model_request(monkeypatch):
    actual=KnowledgeRun(object(),'snapshot',{'automatic':False})
    def denied():raise PermissionError('KNOWLEDGE_PERMISSION_CHANGED')
    actual.revalidate=denied
    p,calls=prepared([('不得发送的回答',None)])
    h=SimpleNamespace(org_id=None,_knowledge_run=actual)
    sink=CollectingExecutionSink()
    with pytest.raises(PermissionError,match='KNOWLEDGE_PERMISSION_CHANGED'):
        await _run_loop(handler=h,request=_request(),prepared=p,cancellation_event=asyncio.Event(),
            sink=sink,totals=StreamTotals(),blocks=[],runtime=None)
    assert calls==[] and sink.text==''


@pytest.mark.asyncio
async def test_revoked_knowledge_stops_budget_synthesis(monkeypatch):
    actual=KnowledgeRun(object(),'snapshot',{'automatic':False})
    def denied():raise PermissionError('KNOWLEDGE_PERMISSION_CHANGED')
    actual.revalidate=denied
    synthesis=AsyncMock(side_effect=AssertionError('do not send revoked source messages'))
    monkeypatch.setattr('services.agent.stop_policy.synthesize_wrap_up',synthesis)
    with pytest.raises(PermissionError,match='KNOWLEDGE_PERMISSION_CHANGED'):
        await _finalize_output_parts(handler=SimpleNamespace(_knowledge_run=actual),
            prepared=SimpleNamespace(budget=SimpleNamespace(stop_reason='max_turns')),
            totals=StreamTotals(),blocks=[],sink=CollectingExecutionSink(),
            cancellation_event=asyncio.Event(),runtime=None)
    synthesis.assert_not_awaited()
