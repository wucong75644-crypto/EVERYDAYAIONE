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
        knowledge_answer_policy=KnowledgeAnswerPolicy('audit_claims','test',policy))
    return p,calls


@pytest.mark.asyncio
async def test_real_loop_repairs_once_then_switches_to_actual_read_evidence(monkeypatch):
    policy,_=guard(monkeypatch)
    p,calls=prepared(policy,[('不能发布的私有猜测',None),('', 'knowledge_discover'),('仅限未拆封。',None)])
    h=SimpleNamespace(org_id=None,_knowledge_run=None,_knowledge_answer_guard=policy)
    sink=CollectingExecutionSink();blocks=[];totals=StreamTotals()
    actual=KnowledgeRun(object(),'snapshot',{'automatic':True})
    actual.revalidate=lambda:None;actual.normalize_citations=lambda t:t
    actual.grounding_sources=lambda:[{'key':'raw','text':'仅限未拆封。'}]
    async def discover(**kwargs):
        assert sink.text=='' and sink.thinking==''
        assert kwargs['calls'][0]['name']=='knowledge_discover'
        h._knowledge_run=actual
    async def audit(candidate,*args,**kwargs):
        assert candidate==['仅限未拆封。'] and sink.text==''
    business=AsyncMock(side_effect=discover)
    monkeypatch.setattr('services.handlers.chat.execution_engine._execute_tools',business)
    monkeypatch.setattr(grounding,'request',AsyncMock(return_value={'requires_sources':True}))
    monkeypatch.setattr(grounding,'audit',audit)
    await _run_loop(handler=h,request=_request(),prepared=p,cancellation_event=asyncio.Event(),
        sink=sink,totals=totals,blocks=blocks,runtime=None)
    assert len(calls)==3 and business.await_count==1
    assert 'knowledge_discover' in {t['function']['name'] for t in calls[0]['tools']}
    assert 'knowledge_read' in {t['function']['name'] for t in calls[-1]['tools']}
    assert p.knowledge_answer_policy.verifier is actual
    assert sink.text=='仅限未拆封。' and sink.thinking==''
    assert '不能发布的私有猜测' not in json.dumps(blocks,ensure_ascii=False)
    assert '不能发布的私有猜测' not in json.dumps([c['messages'] for c in calls],ensure_ascii=False)
    assert all(t['held_for_knowledge'] for t in p.model_turn_timings)


@pytest.mark.asyncio
@pytest.mark.parametrize('active_task',[False,True])
async def test_repeated_private_draft_finishes_safely_without_infinite_retry(monkeypatch,active_task):
    from tests.test_task_execution_model_bridge import setup_case
    policy,_=guard(monkeypatch);p,calls=prepared(policy,[('不应发布的结论',None)]*2)
    runtime,_,h,_=setup_case()
    if not active_task:runtime.task_execution_state=None
    h.org_id=None;h._knowledge_run=None;h._knowledge_answer_guard=policy
    h._calculate_credits=lambda _:0;h._get_conv_source=lambda _:'web';h._pending_emit_payloads=[]
    sink=CollectingExecutionSink();blocks=[];totals=StreamTotals()
    monkeypatch.setattr(grounding,'request',AsyncMock(return_value={'requires_sources':True}))
    await _run_loop(handler=h,request=_request(),prepared=p,cancellation_event=runtime.cancellation_event,
        sink=sink,totals=totals,blocks=blocks,runtime=runtime)
    assert len(calls)==2 and '不应发布的结论' not in json.dumps(blocks,ensure_ascii=False)
    h._execute_tool_calls.assert_not_called()
    saved=runtime._replay_checkpoint_callback.call_args.args[1]
    assert any(m.get('role')=='system' and m['content'].startswith('[Knowledge evidence repair]') for m in saved['messages'])
    if active_task:
        assert runtime.task_execution_state.status=='partial'
        assert saved['_task_execution']['status']=='partial' and saved['checkpoint_kind']=='commit_ready'
        assert '不应发布的结论' not in json.dumps(saved['result_content'],ensure_ascii=False)


@pytest.mark.asyncio
async def test_repair_budget_exhaustion_does_not_sample_extra_wrap_up(monkeypatch):
    policy,_=guard(monkeypatch);p,calls=prepared(policy,[('不应发布的结论',None)],max_turns=1)
    h=SimpleNamespace(org_id=None,_knowledge_run=None,_knowledge_answer_guard=policy,_pending_emit_payloads=[])
    sink=CollectingExecutionSink();blocks=[];totals=StreamTotals();event=asyncio.Event()
    monkeypatch.setattr(grounding,'request',AsyncMock(return_value={'requires_sources':True}))
    synthesis=AsyncMock(side_effect=AssertionError('uncounted model call'))
    monkeypatch.setattr('services.agent.stop_policy.synthesize_wrap_up',synthesis)
    await _run_loop(handler=h,request=_request(),prepared=p,cancellation_event=event,
        sink=sink,totals=totals,blocks=blocks,runtime=None)
    parts=await _finalize_output_parts(handler=h,prepared=p,totals=totals,blocks=blocks,sink=sink,
        cancellation_event=event,runtime=None)
    assert len(calls)==1 and blocks[-1]['task_status']=='partial'
    assert parts[-1].text==blocks[-1]['text'] and '不应发布的结论' not in parts[-1].text
    synthesis.assert_not_awaited()


@pytest.mark.asyncio
async def test_pause_after_independent_decision_preserves_repair_limit_and_context(monkeypatch):
    from services.conversation_commands import ConversationCommand,CommandType
    from services.conversation_state import ConversationPauseRequested
    from tests.test_task_execution_model_bridge import setup_case
    policy,_=guard(monkeypatch);p,calls=prepared(policy,[('不应发布的结论',None)])
    runtime,_,h,_=setup_case();runtime.task_execution_state=None
    h.org_id=None;h._knowledge_run=None;h._knowledge_answer_guard=policy
    class PauseSink(CollectingExecutionSink):
        async def on_block_update(self,block):
            await super().on_block_update(block)
            if '正在补充资料' in block.get('output',''):
                runtime.push(ConversationCommand(command_id='pause-after-source-audit',command_type=CommandType.PAUSE,
                    conversation_id=runtime.conversation_id,task_id=runtime.task_id))
    sink=PauseSink();blocks=[]
    audit=AsyncMock(return_value={'requires_sources':True})
    monkeypatch.setattr(grounding,'request',audit)
    with pytest.raises(ConversationPauseRequested):
        await _run_loop(handler=h,request=_request(),prepared=p,cancellation_event=runtime.cancellation_event,
            sink=sink,totals=StreamTotals(),blocks=blocks,runtime=runtime)
    saved=runtime._replay_checkpoint_callback.call_args.args[1]
    assert saved['next_model_round']==1 and len(calls)==1
    assert saved['messages'][0]=={'role':'user','content':'请按照内部规则处理'}
    assert any(m.get('role')=='system' and m['content'].startswith('[Knowledge evidence repair]') for m in saved['messages'])
    assert '不应发布的结论' not in json.dumps(saved,ensure_ascii=False)
    resumed,again=prepared(policy,[('仍然不应发布',None)])
    resumed.messages=copy.deepcopy(saved['messages'])
    await _run_loop(handler=h,request=_request(),prepared=resumed,cancellation_event=asyncio.Event(),
        sink=CollectingExecutionSink(),totals=StreamTotals(),blocks=[],runtime=None,model_round=1)
    assert len(again)==1 and audit.await_count==2


@pytest.mark.asyncio
async def test_cancellation_during_deferred_audit_stops_provider_and_never_releases(monkeypatch):
    policy,_=guard(monkeypatch);p,calls=prepared(policy,[('不应发布的结论',None)])
    h=SimpleNamespace(org_id=None,_knowledge_run=None,_knowledge_answer_guard=policy)
    started=asyncio.Event();closed=asyncio.Event();event=asyncio.Event()
    async def audit(*args,**kwargs):
        started.set()
        try:await asyncio.Event().wait()
        finally:closed.set()
    monkeypatch.setattr(grounding,'request',audit)
    sink=CollectingExecutionSink();blocks=[]
    task=asyncio.create_task(_run_loop(handler=h,request=_request(),prepared=p,cancellation_event=event,
        sink=sink,totals=StreamTotals(),blocks=blocks,runtime=None))
    await asyncio.wait_for(started.wait(),2);event.set()
    with pytest.raises(asyncio.CancelledError):await task
    assert closed.is_set() and sink.text=='' and sink.thinking=='' and len(calls)==1
    assert not any(m.get('role')=='system' and m['content'].startswith('[Knowledge evidence repair]') for m in p.messages)


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
