"""Local follow-up projection and exact-image reuse; no model/provider IO."""
import copy,json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4
import pytest
from services.task_execution.followup import payload,FollowupObservation,review_messages
from services.task_execution.image_reuse import ReuseImageInput,reuse
from services.task_execution.image_resources import BindImageResources,bind_resources
from services.task_execution.image_binding import image_slots,bind_image_call
from services.task_execution.contracts import TaskError
from services.tools.spec import thaw
from services.task_execution.reducer import register_artifacts,digest
from tests.test_task_execution_image_binding import ready,native


def arguments():
    value=thaw(native(1).arguments)
    for ref in value.get('references',[]):ref['role']='reference'
    return value


def test_followup_keeps_original_facts_unknown_context_and_current_window_without_double_usage():
    from tests.test_task_execution_reducer import state,plan
    s=plan(state(image_binding_version=3));cp={'messages':[{'role':'system','content':'old memory'}],
        '_task_execution':s.model_dump(mode='json'),'opaque_context':{'old':True},'_task_usage':{'old':'already settled'},
        'checkpoint_kind':'commit_ready','result_content':['old result']}
    source={'state':s.model_dump(mode='json'),'checkpoint':cp,'input_message_id':str(uuid4()),'message':'继续原任务'}
    current={'messages':[{'role':'user','content':'继续原任务'}],'new_private_extension':{'keep':True}}
    previous=copy.deepcopy(source)
    result=payload(source,current,task_id='new-owner',turn_id='new-turn',model_round=1,usage={'prompt_tokens':9},
        observation=FollowupObservation(relation='continue',evidence=['继续原任务']))
    assert result['messages'][:1]==cp['messages'] and result['messages'][1:2]==current['messages']
    assert result['opaque_context']==cp['opaque_context'] and result['_task_followup_input_checkpoint']==current
    assert result['_task_execution']['operations']==cp['_task_execution']['operations']
    assert result['_task_execution']['requirements']==cp['_task_execution']['requirements']
    assert result['_task_usage']=={'version':1,'owner_task_id':'new-owner','usage':{'prompt_tokens':9}}
    assert 'checkpoint_kind' not in result and 'result_content' not in result and source==previous
    assert json.loads(review_messages(source)[1]['content'])['new_user_message']=='继续原任务'


@pytest.mark.asyncio
@pytest.mark.parametrize('fault',[None,'prompt','ratio','source','revoked','resolution','variant','rematerialized','same_variant','unknown_parameter','invalid_variant'])
async def test_exact_reuse_marks_only_one_slot_and_cannot_generate_that_slot_again(monkeypatch,fault):
    s=await ready(monkeypatch)
    refs=[{'message_id':'original','content_index':0,'role':'reference'}]
    resolved=[{'workspace_path':'product.png','file_version':[1,2,3,4],'content_sha256':'a'*64,'role':'reference'}]
    resolver=AsyncMock(return_value=resolved)
    artifact,_=await bind_resources(s,BindImageResources(expected_revision=s.revision,planning_artifact_id=s.artifacts[0].id,
        image_item_index=1,mode='image_to_image',references=refs),resolver=resolver,owner_task_id=s.root_task_id,call_id='bind')
    s=register_artifacts(s,[artifact]);args=arguments()
    row={'task_id':str(uuid4()),'message_id':str(uuid4()),'content_index':0,'part':{'type':'image','url':'owned'},
        'part_sha256':'b'*64,'request':{'request_hash':'c'*64,'origin':{'tool_call_id':'original-call'},
            'prompt':args['prompt'],'aspect_ratio':args['aspect_ratio'],'mode':'image_to_image','references':resolved}}
    if fault=='prompt':row['request']['prompt']='different visual'
    if fault=='ratio':row['request']['aspect_ratio']='1:1'
    if fault=='source':row['request']['references']=[{**resolved[0],'content_sha256':'0'*64}]
    if fault=='rematerialized':row['request']['references']=[{**resolved[0],'workspace_path':'old-cache.png','file_version':[8,9,10,11]}]
    if fault=='revoked':resolver.side_effect=PermissionError('revoked')
    if fault=='resolution':args['resolution']='4K'
    if fault=='variant':args['variant_id']='fresh-variant'
    if fault=='unknown_parameter':args['num_images']=7
    if fault=='invalid_variant':args['variant_id']=None
    if fault=='same_variant':args['variant_id']=row['request']['variant_id']='existing-variant'
    reader=SimpleNamespace(reusable_images=AsyncMock(return_value=[row]))
    value=ReuseImageInput(expected_revision=s.revision,arguments=args)
    if fault not in {None,'rematerialized','same_variant'}:
        with pytest.raises((TaskError,PermissionError)):await reuse(s,value,reader=reader,resolver=resolver,owner_task_id=s.root_task_id)
        return
    reused,part=await reuse(s,value,reader=reader,resolver=resolver,owner_task_id=s.root_task_id)
    updated=register_artifacts(s,[reused]);assert image_slots(updated)[0]['status']=='completed'
    assert all(v['status']=='not_submitted' for v in image_slots(updated)[1:])
    assert not updated.operations and reused.reference=='task-image:'+row['task_id']
    from services.tools.catalog import build_capability_catalog
    with pytest.raises(TaskError):bind_image_call(updated,native(1),build_capability_catalog())
    assert part==row['part']


@pytest.mark.asyncio
async def test_original_model_loop_routes_in_isolation_and_restarts_without_business_dispatch(monkeypatch):
    import asyncio
    from services.handlers.chat.execution_engine import _run_loop,ChatExecutionRequest
    from services.handlers.chat.stream_session import StreamTotals
    from services.task_execution.followup import FollowupRestart
    from tests.test_task_execution_model_bridge import setup_case
    from tests.test_task_execution_requirement_review import prepare_loop
    from tests.test_task_execution_reducer import state,plan
    s=plan(state(image_binding_version=3))
    runtime,prepared,handler,sink=setup_case(initial=None);prepare_loop(runtime,prepared,handler,sink)
    runtime.task_execution_state=None
    runtime.task_followup_candidate={'state':s.model_dump(mode='json'),'checkpoint':{'messages':[{'role':'system','content':'原记忆'}]},
        'message':'继续原任务','input_message_id':str(uuid4())}
    runtime.task_followup_adopt=AsyncMock(return_value={'outcome':'saved','delivery_context':{'input_kind':'agent_task_followup'}})
    async def model(private,tools,event,private_sink,totals,*args,task_review=False,**kwargs):
        assert task_review and tools==[] and private is not prepared
        assert json.loads(private.messages[1]['content'])['new_user_message']=='继续原任务'
        totals.usage['prompt_tokens']+=11
        return json.dumps({'relation':'continue','evidence':['继续原任务']}),'',[],set()
    monkeypatch.setattr('services.handlers.chat.execution_engine._read_turn',model)
    request=ChatExecutionRequest(content=[],user_id=str(s.actor_user_id),conversation_id=runtime.conversation_id,
        task_id=runtime.task_id,message_id='output',model_id='offline',context_anchor=None)
    totals=StreamTotals();runtime.task_usage=totals.usage
    with pytest.raises(FollowupRestart):
        await _run_loop(handler=handler,request=request,prepared=prepared,cancellation_event=runtime.cancellation_event,sink=sink,
            totals=totals,blocks=[],runtime=runtime)
    assert totals.usage['prompt_tokens']==11
    handler._execute_tool_calls.assert_not_called()
    runtime.task_followup_adopt.assert_awaited_once()


@pytest.mark.asyncio
async def test_original_skill_pin_and_ceiling_survive_a_real_new_turn_checkpoint_restore(monkeypatch):
    from tests.test_task_execution_methods import bound
    from tests.test_skill_runtime import state as skills_state
    s,skills=await bound(monkeypatch)
    cp={'messages':[],'skill_runtime':skills.checkpoint()}
    if skills.checkpoint_version==2:cp['_skill_composition']=skills.composition.model_dump(mode='json')
    source={'state':s.model_dump(mode='json'),'checkpoint':cp,'message':'继续','input_message_id':str(uuid4())}
    merged=payload(source,{'messages':[]},task_id=str(uuid4()),turn_id='new-turn',model_round=1,usage={},
        observation=FollowupObservation(relation='continue',evidence=['继续']))
    restored=skills_state(skills.source,turn_id='new-turn',platform_tool_names=skills.platform_tool_names,
        authorized_tool_names=skills._initial_ceiling)
    await restored.initialize(merged['skill_runtime'])
    assert restored.manual_skill_id==skills.manual_skill_id
    assert restored.active['report'].body_sha256==skills.active['report'].body_sha256
    assert restored.active['report'].rendered==skills.active['report'].rendered
    assert restored.effective_allowed_tool_names<=skills._initial_ceiling


@pytest.mark.asyncio
async def test_generation_call_automatically_uses_verified_existing_image_without_entering_native_handler(monkeypatch):
    from tests.test_task_execution_model_bridge import setup_case,execute,call
    from services.task_execution.contracts import Artifact,ReusedImage
    s=(await ready(monkeypatch)).model_copy(update={'image_binding_version':3})
    runtime,prepared,handler,sink=setup_case(initial=s)
    runtime.task_artifact_reader=SimpleNamespace(reusable_images=AsyncMock())
    runtime.task_image_resource_resolver=AsyncMock()
    reused=Artifact(id='real-existing',kind='image',reference='task-image:'+str(uuid4()),sha256='a'*64,
        source_task_id=str(uuid4()),source_call_id='real-original',delivery_task_id=runtime.task_id,step_id='images',
        image_reuse=ReusedImage(requirement_id='user.images',item_index=1,requirements_revision=0,planning_artifact_id=s.artifacts[0].id,request_sha256='b'*64))
    part={'type':'image','url':'owned-original'}
    check=AsyncMock(return_value=(reused,part));monkeypatch.setattr('services.task_execution.image_reuse.reuse',check)
    blocks=[]
    await execute(runtime,prepared,handler,sink,[call('generate_image',arguments(),'native-request')],blocks)
    handler._execute_tool_calls.assert_not_called()
    response=json.loads(prepared.messages[-1]['content'])
    assert response['reused'] and response['charged'] is False
    assert prepared.messages[-2]['tool_calls'][0]['function']['name']=='generate_image'
    assert part in blocks and runtime.task_execution_state.operations==s.operations


@pytest.mark.asyncio
async def test_real_executor_reloads_committed_followup_after_lost_ack_and_keeps_current_user_anchor(monkeypatch):
    import asyncio,hashlib
    from services.handlers.chat.executor import ChatGenerationExecutor
    from services.handlers.chat.execution_engine import ChatExecutionResult
    from services.task_execution.followup import FollowupRestart
    from services.conversation_execution import GenerationClaim
    from tests.test_chat_generation_executor import _DB,_ReplayCheckpointStore
    from tests.test_task_execution_reducer import state,plan
    from schemas.message import TextPart
    from core.config import get_settings
    monkeypatch.setattr(get_settings(),'agent_task_execution_enabled',True)
    s=plan(state(image_binding_version=3));root=str(s.root_task_id);actor=str(s.actor_user_id);conv=str(s.conversation_id)
    task_id,message,turn=str(uuid4()),str(uuid4()),str(uuid4());text='继续原任务'
    db=_DB({'id':message,'conversation_id':conv,'turn_id':turn,'role':'user','content':[{'type':'text','text':text}]},
        conversation={'id':conv,'org_id':None,'user_id':actor,'source':'web','scope_type':'user'})
    store=_ReplayCheckpointStore();store.write=AsyncMock(return_value={'outcome':'saved'})
    source={'state':s.model_dump(mode='json'),'checkpoint':{'messages':[{'role':'system','content':'原记忆'}]},
        'message':text,'input_message_id':message,'source_sha256':'a'*64}
    event={'event_id':str(uuid4()),'owner_task_id':task_id,'message':text,'message_sha256':hashlib.sha256(text.encode()).hexdigest()}
    adopted={'messages':[{'role':'system','content':'原记忆'},{'role':'user','content':text}],
        '_task_execution':s.model_dump(mode='json'),'_task_usage':{'version':1,'owner_task_id':task_id,'usage':{'prompt_tokens':5}},
        '_task_user_changes':{'version':1,'root_task_id':root,'items':[{**event,'delivered':False}]}}
    delivery={'actor':True,'input_kind':'agent_task_followup','logical_task_id':root};attempts=[]
    def rpc(name,params):
        async def execute():
            if name=='read_agent_task_followup':return SimpleNamespace(data={'outcome':'found','source':source})
            if name=='read_agent_working_plan_candidates':
                assert params['p_task_id']==task_id
                return SimpleNamespace(data={'outcome':'found','candidates':[]})
            if name=='read_agent_task_user_change_pending':return SimpleNamespace(data={'outcome':'found','events':[event]})
            assert name=='adopt_agent_task_followup';attempts.append(name)
            store.read_result={'outcome':'found','payload':copy.deepcopy(adopted)}
            if len(attempts)==1:raise ConnectionError('committed response lost')
            return SimpleNamespace(data={'outcome':'saved','payload':copy.deepcopy(adopted),'delivery_context':delivery})
        return SimpleNamespace(execute=execute)
    db.rpc=rpc;windows=[]
    async def chat(**kwargs):
        runtime=kwargs['runtime'];request=kwargs['request'];windows.append(request)
        assert request.context_anchor.input_message_id==message
        if len(windows)==1:
            assert runtime.task_execution_state is None and runtime.task_followup_candidate==source
            reply=await runtime.task_followup_adopt(source,{'relation':'continue','evidence':[text]},adopted)
            raise FollowupRestart(reply['delivery_context'])
        assert runtime.task_execution_state.root_task_id==s.root_task_id
        assert runtime.consume_steer_messages()==[text]
        assert request.replay_context['_task_usage']['owner_task_id']==task_id
        assert request.replay_context['messages'][0]['content']=='原记忆'
        return ChatExecutionResult(parts=[TextPart(text='接续成功')],content_blocks=[],usage={'prompt_tokens':17},credits_cost=0,tool_digest=None)
    monkeypatch.setattr('services.handlers.chat.executor.execute_chat',chat)
    executor=ChatGenerationExecutor(db,handler_factory=lambda _:SimpleNamespace(org_id=None),handler_db_factory=lambda:object(),replay_checkpoint_store=store)
    claim=GenerationClaim(task_id=task_id,execution_token=str(uuid4()),conversation_id=conv,turn_id=turn,input_message_id=message,
        base_context_revision=2,context_through_message_id=None,execution_attempt=1,execution_mode='serial')
    result=await executor.execute({'id':task_id,'conversation_id':conv,'assistant_message_id':str(uuid4()),'input_message_id':message,
        'user_id':actor,'org_id':None,'type':'chat','request_params':{},'delivery_context':{'actor':True}},claim,asyncio.Event())
    assert len(windows)==2 and len(attempts)==2 and result.result_content==[{'type':'text','text':'接续成功'}]


@pytest.mark.asyncio
@pytest.mark.parametrize('relation',['unrelated','uncertain'])
async def test_unrelated_or_ambiguous_new_message_never_acquires_old_task(relation):
    from services.task_execution.followup import apply
    from services.handlers.chat.execution_engine import _build_replay_context
    from tests.test_task_execution_model_bridge import setup_case
    runtime,prepared,_,_=setup_case();runtime.task_execution_state=None
    runtime.task_followup_candidate={'message':'另一个问题','input_message_id':str(uuid4())}
    runtime.task_followup_adopt=AsyncMock()
    await apply(runtime,prepared,[],json.dumps({'relation':relation,'evidence':['另一个问题']}),False,
        model_round=1,turn=0,build_checkpoint=_build_replay_context)
    runtime.task_followup_adopt.assert_not_awaited()
    assert runtime.task_followup_candidate is None and runtime.task_execution_state is None
    assert runtime.checkpoint_base['_task_followup']['disposition']==relation


@pytest.mark.asyncio
async def test_failed_route_checkpoint_does_not_drop_current_source_or_private_context():
    from services.task_execution.followup import apply
    from services.handlers.chat.execution_engine import _build_replay_context
    from tests.test_task_execution_model_bridge import setup_case
    runtime,prepared,_,_=setup_case(checkpoint=AsyncMock(side_effect=RuntimeError('save failed')))
    runtime.task_execution_state=None;runtime.checkpoint_base={'opaque':{'keep':True}}
    source={'message':'另一个问题','input_message_id':str(uuid4())};runtime.task_followup_candidate=source
    with pytest.raises(RuntimeError,match='save failed'):
        await apply(runtime,prepared,[],json.dumps({'relation':'unrelated','evidence':['另一个问题']}),False,
            model_round=1,turn=0,build_checkpoint=_build_replay_context)
    assert runtime.checkpoint_base=={'opaque':{'keep':True}} and runtime.task_followup_candidate==source


@pytest.mark.asyncio
async def test_existing_image_access_denial_cannot_fall_back_to_paid_replacement(monkeypatch):
    from tests.test_task_execution_model_bridge import setup_case,execute,call
    s=(await ready(monkeypatch)).model_copy(update={'image_binding_version':3})
    runtime,prepared,handler,sink=setup_case(initial=s)
    runtime.task_artifact_reader=SimpleNamespace(reusable_images=AsyncMock())
    runtime.task_image_resource_resolver=AsyncMock()
    monkeypatch.setattr('services.task_execution.image_reuse.reuse',AsyncMock(side_effect=TaskError('TASK_IMAGE_REUSE_RESOURCE_UNAVAILABLE')))
    await execute(runtime,prepared,handler,sink,[call('generate_image',arguments(),'denied-output')],[])
    handler._execute_tool_calls.assert_not_called()
    assert json.loads(prepared.messages[-1]['content'])['code']=='TASK_IMAGE_REUSE_RESOURCE_UNAVAILABLE'
    assert not runtime.task_execution_state.operations


@pytest.mark.asyncio
@pytest.mark.parametrize('literal_in_user_message',[False,True])
async def test_real_automatic_reuse_path_preserves_native_exact_size_rejection(monkeypatch,literal_in_user_message):
    from tests.test_task_execution_model_bridge import setup_case,execute,call
    from services.handlers.image_size_requirements import resolve_size_requirement
    s=(await ready(monkeypatch)).model_copy(update={'image_binding_version':3})
    runtime,prepared,handler,sink=setup_case(initial=s)
    reader=SimpleNamespace(reusable_images=AsyncMock(return_value=[]));runtime.task_artifact_reader=reader
    resolver=AsyncMock(return_value=[]);runtime.task_image_resource_resolver=resolver
    monkeypatch.setattr('services.task_execution.image_binding.bind_image_call',lambda *a:SimpleNamespace(
        planning_artifact_id=s.artifacts[0].id,item_index=1,effect_key='existing-slot'))
    monkeypatch.setattr('services.task_execution.image_resources.binding_for',lambda *a:{'references':[],'resolved':[],'mode':'text_to_image'})
    args={'mode':'text_to_image','prompt':'完整已核验提示词','aspect_ratio':'3:4','size_requirement':{'mode':'explicit'}}
    if literal_in_user_message:
        async def native_size(arguments,references):
            return resolve_size_requirement(arguments,references,intent={'width':1200,'height':1600})
        resolver.size_requirement=native_size
    else:args['size_requirement'].update(width=1200,height=1600)
    await execute(runtime,prepared,handler,sink,[call('generate_image',args,'exact-size')],[])
    handler._execute_tool_calls.assert_not_called();reader.reusable_images.assert_not_awaited()
    assert json.loads(prepared.messages[-1]['content'])['code']=='IMAGE_EXACT_SIZE_UNSUPPORTED'
    assert runtime.task_execution_state==s


@pytest.mark.asyncio
async def test_reconcile_original_lost_acceptance_once_before_deciding_to_wait():
    from services.task_execution.followup import settle_images
    from services.task_execution.reducer import reserve_operation,record_operation
    from services.task_execution.contracts import TaskState,PlanStep
    from services.handlers.chat.execution_engine import _build_replay_context
    from tests.test_task_execution_model_bridge import setup_case
    from tests.test_task_execution_reducer import state
    s=state(image_binding_version=3)
    s=TaskState.model_validate({**s.model_dump(),'requirements':[s.requirements[0].model_copy(update={'artifact_kind':'image'})],
        'steps':[PlanStep(id='image',title='原图',capability='platform.generate_image',requirement_ids=('report',))]})
    s,op,_=reserve_operation(s,expected_revision=s.revision,step_id='image',capability='platform.generate_image',effect_key='original-slot',
        effect_version=1,arguments={},owner_task_id=s.root_task_id,invocation_id='original-call')
    accepted=record_operation(s,op.id,owner_task_id=s.root_task_id,status='accepted',receipt_ref='task:'+str(uuid4()))
    store=SimpleNamespace(reconcile_image=AsyncMock(return_value=SimpleNamespace(state=accepted)))
    runtime,prepared,_,_=setup_case(initial=s);runtime.task_operation_store_factory=lambda:store
    await settle_images(runtime,prepared,[],turn=0,model_round=0,build_checkpoint=_build_replay_context)
    await settle_images(runtime,prepared,[],turn=0,model_round=0,build_checkpoint=_build_replay_context)
    store.reconcile_image.assert_awaited_once()
    assert runtime.task_execution_state.operations[0].receipt_ref==accepted.operations[0].receipt_ref
    assert len(runtime.task_execution_state.operations)==1
