import asyncio,hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4
import pytest
from services.task_execution.user_changes import UserChanges,restore,merge
from services.task_execution.contracts import TaskError
from services.conversation_commands import ConversationCommand,CommandType,SafePoint
from tests.test_task_execution_model_bridge import setup_case
from tests.test_task_execution_reducer import state


def box(root,owner,event=None,text='先只写提示词，不要生图',delivered=False):
    return {'version':1,'root_task_id':str(root),'items':[{'event_id':str(event or uuid4()),'owner_task_id':str(owner),
        'message':text,'message_sha256':hashlib.sha256(text.encode()).hexdigest(),'delivered':delivered}]}


def test_inbox_cannot_rewrite_source_or_revert_delivery_or_change_root():
    root,owner=uuid4(),uuid4();raw=box(root,owner)
    original=UserChanges.model_validate(raw)
    delivered=original.model_copy(update={'items':tuple(i.model_copy(update={'delivered':True}) for i in original.items)})
    assert merge(delivered,raw,root_task_id=root).items[0].delivered
    changed=box(root,owner,event=original.items[0].event_id,text='再生成一百张图')
    with pytest.raises(TaskError):merge(original,changed,root_task_id=root)
    with pytest.raises(TaskError):restore({'_task_user_changes':raw},root_task_id=uuid4(),owner_task_id=owner)
    with pytest.raises(TaskError):restore({'_task_user_changes':raw},root_task_id=root,owner_task_id=uuid4())
    assert restore({'_task_user_changes':raw},root_task_id=root,owner_task_id=uuid4(),inherited_root_task_id=root)==original


@pytest.mark.asyncio
async def test_managed_steer_is_captured_before_delivery_and_not_acknowledged_twice():
    s=state(image_binding_version=3);runtime,prepared,handler,sink=setup_case(initial=s)
    event=uuid4();raw=box(s.root_task_id,runtime.task_id,event)
    runtime.task_user_change_capture=AsyncMock(return_value=raw)
    runtime.execution_token=str(uuid4());runtime.command_store=SimpleNamespace(load_pending=AsyncMock(return_value=[]),acknowledge=AsyncMock())
    runtime.push(ConversationCommand(str(event),CommandType.STEER,runtime.conversation_id,runtime.task_id,
        turn_id=runtime.turn_id,event_id=str(event),payload={'message':raw['items'][0]['message']}))
    await runtime.safe_point(SafePoint.AFTER_TOOL)
    runtime.task_user_change_capture.assert_awaited_once_with(str(event))
    runtime.command_store.acknowledge.assert_not_awaited()
    assert not runtime.task_user_changes.items[0].delivered
    assert runtime.consume_steer_messages()==[raw['items'][0]['message']]
    assert runtime.task_user_changes.items[0].delivered
    assert runtime.consume_steer_messages()==[]
    await runtime.safe_point(SafePoint.BEFORE_MODEL,replay_payload={'messages':prepared.messages})
    assert runtime._replay_checkpoint_callback.await_args.args[1]['_task_user_changes']['items'][0]['delivered']


@pytest.mark.asyncio
async def test_unconfirmed_capture_never_delivers_or_acknowledges_original_event():
    s=state(image_binding_version=3);runtime,*_=setup_case(initial=s)
    runtime.task_user_change_capture=AsyncMock(side_effect=RuntimeError('local DB unavailable'))
    runtime.execution_token=str(uuid4());runtime.command_store=SimpleNamespace(load_pending=AsyncMock(return_value=[]),acknowledge=AsyncMock())
    event=str(uuid4());runtime.push(ConversationCommand(event,CommandType.STEER,runtime.conversation_id,runtime.task_id,event_id=event,payload={'message':'修改要求'}))
    with pytest.raises(RuntimeError):await runtime.safe_point(SafePoint.AFTER_TOOL)
    assert runtime.consume_steer_messages()==[] and runtime.task_user_changes is None
    runtime.command_store.acknowledge.assert_not_awaited()


def test_undelivered_input_reopens_only_delivery_window_preserving_every_original_fact_and_budget():
    from services.task_execution.user_changes import activate_for_delivery
    from services.task_execution.reducer import register_artifacts,complete_step,finish
    from tests.test_task_execution_reducer import plan,artifact
    s=plan(state(image_binding_version=3))
    s=register_artifacts(s,[artifact(s,'research'),artifact(s,'report','file')])
    s=complete_step(s,'research',['research']);s=complete_step(s,'report',['report']);s=finish(s)
    changes=UserChanges.model_validate(box(s.root_task_id,s.root_task_id))
    awake=activate_for_delivery(s,changes)
    assert awake.status=='active' and awake.revision==s.revision+1
    assert awake.model_dump(exclude={'status','revision'})==s.model_dump(exclude={'status','revision'})
    delivered=changes.model_copy(update={'items':tuple(i.model_copy(update={'delivered':True}) for i in changes.items)})
    assert activate_for_delivery(s,delivered) is s


@pytest.mark.asyncio
@pytest.mark.parametrize('delivered',[False,True])
async def test_actual_executor_does_not_skip_undelivered_steer_in_commit_ready_checkpoint(monkeypatch,delivered):
    from services.conversation_execution import GenerationClaim
    from services.handlers.chat.executor import ChatGenerationExecutor
    from services.handlers.chat.execution_engine import ChatExecutionResult
    from schemas.message import TextPart
    from tests.test_chat_generation_executor import _DB,_ReplayCheckpointStore
    s=state(image_binding_version=3);root=str(s.root_task_id);actor=str(s.actor_user_id);conv=str(s.conversation_id);source=str(s.source_message_ids[0])
    db=_DB({'id':source,'conversation_id':conv,'turn_id':'turn','role':'user','content':[{'type':'text','text':s.original_goal}]},
        conversation={'id':conv,'org_id':None,'user_id':actor,'source':'web','scope_type':'user'})
    store=_ReplayCheckpointStore();store.write=AsyncMock(return_value={'outcome':'saved'})
    store.read_result={'outcome':'found','payload':{'checkpoint_kind':'commit_ready','messages':[],
        '_task_execution':s.model_dump(mode='json'),'_task_user_changes':box(root,root,delivered=delivered),
        'result_content':[{'type':'text','text':'旧结果'}],'usage':{},'credits_cost':0}}
    events=[{k:v for k,v in i.items() if k!='delivered'} for i in store.read_result['payload']['_task_user_changes']['items']]
    db.rpc=lambda name,args:SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(data={'outcome':'found','events':events})))
    seen=[]
    async def execute_chat(**kwargs):
        runtime=kwargs['runtime'];seen.extend(runtime.consume_steer_messages())
        return ChatExecutionResult(parts=[TextPart(text='已读取补充要求')],content_blocks=[],usage={},credits_cost=0,tool_digest=None)
    monkeypatch.setattr('services.handlers.chat.executor.execute_chat',execute_chat)
    executor=ChatGenerationExecutor(db,handler_factory=lambda _:SimpleNamespace(org_id=None),handler_db_factory=lambda:object(),replay_checkpoint_store=store)
    claim=GenerationClaim(task_id=root,execution_token='token',conversation_id=conv,turn_id='turn',input_message_id=source,
        base_context_revision=0,context_through_message_id=None,execution_attempt=2,execution_mode='serial')
    outcome=await executor.execute({'id':root,'conversation_id':conv,'assistant_message_id':str(uuid4()),'input_message_id':source,
        'user_id':actor,'org_id':None,'type':'chat','request_params':{},'delivery_context':{'actor':True}},claim,asyncio.Event())
    assert seen==([] if delivered else ['先只写提示词，不要生图'])
    assert outcome.result_content==[{'type':'text','text':'已读取补充要求'}]
    assert store.write.await_args.kwargs['payload']['_task_user_changes']['items'][0]['delivered']


@pytest.mark.asyncio
async def test_multiple_steers_keep_arrival_order_and_one_delivery_per_canonical_event():
    s=state(image_binding_version=3);runtime,*_=setup_case(initial=s)
    first,second=uuid4(),uuid4();a=box(s.root_task_id,runtime.task_id,first,text='先写提示词')
    b=box(s.root_task_id,runtime.task_id,second,text='保留原图水印');b['items']=a['items']+b['items']
    runtime.task_user_change_capture=AsyncMock(side_effect=[a,b,b])
    for event,message in [(first,'先写提示词'),(second,'保留原图水印')]:
        runtime.push(ConversationCommand(str(event),CommandType.STEER,runtime.conversation_id,runtime.task_id,event_id=str(event),payload={'message':message}))
    await runtime.safe_point(SafePoint.AFTER_TOOL)
    assert runtime.consume_steer_messages()==['先写提示词','保留原图水印']
    # A redelivered source is deduplicated even when the transport command ID differs.
    runtime.push(ConversationCommand('transport-replay',CommandType.STEER,runtime.conversation_id,runtime.task_id,event_id=str(second),payload={'message':'保留原图水印'}))
    await runtime.safe_point(SafePoint.AFTER_TOOL)
    assert runtime.consume_steer_messages()==[] and all(i.delivered for i in runtime.task_user_changes.items)


def test_multiline_original_user_text_is_preserved_with_its_exact_digest():
    text='\n请先写完整提示词\n保留水印\n'
    value=UserChanges.model_validate(box(uuid4(),uuid4(),text=text))
    assert value.items[0].message==text
