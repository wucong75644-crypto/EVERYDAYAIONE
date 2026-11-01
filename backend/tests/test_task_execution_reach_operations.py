"""Real Policy/lifecycle/result payloads, deterministic native ledger double."""
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from services.agent.agent_result import AgentResult
from services.task_execution.contracts import PlanStep,TaskError,TaskState,WorkPlan
from services.task_execution.controller import TaskController
from services.task_execution.native_results import lifecycle_for
from services.task_execution.operation_store import OperationWrite
from services.task_execution.reach_operations import receipt_artifact
from services.task_execution.reducer import reserve_operation,record_operation,revise_plan,finish
from services.tools import ToolCall,ToolConfirmation,ToolContext,ToolDispatcher,ToolExecutionService
from services.tools.catalog import build_capability_catalog
from services.tools.spec import thaw
from services.tools.result import UncertainToolInvocationError
from services.tools.result_payload import encode_result
from services.tool_invocation_store import hash_tool_arguments
from tests.test_task_execution_reducer import state as base_state


class Store:
    registry=build_capability_catalog()
    def __init__(self,state,turn):self.state=state;self.turn=turn;self.payloads={};self.proof=None
    async def begin(self,state,*,step_id,effect_key,effect_version,call):
        candidate,op,action=reserve_operation(state,expected_revision=state.revision,step_id=step_id,
            capability=self.registry.require(call.name).capability,effect_key=effect_key,effect_version=effect_version,
            arguments=thaw(call.arguments),owner_task_id=state.root_task_id,invocation_id=call.call_id)
        if action=='reserve':
            op=op.model_copy(update={'owner_turn_id':self.turn,'tool_name':call.name})
            candidate=TaskState.model_validate({**candidate.model_dump(),'operations':tuple(op if v.id==op.id else v for v in candidate.operations)})
            self.state=candidate
            return OperationWrite('execute',candidate,op)
        return OperationWrite('replay' if op.id in self.payloads else 'reconcile',state,op,self.payloads.get(op.id))
    async def reach_receipt(self,state,operation_id):
        if self.proof is None:raise TaskError('TASK_WRITE_RECEIPT_UNCONFIRMED')
        return self.proof
    async def complete_invocation(self,state,*,operation_id,result,status,receipt_ref=None):
        candidate=record_operation(state,operation_id,owner_task_id=state.root_task_id,status=status,receipt_ref=receipt_ref)
        self.payloads[operation_id]=encode_result(result)
        if self.proof is not None:self.proof.update(tool_result=self.payloads[operation_id],ledger_status='succeeded' if status=='completed' else 'uncertain')
        self.state=candidate
        return OperationWrite('saved',candidate,candidate.operations[-1])


def case(*,status='success',proof=True):
    initial=base_state();org=uuid4()
    state=initial.model_copy(update={'image_binding_version':3,'org_id':org,'original_goal':'提交这条评论并返回实际回执',
        'requirements':(initial.requirements[0].model_copy(update={'artifact_kind':'receipt'}),)})
    state=revise_plan(state,WorkPlan(expected_revision=state.revision,steps=(PlanStep(id='write',title='提交评论',
        capability='platform.agent_reach',requirement_ids=('report',)),)))
    turn=uuid4();store=Store(state,turn)
    runtime=SimpleNamespace(task_execution_state=state,task_operation_store=store,task_id=str(state.root_task_id),
        replay_save_sequence=0,task_read_arguments=None)
    handler=SimpleNamespace(_actor_runtime=runtime,_actor_enabled=True,_actor_invocation_store=object(),
        _actor_turn_id=str(turn),_actor_execution_token=str(uuid4()))
    context=ToolContext(actor_user_id=str(state.actor_user_id),workspace_owner_id=state.execution_identity.workspace_owner_id,
        org_id=str(org),context_scope='user',personal_context_allowed=True,agent_domain='general',permission_mode='ask',
        execution_mode='interactive',task_id=str(state.root_task_id),conversation_id=str(state.conversation_id),
        call_id='original-write',confirmation_available=True,feature_flags={'agent_reach_enabled':True})
    call=ToolCall('original-write','agent_reach',{'task':'提交评论','platform':'twitter','action':'create_comment',
        'content':'最终评论内容','url':'https://x.com/i/status/123','connection_id':str(uuid4())})
    receipt={'action':'create_comment','remote_id':'remote-1','target':call.arguments['url']}
    if proof:store.proof={'native_id':str(uuid4()),'connection_id':call.arguments['connection_id'],'platform':'twitter',
        'state':'succeeded','receipt':receipt,'task_id':str(state.root_task_id),'turn_id':str(turn),
        'tool_call_id':call.call_id,'args_hash':hash_tool_arguments(dict(call.arguments))}
    io=AsyncMock(return_value=AgentResult('平台返回',status=status,metadata={'platform':'twitter','receipt':receipt}))
    service=ToolExecutionService(store.registry,ToolDispatcher({('legacy','agent_reach'):io}))
    return runtime,handler,context,call,service,io,store


async def execute(runtime,handler,context,call,service,*,approved=True):
    lifecycle=lifecycle_for(handler,context,service.registry,call.name)
    decision=service.policy.decide(call.name,context,call.arguments)
    if decision.outcome!='deny':
        reused=await lifecycle.replay(call,context,decision)
        if reused is not None:return reused
    confirmation=ToolConfirmation(decision.confirmation_binding,'approved') if approved and decision.outcome=='require_confirmation' else None
    return await service.execute(call,context,confirmation=confirmation,before_dispatch=lifecycle.begin,on_result=lifecycle.complete)


@pytest.mark.asyncio
async def test_native_confirmation_precedes_reservation_and_policy_denial_does_not_dispatch():
    runtime,handler,context,call,service,io,store=case()
    result=await execute(runtime,handler,context,call,service,approved=False)
    assert result.status!='success' and not runtime.task_execution_state.operations
    await execute(runtime,handler,replace(context,permission_mode='plan'),call,service)
    assert not runtime.task_execution_state.operations;io.assert_not_awaited()


@pytest.mark.asyncio
async def test_real_receipt_completes_operation_and_new_call_id_replays_without_another_send():
    runtime,handler,context,call,service,io,store=case()
    await execute(runtime,handler,context,call,service)
    state=runtime.task_execution_state;op=state.operations[-1]
    assert op.status=='completed'
    new=ToolCall('new-id',call.name,dict(call.arguments))
    replay=await lifecycle_for(handler,context,service.registry,new.name).replay(new,context,service.policy.decide(new.name,context,new.arguments))
    assert replay.execution.replayed;io.assert_awaited_once()
    controller=TaskController(state,owner_task_id=state.root_task_id,commit=AsyncMock(return_value={'outcome':'saved'}),operation_store=store)
    imported=await controller.control('import_task_receipt',{'expected_revision':state.revision,'operation_id':op.id},call_id='import')
    assert imported.result['ok'],imported.result
    completed=await controller.control('complete_task_step',{'expected_revision':controller.state.revision,'step_id':'write',
        'artifact_ids':[imported.result['imported_artifact_id']]},call_id='complete')
    assert completed.result['ok'] and finish(controller.state).status=='completed'


@pytest.mark.asyncio
@pytest.mark.parametrize('status,proof',[('error',True),('partial',True),('success',False)])
async def test_failed_partial_or_missing_native_receipt_never_grants_a_second_dispatch(status,proof):
    runtime,handler,context,call,service,io,store=case(status=status,proof=proof)
    await execute(runtime,handler,context,call,service)
    assert runtime.task_execution_state.operations[-1].status=='uncertain'
    new=ToolCall('new-id',call.name,dict(call.arguments))
    with pytest.raises(UncertainToolInvocationError):
        await lifecycle_for(handler,context,service.registry,new.name).replay(new,context,service.policy.decide(new.name,context,new.arguments))
    io.assert_awaited_once()


@pytest.mark.asyncio
async def test_changed_payload_cannot_use_new_call_id_for_same_original_receipt_obligation():
    runtime,handler,context,call,service,io,store=case()
    await execute(runtime,handler,context,call,service)
    new=ToolCall('new-id',call.name,{**dict(call.arguments),'content':'改写后的另一条评论'})
    with pytest.raises(TaskError,match='ARGUMENTS_CHANGED'):
        await lifecycle_for(handler,context,service.registry,new.name).replay(new,context,service.policy.decide(new.name,context,new.arguments))
    io.assert_awaited_once()


@pytest.mark.asyncio
async def test_recovery_only_cannot_create_an_unrecorded_write_operation():
    runtime,handler,context,call,service,io,store=case()
    lifecycle=lifecycle_for(handler,context,service.registry,call.name,recovery_only=True)
    with pytest.raises(UncertainToolInvocationError,match='RECOVERY_UNRECORDED_EFFECT'):
        await lifecycle.replay(call,context,service.policy.decide(call.name,context,call.arguments))
    assert runtime.task_execution_state.operations==();io.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('damage',['native_state','receipt','actor','args','turn'])
async def test_receipt_import_rejects_changed_native_or_original_invocation_facts(damage):
    runtime,handler,context,call,service,io,store=case()
    await execute(runtime,handler,context,call,service)
    state=runtime.task_execution_state;proof=store.proof
    if damage=='native_state':proof['state']='uncertain'
    if damage=='receipt':proof['receipt']={**proof['receipt'],'remote_id':'different'}
    if damage=='actor':proof['tool_result']['tool_result']['audit']['actor_user_id']=str(uuid4())
    if damage=='args':proof['args_hash']='0'*64
    if damage=='turn':proof['turn_id']=str(uuid4())
    with pytest.raises(TaskError,match='UNCONFIRMED'):
        receipt_artifact(state,state.operations[-1],proof,owner_task_id=state.root_task_id)
