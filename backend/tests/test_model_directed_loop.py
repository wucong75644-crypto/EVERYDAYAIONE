"""Exercise the existing loop and Actor saves, without provider/generation IO."""
import asyncio,copy,json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID,uuid4
import pytest
from services.handlers.chat.execution_engine import _execute_tools,execute_chat,_build_replay_context
from services.handlers.chat.execution_sink import CollectingExecutionSink
from services.task_execution.direct_loop import (stage_native_calls,advance_results,end_candidate,bind_image_recipes,schemas,context_message)
from services.task_execution.model_directed import record_calls,call_binding
from services.task_execution.contracts import TaskError,TaskState
from services.task_execution.progress import project
from services.tools import ToolCall
from services.tools.catalog import build_capability_catalog
from tests.test_model_directed_records import bootstrap
from tests.test_skill_runtime_actor import actor,prepared,handler
from tests.test_task_execution_model_bridge import call
from tests.test_chat_execution_engine import _request


def case(callback=None):
    b=bootstrap();r=actor(callback);r.task_bootstrap=b
    r.task_id=str(b.task_id);r.conversation_id=str(b.conversation_id);r.turn_id=str(uuid4())
    p=prepared();p.messages=[{'role':'system','content':'完整记忆、原上下文和已完成成果'}, {'role':'user','content':b.original_goal}]
    p.execution_context=replace(p.execution_context,actor_user_id=str(b.actor_user_id),workspace_owner_id=str(b.actor_user_id),
        org_id=None,task_id=r.task_id,conversation_id=r.conversation_id,
        feature_flags={**p.execution_context.feature_flags,'chat_image_async_enabled':True})
    req=replace(_request(),task_id=r.task_id,conversation_id=r.conversation_id,user_id=str(b.actor_user_id))
    return r,p,handler(),req


async def execute(r,p,h,req,calls,blocks=None):
    blocks=[] if blocks is None else blocks
    await _execute_tools(runtime=r,prepared=p,handler=h,request=req,calls=calls,blocks=blocks,
        turn=0,turn_text='',next_model_round=1,previewed_call_ids=set(),
        cancellation_event=r.cancellation_event,sink=CollectingExecutionSink())
    return blocks


@pytest.mark.asyncio
async def test_plan_before_first_operation_is_optional_and_saved_in_original_context():
    saved=[]
    async def save(point,payload):saved.append(copy.deepcopy(payload));return {'outcome':'saved'}
    r,p,h,req=case(save);old=copy.deepcopy(p.messages)
    await execute(r,p,h,req,[call('update_task_plan',{'steps':[{'step':'分析原图','status':'in_progress'}]},'notes')])
    assert r.task_execution_state is None
    assert saved[-1]['_task_direct_plan']['steps'][0]['step']=='分析原图'
    assert saved[-1]['messages'][:len(old)]==old
    assert saved[-1]['messages'][-1]['tool_call_id']=='notes'
    h._execute_tool_calls.assert_not_awaited()


@pytest.mark.asyncio
async def test_independent_plan_and_read_share_one_batch_without_a_model_barrier(monkeypatch):
    r,p,h,req=case();seen=[]
    async def run(calls,*args,**kwargs):
        seen.extend(calls);return [(c,'真实查询结果',False,'真实查询结果') for c in calls]
    h._execute_tool_calls.side_effect=run
    monkeypatch.setattr('services.handlers.chat.execution_engine.compact_tool_context',AsyncMock())
    calls=[call('update_task_plan',{'steps':[{'step':'查询资料','status':'in_progress'}]},'notes'),
        call('web_search',{'query':'商品资料'},'search')]
    await execute(r,p,h,req,calls)
    assert [c['id'] for c in seen]==['search']
    assistants=[m for m in p.messages if m.get('tool_calls')]
    assert len(assistants)==1 and len(assistants[0]['tool_calls'])==2
    assert {m['tool_call_id'] for m in p.messages if m.get('role')=='tool'}=={'notes','search'}
    assert r.task_execution_state is None and not r.task_requirement_review


@pytest.mark.asyncio
async def test_unconfirmed_plan_save_rolls_back_without_dispatch_or_context_loss():
    async def failed(*args):raise RuntimeError('save failed')
    r,p,h,req=case(failed);before=copy.deepcopy(p.messages)
    with pytest.raises(RuntimeError,match='save failed'):
        await execute(r,p,h,req,[call('update_task_plan',{'steps':[]},'notes')])
    assert p.messages==before and r.task_direct_plan is None and r.task_execution_state is None
    h._execute_tool_calls.assert_not_awaited()


@pytest.mark.asyncio
async def test_generic_task_uses_main_loop_without_mandatory_proposal_or_blind_review(monkeypatch):
    from core.config import get_settings
    from schemas.message import TextPart
    from tests.test_task_execution_bootstrap import bootstrap as input_bootstrap
    settings=get_settings().model_copy(update={'skill_catalog_enabled':False,'skill_runtime_enabled':False,
        'skill_recommendations_enabled':False,'ecom_image_planning_enabled':False})
    monkeypatch.setattr('core.config.get_settings',lambda:settings)
    b,state,anchor,_=input_bootstrap();b=replace(b,model_directed=True,require_coverage_review=False)
    r,p,h,req=case();r.task_bootstrap=b;r.task_id=str(b.task_id);r.conversation_id=str(b.conversation_id)
    p.execution_context=replace(p.execution_context,actor_user_id=str(b.actor_user_id),workspace_owner_id=str(b.actor_user_id),
        task_id=r.task_id,conversation_id=r.conversation_id)
    history=copy.deepcopy(p.messages)
    monkeypatch.setattr('services.handlers.chat.execution_engine.prepare_chat_stream',AsyncMock(return_value=p))
    req=replace(req,task_id=r.task_id,conversation_id=r.conversation_id,user_id=str(b.actor_user_id),
        content=[TextPart(text='先整理资料，再给出完整文字方案')],context_anchor=anchor)
    h.org_id=None;h.db=object();rounds=[]
    async def model(current,tools,*args,**kwargs):
        assert not kwargs.get('task_review')
        names={t['function']['name'] for t in tools}
        assert 'update_task_plan' in names and not {'start_task_execution','propose_task_revision','complete_task_step'}&names
        assert [m for m in current.messages if m in history]==history
        rounds.append(kwargs['model_round'])
        if len(rounds)==1:return '', '', [call('update_task_plan',{'steps':[{'step':'整理完整方案','status':'in_progress'}]},'notes')],set()
        return '完整文字方案已经交付。','',[],set()
    monkeypatch.setattr('services.handlers.chat.execution_engine._read_turn',model)
    await execute_chat(handler=h,request=req,runtime=r)
    assert len(rounds)==2 and r.task_execution_state is None
    assert r.task_requirement_review is None and r.task_plan_reviews is None
    h._execute_tool_calls.assert_not_awaited()


def test_native_batch_binds_distinct_effects_without_predeclared_dag_and_keeps_erp_off():
    r,p,h,req=case();catalog=build_capability_catalog()
    calls=[call('generate_image',{'mode':'text_to_image','prompt':'原提示词','aspect_ratio':'3:4'},'image-'+str(i)) for i in range(2)]
    candidate,errors=stage_native_calls(r,p,calls,catalog)
    assert not errors and candidate is not None and len(candidate.steps)==2
    assert candidate.working_plan is None and not candidate.method_bindings and not candidate.operations
    from services.task_execution.image_binding import bind_image_call
    bindings=[bind_image_call(candidate,ToolCall(c['id'],c['name'],json.loads(c['arguments'])),catalog) for c in calls]
    assert bindings[0].effect_key!=bindings[1].effect_key
    assert project(candidate)['image_jobs']=={'completed':0,'total':2}
    assert 'erp_execute' not in {t['function']['name'] for t in schemas(r)}


@pytest.mark.asyncio
async def test_invalid_resume_is_correctable_without_transferring_or_dispatching():
    r,p,h,req=case()
    await execute(r,p,h,req,[call('resume_task_execution',{'relation':'continue','evidence':['不存在的来源']},'resume')])
    result=next(m for m in p.messages if m.get('tool_call_id')=='resume')
    assert json.loads(result['content'])['code']=='TASK_FOLLOWUP_SOURCE_CHANGED'
    assert r.task_execution_state is None and getattr(r,'task_followup_candidate',None) is None
    h._execute_tool_calls.assert_not_awaited()


def test_rejected_unstarted_job_is_retired_but_accepted_job_is_never_discarded():
    from services.task_execution.direct_loop import retire_not_dispatched
    from tests.test_model_directed_records import operation_state
    r,p,h,req=case();c=ToolCall('call','generate_image',{'mode':'text_to_image','prompt':'场景'})
    s=record_calls(None,r.task_bootstrap,[c],build_capability_catalog())
    retired=retire_not_dispatched(s,{'call'})
    assert retired.retired_call_steps==(s.steps[0].id,)
    assert retired.requirements==s.requirements and retired.original_goal==s.original_goal
    for status in ('reserved','accepted','uncertain','completed'):
        _,busy=operation_state(status)
        assert retire_not_dispatched(busy,{'first'}) is busy


def test_followup_prefers_fresh_plan_without_overwriting_it_on_later_restore():
    from services.task_execution.direct_loop import restore_plan
    r,p,h,req=case()
    payload={'_task_direct_plan_owner':'previous-owner','_task_direct_plan':{'steps':[{'step':'旧工作','status':'pending'}]},
        '_task_followup':{'source_root_task_id':str(r.task_bootstrap.task_id)},
        '_task_followup_input_checkpoint':{'_task_direct_plan_owner':r.task_id,
            '_task_direct_plan':{'steps':[{'step':'最新用户工作','status':'in_progress'}]}}}
    restore_plan(r,payload)
    assert r.task_direct_plan.steps[0].step=='最新用户工作'
    payload.update(_task_direct_plan_owner=r.task_id,_task_direct_plan={'steps':[{'step':'随后完成的工作','status':'completed'}]})
    restore_plan(r,payload)
    assert r.task_direct_plan.steps[0].step=='随后完成的工作'


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome',['saved','lost','pause'])
async def test_resolved_argument_checkpoint_keeps_native_fencing_and_pause(outcome):
    from services.task_execution.native_writes import NativeWriteLifecycle
    from services.conversation_state import ConversationPauseRequested,ConversationStopRequested
    from services.tools.policy import ToolDecision
    from services.tools import ToolContext
    snapshots=[]
    async def save(point,payload):
        snapshots.append(copy.deepcopy(payload))
        if outcome=='lost':raise RuntimeError('commit unknown')
        return {'outcome':'saved'}
    r,p,h,req=case(save)
    raw=ToolCall('delete','file_delete',{'files':'原文件.txt'})
    resolved=ToolCall('delete','file_delete',{'files':['原文件.txt']})
    r.task_execution_state=record_calls(None,r.task_bootstrap,[raw],build_capability_catalog())
    h._actor_runtime=r
    lifecycle=NativeWriteLifecycle(h,p.execution_context,registry=build_capability_catalog())
    if outcome=='pause':
        from services.conversation_commands import ConversationCommand,CommandType
        r.push(ConversationCommand(command_id='pause',command_type=CommandType.PAUSE,
            conversation_id=r.conversation_id,task_id=r.task_id,turn_id=r.turn_id))
    if outcome=='lost':
        with pytest.raises(ConversationStopRequested):await lifecycle.bind_resolved_arguments(raw,resolved,p.execution_context,None)
        assert not r.task_execution_state.resolved_call_bindings
    elif outcome=='pause':
        with pytest.raises(ConversationPauseRequested):await lifecycle.bind_resolved_arguments(raw,resolved,p.execution_context,None)
        assert r.task_execution_state.resolved_call_bindings
    else:
        await lifecycle.bind_resolved_arguments(raw,resolved,p.execution_context,None)
        assert r.task_execution_state.resolved_call_bindings
    assert snapshots and snapshots[0]['_task_execution']['resolved_call_bindings'][0]['call_id']=='delete'


@pytest.mark.asyncio
async def test_async_image_acceptance_leaves_main_model_free_to_finish_independent_work(monkeypatch):
    from core.config import get_settings
    from schemas.message import TextPart
    from services.agent.agent_result import AgentResult
    from services.task_execution.reducer import reserve_operation,record_operation
    from services.task_execution.image_binding import bind_image_call
    from tests.test_task_execution_bootstrap import bootstrap as input_bootstrap
    settings=get_settings().model_copy(update={'skill_catalog_enabled':False,'skill_runtime_enabled':False,
        'skill_recommendations_enabled':False,'ecom_image_planning_enabled':False})
    monkeypatch.setattr('core.config.get_settings',lambda:settings)
    b,_,anchor,_=input_bootstrap();b=replace(b,model_directed=True,require_coverage_review=False)
    r,p,h,req=case();r.task_bootstrap=b;r.task_id=str(b.task_id);r.conversation_id=str(b.conversation_id)
    p.execution_context=replace(p.execution_context,actor_user_id=str(b.actor_user_id),workspace_owner_id=str(b.actor_user_id),
        task_id=r.task_id,conversation_id=r.conversation_id)
    req=replace(req,task_id=r.task_id,conversation_id=r.conversation_id,user_id=str(b.actor_user_id),
        content=[TextPart(text='提交图片后，再整理无需等图片的文字清单')],context_anchor=anchor)
    monkeypatch.setattr('services.handlers.chat.execution_engine.prepare_chat_stream',AsyncMock(return_value=p))
    monkeypatch.setattr('services.handlers.chat.execution_engine.compact_tool_context',AsyncMock())
    h.org_id=None;h.db=object();rounds=[]
    args={'mode':'text_to_image','prompt':'用户完整原提示词','aspect_ratio':'3:4'}
    r.task_operation_store=SimpleNamespace(registry=build_capability_catalog())
    r.task_image_resource_resolver=SimpleNamespace(prepare_request=AsyncMock(return_value=(args,[],None)))
    monkeypatch.setattr('services.task_execution.image_resources.create_resolver',lambda *a:r.task_image_resource_resolver)
    async def run(calls,*args_,**kwargs):
        raw=calls[0];parsed=ToolCall(raw['id'],raw['name'],json.loads(raw['arguments']))
        bound=bind_image_call(r.task_execution_state,parsed,build_capability_catalog())
        s,op,_=reserve_operation(r.task_execution_state,expected_revision=r.task_execution_state.revision,
            step_id=bound.step_id,capability='platform.generate_image',effect_key=bound.effect_key,effect_version=1,
            arguments=json.loads(raw['arguments']),owner_task_id=b.task_id,invocation_id=parsed.call_id)
        op=op.model_copy(update={'tool_name':'generate_image','owner_turn_id':UUID(r.turn_id)})
        s=TaskState.model_validate({**s.model_dump(),'operations':(op,)})
        r.task_execution_state=record_operation(s,op.id,owner_task_id=b.task_id,status='accepted',receipt_ref='task:'+str(uuid4()))
        result=AgentResult('原生图片任务已受理',metadata={'accepted':True,'completed':False})
        return [(raw,result,False,'已受理')]
    h._execute_tool_calls.side_effect=run
    async def model(current,tools,*args_,**kwargs):
        assert not kwargs.get('task_review')
        rounds.append(kwargs['model_round'])
        if len(rounds)==1:return '', '', [call('generate_image',args,'image')],set()
        assert r.task_execution_state.status=='active'
        assert r.task_execution_state.operations, [m for m in current.messages if m.get('role')=='tool']
        assert r.task_execution_state.operations[0].status=='accepted'
        assert any(m.get('role')=='tool' and m.get('tool_call_id')=='image' for m in current.messages)
        return '图片正在处理；独立文字清单已整理。','',[],set()
    monkeypatch.setattr('services.handlers.chat.execution_engine._read_turn',model)
    outcome=await execute_chat(handler=h,request=req,runtime=r)
    assert len(rounds)==2 and r.task_execution_state.status=='waiting_dependencies'
    assert any('独立文字清单' in getattr(part,'text','') for part in outcome.parts)
    h._execute_tool_calls.assert_awaited_once()


@pytest.mark.asyncio
async def test_code_only_keeps_native_journal_without_publishing_a_work_plan(monkeypatch):
    r,p,h,req=case()
    monkeypatch.setattr('services.handlers.chat.execution_engine.compact_tool_context',AsyncMock())
    async def compute(calls,*args,**kwargs):return [(c,'代码计算结束',False,'代码计算结束') for c in calls]
    h._execute_tool_calls.side_effect=compute
    blocks=await execute(r,p,h,req,[call('code_execute',{'code':'print(1)'},'compute')])
    assert r.task_execution_state is not None
    assert r.task_execution_state.call_bindings[0].tool_name=='code_execute'
    assert not any(b.get('type')=='task_progress' for b in blocks)
    assert r.task_execution_state.working_plan is None


@pytest.mark.asyncio
async def test_plan_only_marker_is_saved_before_publication_without_native_state():
    from services.task_execution.progress import plan_marker
    saved=[]
    async def save(point,payload):saved.append(copy.deepcopy(payload));return {'outcome':'saved'}
    r,p,h,req=case(save)
    blocks=await execute(r,p,h,req,[call('update_task_plan',{'steps':[
        {'step':'读取表格','status':'pending'},{'step':'形成报告','status':'pending'}]},'plan')])
    marker=next(b for b in blocks if b['type']=='task_progress')
    assert saved[-1]['content_blocks'][-1]==marker
    assert marker['root_task_id']==r.task_id and len(marker['plan_revision'])==64
    assert r.task_execution_state is None
    assert plan_marker(blocks,r) is None
    previous_revision=marker['plan_revision']
    blocks=await execute(r,p,h,req,[call('update_task_plan',{'steps':[]},'clear')],blocks)
    assert next(b for b in blocks if b['type']=='task_progress')['plan_revision']!=previous_revision
    assert r.task_direct_plan.steps==() and r.task_execution_state is None


@pytest.mark.asyncio
async def test_failed_plan_checkpoint_publishes_no_marker_and_restores_original_context():
    async def fail(*args):raise RuntimeError('checkpoint failed')
    r,p,h,req=case(fail);before=copy.deepcopy(p.messages);blocks=[]
    sink=CollectingExecutionSink();sink.on_block=AsyncMock()
    with pytest.raises(RuntimeError,match='checkpoint failed'):
        await _execute_tools(runtime=r,prepared=p,handler=h,request=req,
            calls=[call('update_task_plan',{'steps':[{'step':'读取表格','status':'pending'}]},'notes')],
            blocks=blocks,turn=0,turn_text='',next_model_round=1,previewed_call_ids=set(),
            cancellation_event=r.cancellation_event,sink=sink)
    assert blocks==[] and p.messages==before and r.task_direct_plan is None
    sink.on_block.assert_not_awaited()
