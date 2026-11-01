"""Native scheduled effects keep the original form and approval semantics."""
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock
from uuid import uuid4

import pytest

from services.agent.agent_result import AgentResult
from services.scheduler.chat_task_manager import FormBlockResult
from services.task_execution.contracts import PlanStep,TaskError
from services.task_execution.controller import TaskController
from services.task_execution.native_results import lifecycle_for
from services.task_execution.reducer import finish
from services.task_execution.scheduled_operations import request_key,native_request_key
from services.tools import ToolCall,ToolDispatcher,ToolExecutionService
from services.tools.result import UncertainToolInvocationError
from tests.test_task_execution_reach_operations import Store as BaseStore,case as reach_case,execute


class Store(BaseStore):
    async def scheduled_receipt(self,state,operation_id):
        if self.proof is None:raise TaskError('TASK_SCHEDULED_RECEIPT_UNCONFIRMED')
        return self.proof


def case(*,form=True):
    runtime,handler,context,_,_,_,_=reach_case()
    old=runtime.task_execution_state
    state=old.model_copy(update={'original_goal':'创建每天8点的日报任务',
        'steps':(PlanStep(id='schedule',title='创建日报任务',capability='platform.manage_scheduled_task',requirement_ids=('report',)),)})
    runtime.task_execution_state=state;store=Store(state,uuid4());runtime.task_operation_store=store
    handler._actor_turn_id=str(store.turn)
    context=replace(context,permission_mode='auto',entrypoint='model',feature_flags={'scheduled_task_direct_enabled':True})
    call=ToolCall('schedule-one','manage_scheduled_task',{'action':'create','definition':{'name':'日报','prompt':'查询销售日报','schedule_type':'daily','time_str':'08:00'},'recipient':'我'})
    async def native(args):
        op=runtime.task_execution_state.operations[-1]
        if form:return FormBlockResult({'form_type':'scheduled_task_create','form_id':'task_effect_'+op.id,'fields':[]},'等待确认')
        return AgentResult('已提交检查',metadata={'change_set':{'id':store.proof['change_set_id']}})
    io=AsyncMock(side_effect=native)
    service=ToolExecutionService(store.registry,ToolDispatcher({('legacy','manage_scheduled_task'):io}))
    return runtime,handler,context,call,service,io,store


def proof(state,store,operation,*,status='applied',receipt=True):
    cid=str(uuid4());tid=str(uuid4())
    return {'task_id':str(operation.owner_task_id),'turn_id':str(operation.owner_turn_id),'tool_call_id':operation.invocation_id,
        'args_hash':operation.arguments_sha256,'actor_user_id':str(state.actor_user_id),'org_id':str(state.org_id),
        'conversation_id':str(state.conversation_id),'change_set_id':cid,'change_set_status':status,'operation':'create','resource_id':tid,
        'receipt':{'id':str(uuid4()),'org_id':str(state.org_id),'change_set_id':cid,'task_id':tid,'operation':'create',
                   'base_revision':0,'new_revision':1,'result':{'task_id':tid,'outcome':'created','new_revision':1}} if receipt else None}


def controller(runtime,context,store):return TaskController(runtime.task_execution_state,owner_task_id=runtime.task_execution_state.root_task_id,
    result_context=context,operation_store=store,commit=AsyncMock(return_value={'outcome':'saved'}))


@pytest.mark.asyncio
async def test_form_is_accepted_not_completed_and_replays_original_without_another_submission():
    runtime,handler,ctx,call,service,io,store=case()
    original=await execute(runtime,handler,ctx,call,service)
    op=runtime.task_execution_state.operations[-1]
    assert op.status=='accepted' and op.receipt_ref=='scheduled-form:'+original.raw.form['form_id']
    with pytest.raises(TaskError):finish(runtime.task_execution_state)
    new=ToolCall('another-id',call.name,dict(call.arguments))
    replay=await execute(runtime,handler,replace(ctx,call_id=new.call_id),new,service)
    assert replay.execution.replayed and replay.raw.form==original.raw.form;io.assert_awaited_once()
    ctrl=controller(runtime,ctx,store)
    read=await ctrl.control('read_task_scheduled_result',{'expected_revision':ctrl.state.revision,'operation_id':op.id},call_id='read')
    assert read.result['ok'] and read.result['receipt'] is None and ctrl.state==runtime.task_execution_state


@pytest.mark.asyncio
@pytest.mark.parametrize('status',['awaiting_approval','committing','failed','applied'])
async def test_only_original_business_receipt_can_deliver_not_changeset_status(status):
    runtime,handler,ctx,call,service,io,store=case()
    await execute(runtime,handler,ctx,call,service);op=runtime.task_execution_state.operations[-1]
    store.proof=proof(runtime.task_execution_state,store,op,status=status,receipt=False)
    ctrl=controller(runtime,ctx,store)
    pending=await ctrl.control('read_task_scheduled_result',{'expected_revision':ctrl.state.revision,'operation_id':op.id},call_id='read')
    assert pending.result['ok'] and pending.result['operation_status']=='accepted'
    rejected=await ctrl.control('import_task_receipt',{'expected_revision':ctrl.state.revision,'operation_id':op.id},call_id='bad-import')
    assert not rejected.result['ok']
    store.proof=proof(ctrl.state,store,op,status=status)
    done=await ctrl.control('read_task_scheduled_result',{'expected_revision':ctrl.state.revision,'operation_id':op.id},call_id='receipt')
    assert done.result['ok'] and done.result['operation_status']=='completed'
    imported=await ctrl.control('import_task_receipt',{'expected_revision':ctrl.state.revision,'operation_id':op.id},call_id='import')
    assert imported.result['ok'],imported.result
    complete=await ctrl.control('complete_task_step',{'expected_revision':ctrl.state.revision,'step_id':'schedule',
        'artifact_ids':[imported.result['imported_artifact_id']]},call_id='complete')
    assert complete.result['ok'] and finish(ctrl.state).status=='completed';io.assert_awaited_once()


@pytest.mark.asyncio
async def test_changed_arguments_and_missing_actor_cannot_create_a_second_scheduled_effect():
    runtime,handler,ctx,call,service,io,store=case()
    await execute(runtime,handler,ctx,call,service)
    changed=ToolCall('different',call.name,{**dict(call.arguments),'action':'delete'})
    with pytest.raises(TaskError,match='ARGUMENTS_CHANGED'):await execute(runtime,handler,ctx,changed,service)
    handler._actor_enabled=False
    with pytest.raises(PermissionError):await execute(runtime,handler,ctx,call,service)
    io.assert_awaited_once()


@pytest.mark.asyncio
async def test_unknown_native_return_never_allows_fresh_call_id_or_recovery_dispatch():
    runtime,handler,ctx,call,service,io,store=case()
    io.side_effect=RuntimeError('return lost')
    result=await execute(runtime,handler,ctx,call,service)
    assert result.status=='error' and runtime.task_execution_state.operations[-1].status=='uncertain'
    again=ToolCall('new-call',call.name,dict(call.arguments))
    with pytest.raises(UncertainToolInvocationError):await execute(runtime,handler,ctx,again,service)
    io.assert_awaited_once()
    runtime2,handler2,ctx2,call2,service2,io2,_=case()
    lifecycle=lifecycle_for(handler2,ctx2,service2.registry,call2.name,recovery_only=True)
    with pytest.raises(UncertainToolInvocationError):await lifecycle.replay(call2,ctx2,service2.policy.decide(call2.name,ctx2,call2.arguments))
    io2.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('change',[{'actor_user_id':'different-actor','workspace_owner_id':'different-actor'},{'org_id':str(uuid4())},
    {'workspace_owner_id':'channel-owner','context_scope':'channel','personal_context_allowed':False},{'personal_context_allowed':False}])
async def test_result_and_import_enforce_current_frozen_context(change):
    runtime,handler,ctx,call,service,io,store=case();await execute(runtime,handler,ctx,call,service)
    ctrl=controller(runtime,replace(ctx,**change),store);before=ctrl.state
    result=await ctrl.control('read_task_scheduled_result',{'expected_revision':before.revision,'operation_id':before.operations[-1].id},call_id='read')
    assert not result.result['ok'] and ctrl.state==before;ctrl.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_native_request_key_uses_fenced_rpc_and_legacy_has_no_new_dependency():
    executor=SimpleNamespace()
    assert await native_request_key(executor,'legacy',{}) is None
    runtime,handler,ctx,call,service,io,store=case();await execute(runtime,handler,ctx,call,service)
    op=runtime.task_execution_state.operations[-1];expected=request_key(op.id)
    executor=SimpleNamespace(_task_execution_state=runtime.task_execution_state,_task_id=ctx.task_id,
        image_execution_token=handler._actor_execution_token,db=SimpleNamespace(rpc=Mock(return_value=SimpleNamespace(execute=Mock(return_value=SimpleNamespace(data=expected))))))
    assert await native_request_key(executor,call.call_id,{'action':'create'})==expected
    assert executor.db.rpc.call_args.args[0]=='agent_task_scheduled_request_key'


@pytest.mark.asyncio
@pytest.mark.parametrize('fail_save',[False,True])
async def test_receipt_progress_and_model_pairs_share_original_atomic_checkpoint(fail_save):
    import copy
    import json
    from tests.test_task_execution_model_bridge import setup_case,execute as control_execute,call as control_call
    native,handler,ctx,call,service,io,store=case()
    await execute(native,handler,ctx,call,service)
    initial=native.task_execution_state;op=initial.operations[-1];store.proof=proof(initial,store,op)
    snapshots=[]
    async def checkpoint(point,payload):
        snapshots.append(copy.deepcopy(payload))
        if fail_save:raise RuntimeError('checkpoint unconfirmed')
        return {'outcome':'saved'}
    runtime,prepared,model_handler,sink=setup_case(checkpoint,initial=initial)
    runtime.task_operation_store=store
    runtime.task_result_context_provider=AsyncMock(return_value=ctx)
    calls=[control_call('read_task_scheduled_result',{'expected_revision':initial.revision,'operation_id':op.id})]
    if fail_save:
        with pytest.raises(RuntimeError,match='checkpoint unconfirmed'):await control_execute(runtime,prepared,model_handler,sink,calls,[])
        assert runtime.task_execution_state==initial and len(prepared.messages)==1
        sink.on_block.assert_not_awaited();sink.on_block_update.assert_not_awaited()
    else:
        await control_execute(runtime,prepared,model_handler,sink,calls,[])
        assert runtime.task_execution_state.operations[-1].status=='completed'
        assert len(snapshots)==1 and snapshots[0]['_task_execution']['operations'][-1]['status']=='completed'
        saved=snapshots[0]['messages']
        assert saved[1]['role']=='assistant' and saved[2]['role']=='tool' and json.loads(saved[2]['content'])['ok']
    io.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('managed',[False,True])
async def test_original_manager_receives_stable_identity_without_changing_form_or_description_rules(managed):
    from unittest.mock import patch
    from services.agent.tool_executor import ToolExecutor
    from services.tool_invocation_store import hash_tool_arguments
    runtime,handler,ctx,call,service,io,store=case()
    args={'action':'create','description':'模型整理的描述','definition':{'name':'日报','prompt':'查询日报','schedule_type':'daily','time_str':'08:00'}}
    expected=request_key('a'*64)
    rpc=Mock(return_value=SimpleNamespace(execute=Mock(return_value=SimpleNamespace(data=expected))))
    executor=ToolExecutor(SimpleNamespace(rpc=rpc),ctx.actor_user_id,ctx.conversation_id,ctx.org_id,
        task_id=ctx.task_id,tool_entrypoint='model',image_execution_token=handler._actor_execution_token)
    if managed:executor._task_execution_state=runtime.task_execution_state
    original_form={'type':'form','form_type':'scheduled_task_create','form_id':'original-random','title':'确认创建',
        'fields':[{'type':'text','name':'prompt','default_value':'原执行内容'}]}
    manager=SimpleNamespace(handle=AsyncMock(return_value=original_form))
    with patch('services.tools.dispatcher.current_dispatch_call_id',return_value=call.call_id), \
         patch('services.scheduler.task_request_context.current_creation_text',return_value='真正的用户要求'), \
         patch('services.scheduler.chat_task_manager.ChatTaskManager',return_value=manager) as factory:
        result=await executor._manage_scheduled_task(args)
    assert result.form['fields']==original_form['fields']
    assert manager.handle.call_args.args[1]['description']=='真正的用户要求'
    if managed:
        assert result.form['form_id']=='task_effect_'+'a'*64
        assert factory.call_args.kwargs['idempotency_key']==expected
        assert rpc.call_args.args[1]['p_args_hash']==hash_tool_arguments(args)
    else:
        assert result.form['form_id']=='original-random';rpc.assert_not_called()
