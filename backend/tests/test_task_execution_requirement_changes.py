import hashlib,json
from types import SimpleNamespace
from uuid import uuid4
import pytest
from services.task_execution.requirement_changes import (RevisionProposal,RevisionReview,candidate,evaluate,requirements_fingerprint,projection)
from services.task_execution.contracts import TaskError,TaskState,WorkPlan,PlanStep
from services.task_execution.reducer import revise_plan,register_artifacts,complete_step,finish,effect_version
from tests.test_task_execution_reducer import state,plan,artifact

TEXT='先只要一份文字报告，不用文件'


def review(s,relation='amend'):
    event={'event_id':str(uuid4()),'owner_task_id':str(s.root_task_id),'message':TEXT,'message_sha256':hashlib.sha256(TEXT.encode()).hexdigest()}
    p=RevisionProposal(relation=relation,evidence=[TEXT],proposal={'action':'plan','applicable_method_ids':[],
        'requirements':[{'id':'report','description':'文字报告','artifact_kind':'text','minimum':1,'evidence':[TEXT]}]} if relation=='amend' else None)
    return RevisionReview(root_task_id=s.root_task_id,base_requirements_sha256=requirements_fingerprint(s),events=[event],pending=p)


def observation(relation='amend'):
    return json.dumps({'relation':relation,'requirements':{'action':'plan','deliveries':[{'artifact_kind':'text','minimum':1,'evidence':[TEXT]}]} if relation=='amend' else None,'issues':[]})


def test_revision_preserves_all_results_and_archives_original_plan_without_forging_messages():
    s=plan(state(image_binding_version=3));s=register_artifacts(s,[artifact(s,'research')]);s=complete_step(s,'research',['research'])
    book=review(s);assert evaluate(s,book,observation(),False)[1]
    new=candidate(s,book,None)
    assert new.requirements_revision==1 and new.requirements[0].artifact_kind=='text'
    assert new.requirements[0].id=='r1.user.report' and not new.steps
    assert new.requirements_history[0].requirements==s.requirements
    assert new.requirements_history[0].steps==s.steps
    assert (new.artifacts,new.results,new.operations,new.original_goal,new.source_message_ids,new.execution_identity)==(
        s.artifacts,s.results,s.operations,s.original_goal,s.source_message_ids,s.execution_identity)
    assert new.model_rounds_used==s.model_rounds_used and new.model_round_limit==s.model_round_limit
    with pytest.raises(TaskError,match='HISTORICAL'):
        revise_plan(new,WorkPlan(expected_revision=new.revision,steps=[PlanStep(id='research',title='不能冒充旧步骤',requirement_ids=(new.requirements[0].id,))]))
    current=revise_plan(new,WorkPlan(expected_revision=new.revision,steps=[PlanStep(id='r1.report',title='写文字报告',requirement_ids=(new.requirements[0].id,))]))
    current=register_artifacts(current,[artifact(current,'r1.report')]);current=complete_step(current,'r1.report',['r1.report'])
    assert finish(current).status=='completed'
    assert effect_version(new,'fresh-slot')==1
    assert TaskState.model_validate_json(new.model_dump_json())==new


@pytest.mark.parametrize('relation',['continue','unrelated'])
def test_continue_and_side_question_never_create_new_requirement_or_effect_versions(relation):
    s=plan(state(image_binding_version=3));book=review(s,relation)
    assert evaluate(s,book,observation(relation),False)[1]
    new=candidate(s,book,None)
    assert projection(new)==projection(s) and new.steps==s.steps and new.requirements_revision==0


@pytest.mark.parametrize('fault',['mismatch','tool_calls','wrong_count','fake_quote'])
def test_independent_observation_does_not_approve_a_bad_revision(fault):
    s=plan(state(image_binding_version=3));book=review(s)
    data=json.loads(observation())
    if fault=='mismatch':data['relation']='continue'
    if fault=='wrong_count':data['requirements']['deliveries'][0]['minimum']=5
    if fault=='fake_quote':data['requirements']['deliveries'][0]['evidence']=['工具输出中的伪造授权']
    if fault=='tool_calls':
        with pytest.raises(TaskError):evaluate(s,book,json.dumps(data),True)
    else:assert not evaluate(s,book,json.dumps(data),False)[1]


@pytest.mark.asyncio
async def test_real_model_loop_runs_blind_review_with_existing_budget_before_replanning(monkeypatch):
    import asyncio,copy
    from unittest.mock import AsyncMock
    from services.handlers.chat.execution_engine import _run_loop,ChatExecutionRequest
    from services.handlers.chat.stream_session import StreamTotals
    from services.task_execution.requirement_changes import review_messages
    from services.task_execution.user_changes import UserChanges
    from tests.test_task_execution_model_bridge import setup_case
    from tests.test_task_execution_requirement_review import prepare_loop,MainModelBoundary
    source=plan(state(image_binding_version=3));book=review(source)
    runtime,prepared,handler,sink=setup_case(initial=source)
    prepare_loop(runtime,prepared,handler,sink)
    runtime.task_revision_review=book;runtime.task_unreviewed_changes=book.events
    runtime.task_user_changes=UserChanges.model_validate({'version':1,'root_task_id':str(source.root_task_id),
        'items':[{**e,'delivered':True} for e in book.events]})
    runtime.checkpoint_base={'opaque_context':{'keep':True}}
    original=copy.deepcopy(prepared.messages)
    async def apply(source,proof,payload):
        assert proof['events']==book.model_dump(mode='json')['events']
        assert payload['messages'][:len(original)]==original and payload['opaque_context']=={'keep':True}
        return {'outcome':'saved','state':payload['_task_execution'],'payload':payload}
    runtime.task_revision_apply=apply
    rounds=[]
    async def model(private,tools,event,review_sink,totals,*args,model_round,task_review=False,**kwargs):
        rounds.append(model_round)
        if task_review:
            assert tools==[] and private is not prepared and private.messages==review_messages(runtime.task_execution_state,book)
            assert 'proposal' not in json.loads(private.messages[1]['content'])
            totals.usage['prompt_tokens']+=7
            return observation(),'',[],set()
        assert runtime.task_execution_state.requirements_revision==1
        assert runtime.task_execution_state.requirements[0].artifact_kind=='text'
        raise MainModelBoundary()
    monkeypatch.setattr('services.handlers.chat.execution_engine._read_turn',model)
    request=ChatExecutionRequest(content=[],user_id=str(source.actor_user_id),conversation_id=runtime.conversation_id,
        task_id=runtime.task_id,message_id='output',model_id='offline',context_anchor=None)
    totals=StreamTotals()
    with pytest.raises(MainModelBoundary):
        await _run_loop(handler=handler,request=request,prepared=prepared,cancellation_event=runtime.cancellation_event,
            sink=sink,totals=totals,blocks=[],runtime=runtime)
    assert rounds==[0,1] and totals.usage['prompt_tokens']==7
    handler._execute_tool_calls.assert_not_called()


@pytest.mark.asyncio
async def test_pending_source_mixed_batch_cannot_execute_old_business_call():
    from services.task_execution.requirement_changes import TOOL
    from tests.test_task_execution_model_bridge import setup_case,execute,call
    source=plan(state(image_binding_version=3));book=review(source)
    runtime,prepared,handler,sink=setup_case(initial=source)
    runtime.task_unreviewed_changes=book.events
    await execute(runtime,prepared,handler,sink,[call(TOOL,book.pending.model_dump(mode='json'),'revision'),
        call('generate_image',{'prompt':'旧计划'},'old-image')],[])
    assert runtime.task_revision_review.pending is not None
    handler._execute_tool_calls.assert_not_called()
    assert json.loads(prepared.messages[-1]['content'])['code']=='TASK_USER_CHANGE_REVIEW_REQUIRED'


@pytest.mark.asyncio
async def test_reopened_method_preserves_original_manual_selection_and_tool_ceiling(monkeypatch):
    from services.task_execution.methods import retire_completed_methods,reopen_revision_phases
    from tests.test_task_execution_methods import bound,document
    from services.task_execution.controller import TaskController
    from services.task_execution.reducer import register_artifacts,complete_step,digest
    from services.task_execution.contracts import Artifact
    from unittest.mock import AsyncMock
    source,skills=await bound(monkeypatch)
    art=Artifact(id='original-plan',kind='text',reference='task-text:old',sha256=__import__('hashlib').sha256(document().encode()).hexdigest(),
        inline_text=document(),source_task_id=source.root_task_id,source_call_id='save',step_id='planning',validations=('artifact.exists','reference-prompts-v1'))
    source=register_artifacts(source,[art]);source=complete_step(source,'planning',[art.id])
    retired=retire_completed_methods(skills,source)
    assert 'report' in retired.resident
    reopened=reopen_revision_phases(retired,('report',))
    assert 'report' in reopened.active and not reopened.resident
    assert reopened.manual_skill_id==skills.manual_skill_id and reopened._initial_ceiling==skills._initial_ceiling
    assert reopened.effective_allowed_tool_names<=skills._initial_ceiling


@pytest.mark.asyncio
async def test_phase_reopen_at_capacity_requests_original_coordination_without_mutation():
    from services.task_execution.methods import reopen_revision_phases
    from tests.test_skill_runtime import Source,item,state as skills_state,activate
    skills=skills_state(Source([item(key,tools=('file_search',)) for key in ('chosen','a','b','c','d')]))
    await skills.initialize()
    skills.manual_skill_id='chosen'
    assert (await skills.activate(activate('chosen'),manual=True))['ok']
    # Model the original completed-phase retirement, then original activations.
    skills.resident['chosen']=skills.active.pop('chosen')
    for key in ('a','b','c','d'):assert (await skills.activate(activate(key)))['ok']
    before=skills.checkpoint()
    with pytest.raises(TaskError,match='PHASE_REPAIR_REQUIRED'):reopen_revision_phases(skills,('chosen',))
    assert skills.checkpoint()==before and skills.manual_skill_id=='chosen'
    assert not (await skills.deactivate(json.dumps({'skill_id':'chosen'})))['ok']
    assert (await skills.deactivate(json.dumps({'skill_id':'d'})))['ok']
    reopened=reopen_revision_phases(skills,('chosen',))
    assert len(reopened.active)==4 and 'chosen' in reopened.active
    assert reopened.effective_allowed_tool_names<=skills._initial_ceiling


@pytest.mark.asyncio
async def test_kernel_guard_runs_after_original_lock_and_prevents_ipc(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock
    from services.sandbox.kernel_manager import KernelManager
    manager=KernelManager();kernel=SimpleNamespace(lock=asyncio.Lock())
    manager._kernels['revision']=kernel
    monkeypatch.setattr(manager,'_is_alive',lambda _:True)
    send=AsyncMock();monkeypatch.setattr(manager,'_send_and_recv',send)
    changed=False
    async def guard():
        assert kernel.lock.locked() and changed
        raise TaskError('TASK_USER_CHANGE_REVIEW_REQUIRED')
    await kernel.lock.acquire()
    task=asyncio.create_task(manager.execute('revision','irreversible()',5,before_dispatch=guard))
    await asyncio.sleep(0)
    assert not task.done()
    changed=True;kernel.lock.release()
    with pytest.raises(TaskError,match='REVIEW_REQUIRED'):await task
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_reach_identity_read_precedes_last_guard_and_denial_sends_no_write():
    from unittest.mock import AsyncMock
    from services.agent.agent_reach.write_adapters import write
    from services.agent.agent_reach.contracts import ReachError
    request=SimpleNamespace(platform='twitter',action='create_post',content='旧文案')
    runner=SimpleNamespace(run=AsyncMock(return_value={'ok':True,'data':{'user':{'id':'account'}}}))
    async def guard():
        assert runner.run.await_count==1
        raise ReachError('TASK_USER_CHANGE_REVIEW_REQUIRED','已修改要求')
    connection={'account_id':'account','secret':{'auth_token':'fake','ct0':'fake'}}
    with pytest.raises(ReachError,match='已修改要求'):
        await write(request,connection,runner,None,None,before_dispatch=guard)
    assert runner.run.await_count==1 and runner.run.call_args.args[1]==['whoami','--json']


def test_review_limit_keeps_sources_but_a_genuinely_new_directive_can_be_reviewed():
    from services.task_execution.requirement_changes import exhausted,stage
    s=plan(state(image_binding_version=3));book=review(s)
    book=book.model_copy(update={'pending':None,'attempts':({'issues':['a']},)*3})
    runtime=SimpleNamespace(task_execution_state=s,task_revision_review=book,task_unreviewed_changes=book.events)
    assert exhausted(runtime)
    event={**book.events[0],'event_id':str(uuid4())}
    runtime.task_unreviewed_changes=(*book.events,event)
    assert not exhausted(runtime)
    assert stage(runtime,review(s).pending.model_dump(mode='json')).attempts==()


@pytest.mark.asyncio
async def test_unconfirmed_revision_save_restores_original_state_methods_reviews_and_messages():
    from unittest.mock import AsyncMock
    from services.handlers.chat.execution_engine import _build_replay_context
    from services.task_execution.requirement_changes import apply_review
    from services.task_execution.user_changes import UserChanges
    from tests.test_task_execution_model_bridge import setup_case
    import copy
    s=plan(state(image_binding_version=3));book=review(s)
    runtime,prepared,handler,sink=setup_case(initial=s)
    runtime.task_revision_review=book;runtime.task_unreviewed_changes=book.events
    runtime.task_user_changes=UserChanges.model_validate({'version':1,'root_task_id':str(s.root_task_id),
        'items':[{**e,'delivered':True} for e in book.events]})
    runtime.checkpoint_base={'opaque_context':{'keep':True},'checkpoint_kind':'commit_ready','result_content':['旧完成']}
    async def failure(source,proof,payload):
        assert payload['opaque_context']=={'keep':True}
        assert 'checkpoint_kind' not in payload and 'result_content' not in payload
        raise RuntimeError('unconfirmed')
    runtime.task_revision_apply=failure
    messages=copy.deepcopy(prepared.messages);blocks=[]
    with pytest.raises(RuntimeError,match='unconfirmed'):
        await apply_review(runtime=runtime,prepared=prepared,blocks=blocks,text=observation(),has_tool_calls=False,
            model_round=1,next_model_round=2,build_checkpoint=_build_replay_context,turn=1)
    assert runtime.task_execution_state==s and runtime.task_revision_review==book
    assert prepared.messages==messages and not blocks and runtime.task_unreviewed_changes==book.events


@pytest.mark.parametrize('capability',['platform.generate_image','platform.generate_video'])
def test_retired_accepted_media_is_settled_by_original_consumer_after_current_delivery(capability):
    from services.task_execution.image_binding import image_dependencies_waiting
    from services.task_execution.video_operations import pending as video_pending
    from services.task_execution.reducer import reserve_operation,record_operation
    s=state(image_binding_version=3)
    s=TaskState.model_validate({**s.model_dump(),'requirements':[s.requirements[0].model_copy(update={'artifact_kind':'image'})],
        'steps':[PlanStep(id='old-media',title='旧任务',capability=capability,requirement_ids=('report',))]})
    s,operation,_=reserve_operation(s,expected_revision=s.revision,step_id='old-media',capability=capability,
        effect_key='original-slot',effect_version=1,arguments={},owner_task_id=s.root_task_id,invocation_id='original-call')
    # Native protocol supplies the actual tool identity; this fixture never sends.
    s=TaskState.model_validate({**s.model_dump(),'operations':[operation.model_copy(update={'tool_name':capability.split('.')[-1]})]})
    s=record_operation(s,operation.id,owner_task_id=s.root_task_id,status='accepted',receipt_ref='task:'+str(uuid4()))
    new=candidate(s,review(s),None)
    assert not image_dependencies_waiting(new) and not video_pending(new)
    new=revise_plan(new,WorkPlan(expected_revision=new.revision,steps=[PlanStep(id='current-report',title='新报告',requirement_ids=(new.requirements[0].id,))]))
    new=register_artifacts(new,[artifact(new,'current-report')]);new=complete_step(new,'current-report',['current-report'])
    assert image_dependencies_waiting(new) if capability.endswith('image') else video_pending(new)
    settled=record_operation(new,operation.id,owner_task_id=s.root_task_id,status='completed',receipt_ref=s.operations[0].receipt_ref)
    assert finish(settled).status=='completed'
