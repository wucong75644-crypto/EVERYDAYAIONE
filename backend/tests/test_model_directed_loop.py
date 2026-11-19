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
    from services.task_execution import content_objects as co
    p.content_controls_available=True
    await execute(r,p,h,req,[call(co.SAVE,{'document':{'title':'完整提示词','items':[{'title':'图1','body':args['prompt'],'materials':[],'options':{'aspect_ratio':'3:4'}}]}},'saved-text')])
    row=co.catalog(r.task_execution_state)[0]
    reference={k:row[k] for k in ('artifact_id','sha256')}|{'item_index':1}
    # This test isolates post-acceptance scheduling; its in-memory history has no persisted text source.
    def no_history_source(*_args): return {}
    monkeypatch.setattr('services.handlers.chat.content_tools.source_text', no_history_source)
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
        if len(rounds)==1:return '', '', [call('generate_image',{'content_source':reference},'image')],set()
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


@pytest.mark.asyncio
@pytest.mark.parametrize('key', ['reference-image-prompts', 'ecommerce-image-prompts'])
@pytest.mark.parametrize('submission_count', [1, 5])
async def test_original_method_saves_before_dispatch_and_reuses_actual_five_item_document(
        key, submission_count, record_property, monkeypatch):
    import os
    from services.skills.selection import SkillSelection
    from services.task_execution.direct_loop import duplicate_image_plan_items, image_plan_item, saved_image_plans
    from services.task_execution.image_binding import image_prompt
    from services.task_execution.methods import ImageMethodDocument
    from services.task_execution.model_directed import SAVE_IMAGE_PLAN
    from tests.test_task_execution_methods import document
    from tests.test_task_execution_original_skills import OriginalSource, original_runtime

    if not os.getenv('TASK_ORIGINAL_SKILLS_EXPORT'):
        pytest.skip('Requires private original published Skill inputs')
    saves = []
    async def save(point, payload):
        saves.append(copy.deepcopy(payload))
        return {'outcome': 'saved'}
    runtime, prepared, handler, request = case(save)
    source = OriginalSource()
    selection = SkillSelection(skill_id=key, revision=source.values[key].revision)
    skills = original_runtime(source, turn_id=runtime.turn_id)
    await skills.initialize(selection=selection)
    assert (await skills.activate_manual(selection))['ok']
    runtime.skill_runtime = skills

    from pathlib import Path
    replay = os.getenv('TASK_ORIGINAL_REFERENCE_PLAN') if key == 'reference-image-prompts' else None
    plan = ImageMethodDocument.model_validate_json(Path(replay).read_text() if replay else document(5))
    from services.task_execution import content_objects as co
    prepared.content_controls_available=True
    original={'title':'原方法五条完整稿','items':[{'title':brief.title,'body':image_prompt(brief),'materials':[],
        'options':{'aspect_ratio':brief.aspect_ratio}} for brief in plan.image_plans]}
    if key == 'reference-image-prompts':
        original['display_prefix'] = '# 参考图判断\n\n' + plan.reference_analysis
        for index, (item, brief) in enumerate(zip(original['items'], plan.image_plans), 1):
            item['display_prefix'] = (f'# 方案{index}｜{brief.title}\n\n'
                f'## 方案内容\n\n{brief.layout}\n\n## 视觉设定\n\n{plan.visual_direction}\n\n'
                f'## 参考图使用方式\n\n{plan.product_facts}')
            item['body'] = f'## 完整生图提示词\n\n{brief.prompt}\n\n## 负面提示词\n\n{brief.negative_prompt}'
    blocks=await execute(runtime,prepared,handler,request,[call(co.SAVE,{'document':original},'save-five')])
    handler._execute_tool_calls.assert_not_awaited()
    state=runtime.task_execution_state
    row=co.catalog(state)[0]
    location={k:row[k] for k in ('artifact_id','sha256')}
    artifact=next(a for a in state.artifacts if a.id==row['artifact_id'])
    assert len(co.load(state,location|{'item_index':1})[1].items)==5
    assert artifact.image_method.skill_key==key
    from services.task_execution.plan_facts import requires_review
    assert not requires_review(state,artifact)
    assert not any(t['function']['name']==SAVE_IMAGE_PLAN for t in schemas(runtime))
    restored=original_runtime(source,task_state=state,turn_id=runtime.turn_id)
    await restored.initialize(saves[-1]['skill_runtime'])
    assert 'generate_image' in restored.effective_allowed_tool_names
    assert key in (restored.resident if key=='reference-image-prompts' else restored.active)
    runtime.skill_runtime=restored
    image,_=co.compile_input(state,'generate_image',{},location|{'item_index':2})
    registry = build_capability_catalog()
    runtime.task_image_resource_resolver = SimpleNamespace(
        prepare_request=AsyncMock(side_effect=lambda args, *_a, **_kw: (args, [], None)))
    dispatch = call('generate_image', image, 'second')
    candidate, errors = stage_native_calls(runtime, prepared, [dispatch], registry)
    assert not errors
    runtime.task_execution_state = candidate
    candidate, errors = await bind_image_recipes(runtime, [dispatch], registry)
    assert not errors
    recipe = next(a for a in candidate.artifacts if 'image.direct.recipe' in a.validations)
    assert json.loads(recipe.inline_text)['method_handoff'] == {'artifact_id': artifact.id, 'image_item_index': 2}
    runtime.task_execution_state = candidate
    changed_ref = call('generate_image', {**image, 'resolution': '2K'}, 'same-item')
    candidate, errors = stage_native_calls(runtime, prepared, [changed_ref], registry)
    assert not errors
    runtime.task_execution_state = candidate
    candidate, errors = await bind_image_recipes(runtime, [changed_ref], registry)
    assert not errors
    assert set(duplicate_image_plan_items(candidate, [dispatch, changed_ref])) == {'same-item'}

    from services.skills.contracts import SkillError
    with monkeypatch.context() as patch:
        patch.setattr(source, 'load', AsyncMock(side_effect=SkillError('SKILL_ACCESS_DENIED')))
        runtime.task_execution_state = candidate
        _, denied = await bind_image_recipes(runtime, [dispatch], registry)
        assert denied['second'][0] == 'TASK_IMAGE_METHOD_UNAVAILABLE'

    from services.agent.agent_result import AgentResult
    from services.task_execution.image_binding import bind_image_call
    from services.task_execution.reducer import reserve_operation, record_operation
    monkeypatch.setattr('services.handlers.chat.execution_engine.compact_tool_context', AsyncMock())
    runtime.task_execution_state = state
    runtime.task_operation_store = SimpleNamespace(registry=registry)
    submitted = []

    async def accept(calls, *_args, **_kwargs):
        results = []
        for raw in calls:
            submitted.append(raw['id'])
            parsed = ToolCall(raw['id'], raw['name'], json.loads(raw['arguments']))
            current = runtime.task_execution_state
            binding = bind_image_call(current, parsed, registry)
            saved_recipe = next(a for a in current.artifacts if a.id == binding.planning_artifact_id)
            assert json.loads(saved_recipe.inline_text)['method_handoff']['artifact_id'] == artifact.id
            current, operation, _ = reserve_operation(current, expected_revision=current.revision,
                step_id=binding.step_id, capability='platform.generate_image', effect_key=binding.effect_key,
                effect_version=1, arguments=json.loads(raw['arguments']), owner_task_id=UUID(runtime.task_id),
                invocation_id=raw['id'])
            operation = operation.model_copy(update={'tool_name': 'generate_image', 'owner_turn_id': UUID(runtime.turn_id)})
            current = TaskState.model_validate({**current.model_dump(),
                'operations': tuple(operation if old.id == operation.id else old for old in current.operations)})
            runtime.task_execution_state = record_operation(current, operation.id, owner_task_id=UUID(runtime.task_id),
                status='accepted', receipt_ref='task:' + str(uuid4()))
            results.append((raw, AgentResult('受控受理回执', metadata={'accepted': True}), False, '受控受理回执'))
        return results

    handler._execute_tool_calls.side_effect = accept
    requested=[call('generate_image',{'content_source':location|{'item_index':i}},f'image-{i}') for i in ([2] if submission_count==1 else range(1,6))]
    await execute(runtime, prepared, handler, request, requested, blocks=blocks)
    assert submitted == [raw['id'] for raw in requested]
    assert len(runtime.task_execution_state.operations) == submission_count
    assert str(runtime.task_execution_state.root_task_id) == runtime.task_id
    for name, value in {
        'skill': key, 'revision': selection.revision, 'planned_items': 5, 'external_dispatches': 0, 'substitute_acceptances': len(submitted),
        'root_before': runtime.task_id, 'root_after_restore': str(state.root_task_id),
        'plan_input': 'saved original plan' if replay else 'controlled five-item document',
        'plan_ref': artifact.reference, 'recipe_ref': recipe.reference,
    }.items():
        record_property(name, value)


@pytest.mark.asyncio
async def test_chat_revision_discovery_and_binding_video_and_word(monkeypatch, tmp_path):
    from docx import Document
    from services.agent.agent_result import AgentResult
    from services.handlers.chat import content_tools
    from services.handlers.chat.tool_loop import prepare_tool_turn

    runtime, prepared, handler, request = case()
    prepared.content_controls_available = True
    revision = '首发八折，今天有效。'
    message_id = str(uuid4())
    parent = {'user_id': request.user_id, 'org_id': None,
              'conversation_id': request.conversation_id, 'base_context_revision': 4}
    rows = [
        {'id': message_id, 'role': 'assistant', 'content': [{'type': 'text', 'text': revision}]},
    ]

    class Query:
        def __init__(self, table):
            self.name = table
        def __getattr__(self, name):
            return lambda *args, **kwargs: self
        def execute(self):
            return SimpleNamespace(data=parent if self.name == 'tasks' else rows)

    handler.db = SimpleNamespace(table=Query)
    monkeypatch.setattr('services.file_upload.upload_to_payload', AsyncMock(return_value=None))
    monkeypatch.setattr(content_tools, '_check_identity', lambda *_: None)
    monkeypatch.setattr('services.tools.runtime_context._check_identity', lambda *_: None)
    monkeypatch.setattr('services.handlers.chat.execution_engine.compact_tool_context', AsyncMock())
    await execute(runtime, prepared, handler, request, [
        call(content_tools.DISCOVER, {'query': '生成短视频'}, 'discover'),
        call(content_tools.READ, {'message_id': message_id, 'text_exact': revision}, 'read'),
    ])
    handler._execute_tool_calls.assert_not_awaited()
    source = json.loads(prepared.messages[-1]['content'])['source']
    assert source['text'] == revision
    names = runtime.checkpoint_base[content_tools.STATE]['names']
    assert 'generate_video' in names
    tools = prepare_tool_turn(core_tools=[], discovered_names=set(names),
        org_id=None, turn=0, messages=[], tool_context=prepared.tool_context,
        permission=prepared.permission, execution_context=prepared.execution_context)
    schema = next(t for t in content_tools.model_tools(tools) if t['function']['name'] == 'generate_video')
    assert 'content_source' in schema['function']['parameters']['properties']

    from services.task_execution import content_objects as co
    await execute(runtime,prepared,handler,request,[call(co.SAVE,{'document':{'title':'修订稿','items':[
        {'title':'最终原文','body':revision,'materials':[],'options':{}}]}},'register')])
    row=co.catalog(runtime.task_execution_state)[0]
    reference={k:row[k] for k in ('artifact_id','sha256')}|{'item_index':1}
    delivered = {}
    async def accept(calls, *_args, **kwargs):
        for raw in calls:
            args = json.loads(raw['arguments'])
            assert 'chat_text_source' not in args
            assert kwargs['chat_source_bindings'][raw['id']][2] == args
            if raw['name'] == 'generate_video':
                delivered['video_prompt'] = args['prompt']
            else:
                from services.sandbox.executor import SandboxExecutor
                output = tmp_path / '下载'
                output.mkdir()
                from services.sandbox.kernel_manager import KernelManager
                manager = KernelManager()
                await manager.start()
                try:
                    sandbox = SandboxExecutor(timeout=30, max_result_chars=8000, workspace_dir=str(tmp_path),
                        staging_dir=str(tmp_path), output_dir=str(output), kernel_manager=manager,
                        conversation_id=request.conversation_id)
                    sandbox._expected_document_text = revision
                    result = await sandbox.execute(args['code'], '生成 Word')
                finally:
                    await manager.shutdown()
                assert result.status == 'success', result.summary
                assert result.emit_payloads and result.emit_payloads[0]['kind'] == 'file'
                delivered['word'] = Document(output / 'revision.docx').paragraphs[0].text
        return [(raw, AgentResult('受控执行结果'), False, '受控执行结果') for raw in calls]

    handler._execute_tool_calls.side_effect = accept
    runtime.task_operation_store = SimpleNamespace(registry=build_capability_catalog())
    await execute(runtime, prepared, handler, request, [
        call('generate_video', {'content_source':reference}, 'video'),
        call('code_execute', {'code': "from docx import Document\ndoc = Document()\ndoc.add_paragraph(chat_source_text)\ndoc.save('下载/revision.docx')\nemit_file('下载/revision.docx')",
            'description':'按修订稿交付Word','content_source':reference}, 'word'),
    ])
    assert delivered == {'video_prompt': revision, 'word': revision}
    bindings=runtime.checkpoint_base[content_tools.STATE]['bindings']
    assert bindings['video']['reference']==bindings['word']['reference']==reference
    rows[0]['content'][0]['text']='历史聊天已变化，但保存的原文不变'
    handler._execute_tool_calls.assert_awaited_once()
    await execute(runtime,prepared,handler,request,[call('generate_video',{'prompt':revision,'chat_text_source':{**source,'mode':'exact'}},'retired')])
    assert 'CONTENT_INPUT_PATH_RETIRED' in str(prepared.messages[-1])
    handler._execute_tool_calls.assert_awaited_once()


@pytest.mark.asyncio
async def test_exact_word_delivery_checks_host_file_before_upload(monkeypatch, tmp_path):
    from docx import Document
    from services.sandbox.executor import SandboxExecutor

    upload = AsyncMock(return_value=None)
    monkeypatch.setattr('services.file_upload.upload_to_payload', upload)
    document = Document()
    document.add_paragraph('模型误用了旧稿')
    path = tmp_path / 'revision.docx'
    document.save(path)
    sandbox = SandboxExecutor(workspace_dir=str(tmp_path), output_dir=str(tmp_path))
    sandbox._expected_document_text = '首发八折，今天有效。'
    payload = {'kind': 'file', 'path': str(path), 'name': path.name}
    with pytest.raises(ValueError, match='CHAT_SOURCE_DOCUMENT_MISMATCH'):
        await sandbox._upload_payload_files([payload])
    upload.assert_not_awaited()
    with pytest.raises(ValueError, match='CHAT_SOURCE_DOCUMENT_MISMATCH'):
        await sandbox._auto_emit_missed({}, [])
    upload.assert_not_awaited()
    document.paragraphs[0].text = sandbox._expected_document_text
    document.save(path)
    await sandbox._upload_payload_files([payload])
    upload.assert_awaited_once()


@pytest.mark.asyncio
async def test_source_failure_preserves_reason_and_stops_automatic_generation(monkeypatch):
    runtime, prepared, handler, request = case()
    runtime.task_operation_store = SimpleNamespace(registry=build_capability_catalog())
    runtime.task_image_resource_resolver = SimpleNamespace(
        prepare_request=AsyncMock(side_effect=ValueError('IMAGE_SOURCE_PROMPT_CHANGED')))
    monkeypatch.setattr('services.handlers.chat.execution_engine.compact_tool_context', AsyncMock())
    await execute(runtime, prepared, handler, request, [
        call('generate_image', {'mode': 'image_to_image', 'prompt': '原提示词',
             'references': [{'message_id': str(uuid4()), 'content_index': 1, 'role': '产品'}],
             'source_prompt': {'message_id': str(uuid4()), 'content_index': 0},
             'aspect_ratio': '1:1'}, 'bad-source'),
    ])
    handler._execute_tool_calls.assert_not_awaited()
    assert 'CONTENT_INPUT_PATH_RETIRED' in str(prepared.messages[-1]['content'])
    assert '停止提交' in handler._tool_result_stop_reason


@pytest.mark.asyncio
@pytest.mark.parametrize('reader', ['new', 'legacy'])
@pytest.mark.parametrize('parallel', [False, True])
async def test_selected_fragments_survive_batch_reads_without_last_item_overwrite(monkeypatch, mock_db, reader, parallel):
    from services.agent.agent_result import AgentResult
    from services.agent.conversation_tool_mixin import ConversationToolMixin
    from services.handlers.chat import content_tools

    runtime, prepared, handler, request = case()
    message_id, input_id = str(uuid4()), str(uuid4())
    texts = ['第一个版本：新品首发。', '第二个版本：限时优惠。']
    mock_db.set_table_data('tasks', [{'id': request.task_id, 'user_id': request.user_id, 'org_id': None,
        'conversation_id': request.conversation_id, 'base_context_revision': 1,
        'input_message_id': input_id, 'request_params': {}}])
    mock_db.set_table_data('messages', [{'id': message_id, 'role': 'assistant', 'org_id': None,
        'conversation_id': request.conversation_id, 'status': 'completed', 'message_kind': 'conversation',
        'context_revision': 1, 'content': [{'type': 'text', 'text': '\n'.join(texts)}]}])
    handler.db = mock_db
    owner = SimpleNamespace(db=mock_db, user_id=request.user_id, workspace_user_id=request.user_id,
        org_id=None, conversation_id=request.conversation_id, task_id=request.task_id,
        context_scope='user', personal_context_allowed=True, execution_mode='interactive')
    seen = []

    async def accept(calls, *_args, **_kwargs):
        results = []
        for raw in calls:
            args = json.loads(raw['arguments'])
            if raw['name'] == 'get_conversation_context':
                output = ConversationToolMixin._exact_image_history(owner, args)
            else:
                seen.append(args['prompt'])
                output = '本地替身'
            results.append((raw, AgentResult(output), False, output))
        return results

    handler._execute_tool_calls.side_effect = accept
    monkeypatch.setattr(content_tools, '_check_identity', lambda *_: None)
    monkeypatch.setattr('services.handlers.chat.execution_engine.compact_tool_context', AsyncMock())
    prepared.content_controls_available = reader == 'new'
    name = content_tools.READ if reader == 'new' else 'get_conversation_context'
    location = {'message_id': message_id} if reader == 'new' else {'message_ids': [message_id]}
    # This test explicitly selects individual fragments, not entire documents.
    reads = ([call(name, {**location, 'text_exact': text}, f'read-{index}') for index, text in enumerate(texts)]
             if parallel else [call(name, {**location, 'text_exacts': texts}, 'read-all')])
    await execute(runtime, prepared, handler, request, reads)
    state=content_tools.checkpoint_state(runtime)
    assert 'selected_sources' not in state and 'bindings' not in state
    assert not seen
