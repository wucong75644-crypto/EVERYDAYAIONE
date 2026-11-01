"""Managed ERP uses actual dispatcher boundaries; no external IO or paid calls."""
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock
from uuid import uuid4

import httpx
import pytest

pytestmark = pytest.mark.usefixtures('erp_write_adapter_enabled')

from services.agent.agent_result import AgentResult
from services.kuaimai.client import KuaiMaiClient
from services.kuaimai.dispatcher import ErpDispatcher
from services.task_execution.contracts import PlanStep,TaskError
from services.task_execution.controller import TaskController
from services.task_execution.native_writes import NativeWriteSession,verified_phase
from services.task_execution.reducer import finish
from services.tools import ToolCall,ToolDispatcher,ToolExecutionService
from services.tools.result import UncertainToolInvocationError
from tests.test_task_execution_reach_operations import Store as BaseStore,case as base_case,execute


class Store(BaseStore):
    async def native_write_receipt(self,state,operation_id):
        if self.proof is None:raise TaskError('TASK_NATIVE_WRITE_PROOF_UNCONFIRMED')
        return self.proof
    async def effect_result(self,state,operation_id):return {'result':self.payloads[operation_id]}


def case(*,body=None,mode='ok'):
    runtime,handler,ctx,_,_,_,_=base_case()
    state=runtime.task_execution_state.model_copy(update={'original_goal':'新增商品标签并返回回执',
        'steps':(PlanStep(id='write',title='新增商品标签',capability='platform.erp_execute',requirement_ids=('report',)),)})
    runtime.task_execution_state=state;store=Store(state,uuid4());runtime.task_operation_store=store
    ctx=replace(ctx,feature_flags={},permission_mode='ask',agent_domain='erp',call_id='erp-original')
    call=ToolCall('erp-original','erp_execute',{'category':'product','action':'tag_add','params':{} if mode=='missing' else {'name':'原标签'}})
    response=body if body is not None else {'success':True,'data':{'id':'remote-tag-1'}}
    client=SimpleNamespace(request_once=AsyncMock(return_value=response),request_with_retry=AsyncMock())
    if mode=='timeout':client.request_once.side_effect=httpx.ReadTimeout('lost response')
    dispatcher=ErpDispatcher(client)
    def rpc(name,params):
        assert name=='record_agent_task_native_write'
        if params['p_phase']=='dispatched':store.proof.update(phase='dispatched',dispatch=params['p_value'])
        else:store.proof.update(phase='returned',response=params['p_value'])
        return SimpleNamespace(execute=lambda:SimpleNamespace(data={'outcome':'recorded'}))
    async def native(arguments):
        operation=runtime.task_execution_state.operations[-1]
        store.proof={'operation_id':operation.id,'native_id':str(uuid4()),'task_id':str(operation.owner_task_id),
            'turn_id':str(operation.owner_turn_id),'tool_call_id':operation.invocation_id,'args_hash':operation.arguments_sha256,
            'actor_user_id':str(state.actor_user_id),'org_id':str(state.org_id),'domain':'erp','phase':'prepared',
            'intent':{'category':'product','action':'tag_add'},'dispatch':None,'response':None}
        session=NativeWriteSession(SimpleNamespace(db=SimpleNamespace(rpc=rpc)),
            {'operation_id':operation.id,'dispatch_token':str(uuid4())},store.proof['intent'])
        dispatcher._native_write_observer=session.observe
        if mode=='format_loss':dispatcher._format_response=Mock(side_effect=RuntimeError('format delivery lost'))
        return await dispatcher.execute('erp_product_query','tag_add',arguments['params'])
    io=AsyncMock(side_effect=native)
    service=ToolExecutionService(store.registry,ToolDispatcher({('legacy','erp_execute'):io}))
    return runtime,handler,ctx,call,service,io,store,client


def controller(runtime,ctx,store):
    return TaskController(runtime.task_execution_state,owner_task_id=runtime.task_execution_state.root_task_id,
        operation_store=store,result_context=ctx,commit=AsyncMock(return_value={'outcome':'saved'}))


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['ok','format_loss'])
async def test_real_response_import_and_lost_format_recovery_never_resubmit(mode):
    runtime,handler,ctx,call,service,io,store,client=case(mode=mode)
    result=await execute(runtime,handler,ctx,call,service)
    op=runtime.task_execution_state.operations[-1]
    assert op.status==('completed' if mode=='ok' else 'uncertain')
    ctrl=controller(runtime,ctx,store)
    read=await ctrl.control('read_task_write_result',{'expected_revision':ctrl.state.revision,'operation_id':op.id},call_id='read')
    assert read.result['ok'] and read.result['operation_status']=='completed',read.result
    imported=await ctrl.control('import_task_receipt',{'expected_revision':ctrl.state.revision,'operation_id':op.id},call_id='import')
    assert imported.result['ok'],imported.result
    done=await ctrl.control('complete_task_step',{'expected_revision':ctrl.state.revision,'step_id':'write',
        'artifact_ids':[imported.result['imported_artifact_id']]},call_id='done')
    assert done.result['ok'] and finish(ctrl.state).status=='completed',done.result
    if mode=='ok':
        replay=await execute(runtime,handler,ctx,ToolCall('another',call.name,dict(call.arguments)),service)
        assert replay.execution.replayed
    assert result.status==('success' if mode=='ok' else 'error')
    client.request_once.assert_awaited_once();client.request_with_retry.assert_not_awaited();io.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('body',[{'success':True,'data':{'taskId':'job-1'}},{'success':True,'partial':True},
    {'success':True,'data':{'failCount':1}},{'success':True,'status':'pending'},{'success':'true'}, {'success':False}])
async def test_async_partial_or_nonboolean_success_cannot_deliver(body):
    runtime,handler,ctx,call,service,io,store,client=case(body=body)
    await execute(runtime,handler,ctx,call,service)
    ctrl=controller(runtime,ctx,store);op=ctrl.state.operations[-1]
    result=await ctrl.control('import_task_receipt',{'expected_revision':ctrl.state.revision,'operation_id':op.id},call_id='import')
    assert not result.result['ok'];assert op.status in {'accepted','uncertain'}
    with pytest.raises(TaskError):finish(ctrl.state)
    client.request_once.assert_awaited_once();client.request_with_retry.assert_not_awaited()


@pytest.mark.asyncio
async def test_timeout_keeps_unknown_and_new_call_id_cannot_send_again():
    runtime,handler,ctx,call,service,io,store,client=case(mode='timeout')
    await execute(runtime,handler,ctx,call,service)
    assert runtime.task_execution_state.operations[-1].status=='uncertain' and store.proof['phase']=='dispatched'
    with pytest.raises(UncertainToolInvocationError):
        await execute(runtime,handler,ctx,ToolCall('new',call.name,dict(call.arguments)),service)
    client.request_once.assert_awaited_once();client.request_with_retry.assert_not_awaited();io.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_parameter_is_positive_not_started_evidence_and_same_call_replays_error():
    runtime,handler,ctx,call,service,io,store,client=case(mode='missing')
    await execute(runtime,handler,ctx,call,service)
    assert runtime.task_execution_state.operations[-1].status=='failed_before_dispatch'
    replay=await execute(runtime,handler,ctx,call,service)
    assert replay.status=='error' and replay.execution.replayed
    client.request_once.assert_not_awaited();io.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('changes',[{'org_id':str(uuid4())},{'actor_user_id':'different-actor','workspace_owner_id':'different-actor'},
    {'workspace_owner_id':'channel-owner','context_scope':'channel','personal_context_allowed':False},{'permission_mode':'plan'}])
async def test_current_scope_or_permission_revocation_blocks_receipt_import(changes):
    runtime,handler,ctx,call,service,io,store,client=case();await execute(runtime,handler,ctx,call,service)
    ctrl=controller(runtime,replace(ctx,**changes),store);op=ctrl.state.operations[-1]
    result=await ctrl.control('import_task_receipt',{'expected_revision':ctrl.state.revision,'operation_id':op.id},call_id='import')
    assert not result.result['ok'];ctrl.commit.assert_not_awaited();client.request_once.assert_awaited_once()


@pytest.mark.asyncio
async def test_sdk_single_attempt_really_bypasses_inner_tenacity_retry():
    client=KuaiMaiClient(app_key='local',app_secret='local',access_token='local')
    http=SimpleNamespace(post=AsyncMock(side_effect=httpx.ReadTimeout('unknown')),is_closed=False)
    client._client=http
    with pytest.raises(httpx.ReadTimeout):await client.request_once('erp.item.tag.add',{'name':'原标签'})
    http.post.assert_awaited_once()


@pytest.mark.asyncio
async def test_legacy_sdk_request_still_uses_original_retry_wrapper():
    client=KuaiMaiClient(app_key='local',app_secret='local',access_token='local')
    client.request_once=AsyncMock(return_value={'success':True})
    assert await client.request('erp.item.tag.add',{'name':'原标签'})=={'success':True}
    client.request_once.assert_awaited_once()


def test_unreviewed_erp_method_success_cannot_claim_final_business_delivery():
    proof={'phase':'returned','dispatch':{'method':'erp.trade.create'},
        'response':{'method':'erp.trade.create','body':{'success':True,'data':{'id':'order-1'}}}}
    assert verified_phase(proof)=='accepted'


@pytest.mark.asyncio
@pytest.mark.parametrize('missing',[False,True])
async def test_original_erp_executor_retains_redis_guard_and_allows_proven_parameter_correction(missing):
    from unittest.mock import patch
    from services.agent.erp_tool_executor import ErpToolMixin
    class Executor(ErpToolMixin):pass
    executor=Executor();executor.user_id=str(uuid4())
    client=SimpleNamespace(request_once=AsyncMock(return_value={'success':True,'data':{'id':'remote-tag-1'}}))
    dispatcher=ErpDispatcher(client);dispatcher.close=AsyncMock()
    executor._get_erp_dispatcher=AsyncMock(return_value=dispatcher)
    redis=SimpleNamespace(get=AsyncMock(return_value=None),set=AsyncMock())
    rpc=Mock(return_value=SimpleNamespace(execute=lambda:SimpleNamespace(data={'outcome':'recorded'})))
    session=NativeWriteSession(SimpleNamespace(db=SimpleNamespace(rpc=rpc)),
        {'operation_id':'a'*64,'dispatch_token':str(uuid4())},{'category':'product','action':'tag_add'})
    with patch('services.task_execution.native_writes.prepare_erp',new=AsyncMock(return_value=session)), \
         patch('core.redis.get_redis',new=AsyncMock(return_value=redis)), \
         patch('core.redis.RedisClient.acquire_lock',new=AsyncMock(return_value='native-lock')), \
         patch('core.redis.RedisClient.release_lock',new=AsyncMock()) as release:
        result=await executor._erp_dispatch('erp_execute',{'category':'product','action':'tag_add','params':{} if missing else {'name':'原标签'}})
    assert session.dispatched is (not missing)
    if missing:
        assert result.status=='error';redis.set.assert_not_awaited();client.request_once.assert_not_awaited()
    else:
        assert result.status=='success';redis.set.assert_awaited_once();client.request_once.assert_awaited_once()
    release.assert_awaited_once();dispatcher.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('fail_save',[False,True])
async def test_write_recovery_state_and_assistant_tool_pairs_commit_atomically(fail_save):
    import copy
    import json
    from tests.test_task_execution_model_bridge import setup_case,execute as control_execute,call as control_call
    native,handler,ctx,call,service,io,store,client=case(mode='format_loss')
    await execute(native,handler,ctx,call,service)
    initial=native.task_execution_state;operation=initial.operations[-1];snapshots=[]
    async def checkpoint(point,payload):
        snapshots.append(copy.deepcopy(payload))
        if fail_save:raise RuntimeError('checkpoint unconfirmed')
        return {'outcome':'saved'}
    runtime,prepared,model_handler,sink=setup_case(checkpoint,initial=initial)
    runtime.task_operation_store=store;runtime.task_result_context_provider=AsyncMock(return_value=ctx)
    calls=[control_call('read_task_write_result',{'expected_revision':initial.revision,'operation_id':operation.id})]
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
    client.request_once.assert_awaited_once()


@pytest.mark.asyncio
async def test_imported_write_receipt_is_revalidated_before_completing_step():
    runtime,handler,ctx,call,service,io,store,client=case();await execute(runtime,handler,ctx,call,service)
    ctrl=controller(runtime,ctx,store);operation=ctrl.state.operations[-1]
    imported=await ctrl.control('import_task_receipt',{'expected_revision':ctrl.state.revision,'operation_id':operation.id},call_id='import')
    assert imported.result['ok']
    store.proof['response']={'method':'erp.item.tag.add','body':{'success':True,'data':{'id':'changed-native-id'}}}
    before=ctrl.state;ctrl.commit.reset_mock()
    done=await ctrl.control('complete_task_step',{'expected_revision':ctrl.state.revision,'step_id':'write',
        'artifact_ids':[imported.result['imported_artifact_id']]},call_id='complete')
    assert not done.result['ok'] and ctrl.state==before;ctrl.commit.assert_not_awaited()
    client.request_once.assert_awaited_once()


@pytest.mark.parametrize('data',[
    {'failCount':'1'}, {'nested':{'failedCount':2}}, {'results':[{'id':'ok'},{'success':False}]},
    {'results':[{'jobId':'async-1'}]}, {'status':' PROCESSING '}, {'partial':'true'},
    {'errorList':[{'message':'one item failed'}]}, {'isSuccess':False},
    {'failCount':None}, {'failedCount':'unknown'}, {'result':False},
])
def test_nested_batch_async_or_unconfirmed_counts_cannot_complete_even_an_admitted_method(data):
    proof={'phase':'returned','dispatch':{'method':'erp.item.tag.add'},
        'response':{'method':'erp.item.tag.add','body':{'success':True,'data':data}}}
    assert verified_phase(proof)=='accepted'


@pytest.mark.parametrize('data',[{'id':'tag-1'},{'failCount':0},{'errorCount':'0'},{'nested':{'failedCount':'0.0'}}])
def test_verified_synchronous_receipt_preserves_original_completion_contract(data):
    proof={'phase':'returned','dispatch':{'method':'erp.item.tag.add'},
        'response':{'method':'erp.item.tag.add','body':{'success':True,'data':data}}}
    assert verified_phase(proof)=='completed'


def test_oversized_nested_receipt_requires_followup_without_recursing_or_inventing_success():
    proof={'phase':'returned','dispatch':{'method':'erp.item.tag.add'},
        'response':{'method':'erp.item.tag.add','body':{'success':True,'data':[{} for _ in range(3000)]}}}
    assert verified_phase(proof)=='accepted'
