"""On-demand default knowledge, real loop delivery, no live providers."""
from services.knowledge_workspace.answer_policy import KnowledgeAnswerPolicy
import asyncio
import copy
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
import httpx
import pytest
from services.knowledge_workspace import grounding
from services.knowledge_workspace.answer_policy import DeferredKnowledgeScope, KnowledgeEvidenceRequired
from services.knowledge_workspace.chat import KnowledgeRun
from services.handlers.chat.execution_engine import _run_loop, _finalize_output_parts
from services.handlers.chat.execution_sink import CollectingExecutionSink
from services.handlers.chat.stream_session import StreamTotals
from services.agent.execution_budget import ExecutionBudget
from services.tools.runtime_context import catalog_context
from tests.test_chat_execution_engine import _request


def guard(monkeypatch):
    checks=[]
    monkeypatch.setattr(DeferredKnowledgeScope,'revalidate',lambda _:checks.append('authorized'))
    return DeferredKnowledgeScope(object(),'conv','input',()),checks


@pytest.mark.asyncio
@pytest.mark.parametrize('value,expected', [({'requires_sources':False},'普通回复'),
    ({'requires_sources':True},'repair'),({'requires_sources':'false'},'不足'),({},'不足'),
    (None,'不足'),('outage','不足')])
async def test_independent_policy_never_uses_answer_model_self_exemption(monkeypatch,value,expected):
    policy,checks=guard(monkeypatch);calls=[]
    async def audit(messages,**kwargs):
        calls.append(messages)
        assert json.loads(messages[1]['content'])['source_context']=={'has_user_images':True}
        if value=='outage':raise httpx.ConnectError('offline')
        return value
    monkeypatch.setattr(grounding,'request',audit)
    if expected=='repair':
        with pytest.raises(KnowledgeEvidenceRequired):
            await grounding.release_answer(policy,'普通回复','任务',source_context={'has_user_images':True})
    else:
        answer=await grounding.release_answer(policy,'普通回复','任务',source_context={'has_user_images':True})
        assert expected in answer
    assert len(calls)==1 and checks


def prepared(policy,steps,max_turns=6):
    calls=[]
    async def stream(**kwargs):
        index=len(calls);calls.append(copy.deepcopy(kwargs));text,tool=steps[index]
        yield SimpleNamespace(content=text,thinking_content='不展示的内部思考',
            tool_calls=[SimpleNamespace(index=0,id='discover',name=tool,arguments_delta='{"query":"内部规则"}')] if tool else None,
            prompt_tokens=3,completion_tokens=2,credits_consumed=None,finish_reason='stop')
    context=catalog_context(None)
    context=replace(context,feature_flags={**context.feature_flags,'knowledge_auto_discovery':True})
    p=SimpleNamespace(execution_context=context,adapter=SimpleNamespace(stream_chat=stream),stream_kwargs={},
        budget=ExecutionBudget(max_turns=max_turns,wrap_up_turns_reserved=0),core_tools=[],
        messages=[{'role':'user','content':'请按照内部规则处理'}],
        tool_context=SimpleNamespace(discovered_tools=set(),build_context_prompt=lambda:''),
        permission=SimpleNamespace(mode=SimpleNamespace(value='auto'),need_exit_attachment=False,get_reminder=lambda _:''),
        knowledge_answer_policy=KnowledgeAnswerPolicy('audit_claims','test',policy) if policy is not None else None)
    return p,calls


@pytest.mark.asyncio
async def test_model_requested_knowledge_switches_next_round_to_real_scope(monkeypatch):
    p,calls=prepared(None,[('', 'knowledge_discover'),('仅限未拆封。',None)])
    h=SimpleNamespace(org_id=None,_knowledge_run=None)
    sink=CollectingExecutionSink();blocks=[];totals=StreamTotals()
    actual=KnowledgeRun(object(),'snapshot',{'automatic':True})
    actual.revalidate=lambda:None;actual.normalize_citations=lambda t:t
    actual.grounding_sources=lambda:[{'key':'raw','text':'仅限未拆封。'}]
    async def discover(**kwargs):
        assert kwargs['calls'][0]['name']=='knowledge_discover'
        h._knowledge_run=actual
    async def audit(candidate,*args,**kwargs):
        assert candidate==['仅限未拆封。'] and sink.text==''
    business=AsyncMock(side_effect=discover)
    monkeypatch.setattr('services.handlers.chat.execution_engine._execute_tools',business)
    monkeypatch.setattr(grounding,'audit',audit)
    await _run_loop(handler=h,request=_request(),prepared=p,cancellation_event=asyncio.Event(),
        sink=sink,totals=totals,blocks=blocks,runtime=None)
    assert len(calls)==2 and business.await_count==1
    assert p.knowledge_answer_policy.verifier is actual
    assert sink.text=='仅限未拆封。'
    assert sink.thinking=='不展示的内部思考'  # Only the pre-binding tool round streams thinking.
    assert [t['held_for_knowledge'] for t in p.model_turn_timings]==[False,True]


@pytest.mark.asyncio
async def test_bound_scope_without_evidence_does_not_publish_guess_or_loop(monkeypatch):
    actual=KnowledgeRun(object(),'snapshot',{'automatic':False})
    actual.revalidate=lambda:None
    actual.grounding_sources=lambda:[]
    p,calls=prepared(None,[('不应发布的结论',None)])
    h=SimpleNamespace(org_id=None,_knowledge_run=actual)
    sink=CollectingExecutionSink();blocks=[]
    audit=AsyncMock(side_effect=AssertionError('No source-free classification needed'))
    monkeypatch.setattr(grounding,'request',audit)
    await _run_loop(handler=h,request=_request(),prepared=p,cancellation_event=asyncio.Event(),
        sink=sink,totals=StreamTotals(),blocks=blocks,runtime=None)
    assert len(calls)==1 and '不应发布的结论' not in sink.text
    assert sink.text and sink.thinking==''
    audit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('control',['cancel','pause'])
async def test_control_during_actual_scope_audit_never_releases_draft(monkeypatch,control):
    from services.conversation_commands import ConversationCommand,CommandType
    from services.conversation_state import ConversationPauseRequested
    from tests.test_task_execution_model_bridge import setup_case
    actual=KnowledgeRun(object(),'snapshot',{'automatic':False})
    actual.revalidate=lambda:None
    actual.grounding_sources=lambda:[{'key':'real','text':'原始规则'}]
    actual.normalize_citations=lambda text:text
    p,calls=prepared(None,[('不应发布的结论',None)])
    runtime,_,h,_=setup_case();runtime.task_execution_state=None
    h.org_id=None;h._knowledge_run=actual
    started=asyncio.Event();closed=asyncio.Event();event=runtime.cancellation_event
    async def audit(*args,**kwargs):
        started.set()
        try:await asyncio.Event().wait()
        finally:closed.set()
    monkeypatch.setattr(grounding,'audit',audit)
    sink=CollectingExecutionSink();blocks=[]
    task=asyncio.create_task(_run_loop(handler=h,request=_request(),prepared=p,cancellation_event=event,
        sink=sink,totals=StreamTotals(),blocks=blocks,runtime=runtime))
    await asyncio.wait_for(started.wait(),2)
    if control=='cancel':event.set()
    else:runtime.push(ConversationCommand(command_id='pause',command_type=CommandType.PAUSE,
        conversation_id=runtime.conversation_id,task_id=runtime.task_id))
    with pytest.raises(asyncio.CancelledError if control=='cancel' else ConversationPauseRequested):await task
    assert closed.is_set() and sink.text=='' and sink.thinking=='' and len(calls)==1


@pytest.mark.asyncio
async def test_last_budgeted_tool_cannot_leave_wrap_up_on_stale_stream_policy(monkeypatch):
    # A knowledge tool can run on the last iteration, without another model
    # boundary to bind its newly acquired scope before budget synthesis.
    actual=KnowledgeRun(object(),'snapshot',{'automatic':True})
    actual.normalize_citations=lambda text:text
    actual.evidence_footer=lambda:''
    h=SimpleNamespace(_knowledge_run=actual,_pending_emit_payloads=[])
    p=SimpleNamespace(knowledge_answer_policy=KnowledgeAnswerPolicy('stream','host_self_contained'),
        budget=SimpleNamespace(stop_reason='max_turns'),adapter=object(),messages=[],text_content='任务')
    sink=CollectingExecutionSink();blocks=[]
    monkeypatch.setattr('services.agent.stop_policy.synthesize_wrap_up',AsyncMock(return_value='未经核验的结论'))
    async def audit(run,text,*args,**kwargs):
        assert run is actual and sink.text=='' and not blocks
        assert text=='未经核验的结论'
        return '核验后的收尾'
    checked=AsyncMock(side_effect=audit)
    monkeypatch.setattr('services.handlers.chat.execution_engine._verify_knowledge_answer',checked)
    parts=await _finalize_output_parts(handler=h,prepared=p,totals=StreamTotals(),blocks=blocks,
        sink=sink,cancellation_event=asyncio.Event(),runtime=None)
    checked.assert_awaited_once()
    assert parts[0].text=='核验后的收尾' and sink.blocks[0]['text']=='核验后的收尾'
    assert '未经核验的结论' not in json.dumps(blocks,ensure_ascii=False)
