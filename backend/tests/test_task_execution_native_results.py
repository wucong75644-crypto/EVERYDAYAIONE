"""Managed reads use real Policy/lifecycle/payloads; business and DB are doubles."""
import copy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from services.agent.agent_result import AgentResult
from services.task_execution.contracts import PlanStep,TaskError,WorkPlan
from services.task_execution.controller import ImportNativeResult
from services.task_execution.native_results import lifecycle_for,import_result,read_result,restore_arguments
from services.task_execution.reducer import revise_plan,register_artifacts,complete_step,finish
from services.tools import ToolCall,ToolContext,ToolDispatcher,ToolExecutionService
from services.tools.catalog import build_capability_catalog
from tests.test_task_execution_reducer import state as base_state


class Store:
    def __init__(self):self.rows={}
    def lookup(self,**args):return self.rows.get(args['tool_call_id'])
    def mark_stale(self,**_):return {}
    def begin(self,**args):
        old=self.rows.get(args['tool_call_id'])
        if old:return {'outcome':'replay' if old['status']=='succeeded' else old['status'],'result':old.get('result')}
        self.rows[args['tool_call_id']]={'id':str(uuid4()),**args,'status':'running'}
        return {'outcome':'execute'}
    def complete(self,**args):self.rows[args['tool_call_id']].update(status=args['status'],result=args['result'])


def case():
    initial=base_state()
    state=initial.model_copy(update={'image_binding_version':3,'original_goal':'查询商品数据',
        'requirements':(initial.requirements[0].model_copy(update={'artifact_kind':'data','description':'原查询数据'}),)})
    state=revise_plan(state,WorkPlan(expected_revision=state.revision,steps=(PlanStep(id='query',title='查询商品数据',
        capability='platform.web_search',requirement_ids=('report',)),)))
    context=ToolContext(actor_user_id=str(state.actor_user_id),workspace_owner_id=state.execution_identity.workspace_owner_id,
        org_id=None,context_scope='user',personal_context_allowed=True,agent_domain='general',permission_mode='auto',
        execution_mode='interactive',task_id=str(state.root_task_id),conversation_id=str(state.conversation_id))
    runtime=SimpleNamespace(task_execution_state=state,task_id=str(state.root_task_id),task_read_arguments=None)
    handler=SimpleNamespace(_actor_enabled=True,_actor_invocation_store=Store(),_actor_runtime=runtime,
        _actor_turn_id=str(uuid4()),_actor_execution_token=str(uuid4()))
    registry=build_capability_catalog()
    io=AsyncMock(return_value=AgentResult('实际查询数据',data=[{'product':'风琴包','capacity':'8入'}]))
    service=ToolExecutionService(registry,ToolDispatcher({('legacy','web_search'):io}))
    call=ToolCall('read-original','web_search',{'query':'风琴包商品数据'})
    lifecycle=lifecycle_for(handler,context,registry,call.name)
    return state,context,runtime,handler,service,call,lifecycle,io


async def execute_case():
    state,context,runtime,handler,service,call,lifecycle,io=case()
    decision=service.policy.decide(call.name,context,call.arguments)
    assert await lifecycle.replay(call,context,decision) is None
    result=await service.execute(call,context,before_dispatch=lifecycle.begin,on_result=lifecycle.complete)
    record=handler._actor_invocation_store.rows[call.call_id]
    record['arguments']=runtime.task_read_arguments['calls'][call.call_id]['arguments']
    return state,context,runtime,handler,service,call,lifecycle,io,result,record


@pytest.mark.asyncio
async def test_actual_read_is_journaled_imported_and_reused_without_a_second_business_call():
    state,context,runtime,handler,service,call,lifecycle,io,result,record=await execute_case()
    assert record['status']=='succeeded'
    value=ImportNativeResult(expected_revision=state.revision,step_id='query',tool_call_id=call.call_id)
    artifact,body=import_result(state,value,record,owner_task_id=state.root_task_id,context=context,registry=service.registry)
    assert body['data']==[{'product':'风琴包','capacity':'8入'}]
    completed=complete_step(register_artifacts(state,[artifact]),'query',[artifact.id])
    assert finish(completed).status=='completed'
    assert read_result(completed,artifact,record)['data']==body['data']
    replay=await lifecycle_for(handler,context,service.registry,call.name).replay(call,context,
        service.policy.decide(call.name,context,call.arguments))
    assert replay.execution.replayed and not replay.execution.handler_started
    io.assert_awaited_once()
    assert restore_arguments(runtime.task_read_arguments,task_id=runtime.task_id,turn_id=handler._actor_turn_id)==runtime.task_read_arguments


@pytest.mark.asyncio
@pytest.mark.parametrize('damage',['uncertain','business_error','hash','actor','capability','changed_data'])
async def test_unknown_failed_forged_or_wrong_capability_result_cannot_complete_delivery(damage):
    state,context,_,_,service,call,_,_,_,record=await execute_case()
    record=copy.deepcopy(record)
    value=ImportNativeResult(expected_revision=state.revision,step_id='query',tool_call_id=call.call_id)
    if damage=='uncertain':record['status']='uncertain'
    if damage=='business_error':record['result']['tool_result']['status']='error'
    if damage=='hash':record['args_hash']='0'*64
    if damage=='actor':record['result']['tool_result']['audit']['actor_user_id']=str(uuid4())
    if damage=='capability':record['tool_name']='generate_image'
    if damage=='changed_data':
        artifact,_=import_result(state,value,record,owner_task_id=state.root_task_id,context=context,registry=service.registry)
        record['result']['tool_result']['raw']['data'][0]['capacity']='fake'
        with pytest.raises(TaskError,match='RESULT_CHANGED'):read_result(state,artifact,record)
    else:
        with pytest.raises(TaskError):import_result(state,value,record,owner_task_id=state.root_task_id,context=context,registry=service.registry)


@pytest.mark.asyncio
@pytest.mark.parametrize('empty',[False,True])
async def test_cache_consumption_is_journaled_with_cached_provenance_and_no_handler_execution(empty):
    state,context,runtime,handler,service,call,lifecycle,io=case()
    decision=service.policy.decide(call.name,context,call.arguments)
    from services.tools import ToolResult
    data=[] if empty else [{'capacity':'8入'}]
    cached=ToolResult.wrap(AgentResult('缓存数据',status='empty' if empty else 'success',data=data),call=call,context=context,decision=decision)
    cached=cached.reused(call=call,context=context,decision=decision,cached=True)
    await lifecycle.replay(call,context,decision)
    actual=await lifecycle.consume_cached(call,context,decision,cached)
    record=handler._actor_invocation_store.rows[call.call_id]
    record['arguments']=dict(call.arguments)
    assert actual.execution.cached and not actual.execution.handler_started
    assert record['status']=='succeeded' and record['result']['tool_result']['execution']['cached'] is True
    value=ImportNativeResult(expected_revision=state.revision,step_id='query',tool_call_id=call.call_id)
    _,body=import_result(state,value,record,owner_task_id=state.root_task_id,context=context,registry=service.registry)
    assert body['cached'] is True and body['data']==data
    assert body['result_status']==('empty' if empty else 'success')
    io.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_task_keeps_existing_read_lifecycle_and_argument_witness_is_owner_bound():
    state,context,runtime,handler,service,call,_,_=case()
    runtime.task_execution_state=state.model_copy(update={'image_binding_version':1})
    lifecycle=lifecycle_for(handler,context,service.registry,call.name)
    decision=service.policy.decide(call.name,context,call.arguments)
    assert lifecycle._enabled(decision) is False
    assert runtime.task_read_arguments is None
    runtime.task_execution_state=state
    await lifecycle_for(handler,context,service.registry,call.name).replay(call,context,decision)
    with pytest.raises(TaskError,match='SCOPE_DENIED'):
        restore_arguments(runtime.task_read_arguments,task_id=str(uuid4()),turn_id=handler._actor_turn_id)


@pytest.mark.asyncio
@pytest.mark.parametrize('empty',[False,True])
async def test_real_tool_runtime_records_authorized_erp_cache_consumption_without_querying_twice(empty):
    from tests.tool_runtime_support import MockHandlerExecutor
    from services.agent.tool_result_cache import ToolResultCache
    from services.kuaimai.registry import TOOL_REGISTRIES
    state,_,runtime,handler,_,_,_,_=case()
    org=uuid4();state=state.model_copy(update={'org_id':org})
    runtime.task_execution_state=state
    executor=MockHandlerExecutor(user_id=str(state.actor_user_id),org_id=str(org),conversation_id=str(state.conversation_id),
        task_id=str(state.root_task_id),agent_domain='erp',permission_mode='auto')
    executor.handler.return_value=AgentResult('实际查询数据',data=[{'capacity':'8入'}])
    if empty:executor.handler.return_value=AgentResult('未找到匹配数据',status='empty',data=[])
    tool_runtime=executor.tool_runtime
    action=next(key for key,entry in TOOL_REGISTRIES['erp_info_query'].items() if not entry.is_write)
    args={'action':action}
    cache=ToolResultCache()
    first_context=tool_runtime.context('first-read')
    first_lifecycle=lifecycle_for(handler,first_context,tool_runtime.registry,'erp_info_query')
    first=await tool_runtime.execute('erp_info_query',args,call_id='first-read',cache=cache,lifecycle=first_lifecycle)
    assert first.status==('empty' if empty else 'success'),first.display
    second_context=tool_runtime.context('cached-read')
    second=await tool_runtime.execute('erp_info_query',args,call_id='cached-read',cache=cache,
        lifecycle=lifecycle_for(handler,second_context,tool_runtime.registry,'erp_info_query'))
    assert second.status==first.status and second.execution.cached and not second.execution.handler_started
    executor.handler.assert_awaited_once()
    record=handler._actor_invocation_store.rows['cached-read']
    assert record['status']=='succeeded' and record['result']['tool_result']['execution']['cached'] is True


@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['text','data'])
async def test_empty_result_is_real_delivery_and_its_status_cannot_change_after_import(kind):
    state,context,runtime,handler,service,call,lifecycle,io=case()
    io.return_value=AgentResult('未找到匹配数据',status='empty',data=[])
    await lifecycle.replay(call,context,service.policy.decide(call.name,context,call.arguments))
    result=await service.execute(call,context,before_dispatch=lifecycle.begin,on_result=lifecycle.complete)
    record=handler._actor_invocation_store.rows[call.call_id]
    record['arguments']=runtime.task_read_arguments['calls'][call.call_id]['arguments']
    artifact,body=import_result(state,ImportNativeResult(expected_revision=state.revision,step_id='query',tool_call_id=call.call_id,kind=kind),
        record,owner_task_id=state.root_task_id,context=context,registry=service.registry)
    assert result.status==body['result_status']=='empty'
    assert read_result(state,artifact,record)['result_status']=='empty'
    record['result']['tool_result']['status']='success'
    record['result']['tool_result']['raw']['status']='success'
    with pytest.raises(TaskError,match='RESULT_CHANGED'):read_result(state,artifact,record)


@pytest.mark.asyncio
async def test_import_rechecks_current_tool_access_and_keeps_real_data_on_denial():
    state,context,_,_,service,call,_,_,_,record=await execute_case()
    value=ImportNativeResult(expected_revision=state.revision,step_id='query',tool_call_id=call.call_id)
    revoked=replace(context,authorized_tool_names=frozenset())
    with pytest.raises(TaskError,match='READ_REQUIRED'):
        import_result(state,value,record,owner_task_id=state.root_task_id,context=revoked,registry=service.registry)
    assert record['result']['tool_result']['raw']['data']==[{'product':'风琴包','capacity':'8入'}]


@pytest.mark.asyncio
@pytest.mark.parametrize('damage',['text','execution','cancelled','error','actor','raw_status'])
async def test_restored_read_revalidates_native_text_and_execution_facts(damage):
    state,context,_,_,service,call,_,_,_,record=await execute_case()
    value=ImportNativeResult(expected_revision=state.revision,step_id='query',tool_call_id=call.call_id,kind='text')
    artifact,_=import_result(state,value,record,owner_task_id=state.root_task_id,context=context,registry=service.registry)
    assert read_result(state,artifact,record)['text']==artifact.inline_text
    extension=record['result']['tool_result']
    if damage=='text':extension['raw']['summary']='替换后的查询结果'
    if damage=='execution':extension['execution']['status']='uncertain'
    if damage=='cancelled':extension['execution']['cancelled']=True
    if damage=='error':extension['error']={'message':'failed'}
    if damage=='actor':extension['audit']['actor_user_id']=str(uuid4())
    if damage=='raw_status':extension['raw']['status']='error'
    with pytest.raises(TaskError,match='RESULT_CHANGED'):read_result(state,artifact,record)


@pytest.mark.asyncio
@pytest.mark.parametrize('name',['local_data','local_product_stats','local_stock_query','local_product_identify',
    'local_platform_map_query','local_compare_stats','local_shop_list','local_warehouse_list','local_supplier_list',
    'knowledge_outline','knowledge_search','knowledge_read','knowledge_follow_reference','knowledge_discover'])
@pytest.mark.parametrize('kind',['text','data'])
async def test_registered_local_and_knowledge_reads_keep_native_scope_results_and_current_permissions(name,kind):
    state,context,runtime,handler,_,_,_,io=case()
    registry=build_capability_catalog();spec=registry.require(name);org=uuid4() if name.startswith('local_') else None
    state=state.model_copy(update={'org_id':org,'steps':(state.steps[0].model_copy(update={'capability':spec.capability}),),
        'requirements':(state.requirements[0].model_copy(update={'artifact_kind':kind}),)})
    runtime.task_execution_state=state
    context=replace(context,org_id=str(org) if org else None,agent_domain=spec.domain,
        feature_flags={'knowledge_refs_present':True,'knowledge_auto_discovery':True})
    call=ToolCall('original-read',name,{'query':'已授权查询','root_handle':'native-root','item_handles':['native-item']})
    service=ToolExecutionService(registry,ToolDispatcher({('legacy',spec.handler_key):io}))
    lifecycle=lifecycle_for(handler,context,registry,name)
    decision=service.policy.decide(name,context,call.arguments)
    assert decision.outcome=='allow',decision.reason
    await lifecycle.replay(call,context,decision)
    result=await service.execute(call,context,before_dispatch=lifecycle.begin,on_result=lifecycle.complete)
    assert result.status=='success'
    record=handler._actor_invocation_store.rows[call.call_id]
    record['arguments']=runtime.task_read_arguments['calls'][call.call_id]['arguments']
    value=ImportNativeResult(expected_revision=state.revision,step_id='query',tool_call_id=call.call_id,kind=kind)
    artifact,body=import_result(state,value,record,owner_task_id=state.root_task_id,context=context,registry=registry)
    assert read_result(state,artifact,record)['result_status']=='success'
    assert body['data']==[{'product':'风琴包','capacity':'8入'}] if kind=='data' else bool(body['text'])
    assert finish(complete_step(register_artifacts(state,[artifact]),'query',[artifact.id])).status=='completed'
    replay=await lifecycle_for(handler,context,registry,name).replay(call,context,decision)
    assert replay.execution.replayed;io.assert_awaited_once()
    with pytest.raises(TaskError,match='READ_REQUIRED'):
        import_result(state,value,record,owner_task_id=state.root_task_id,
            context=replace(context,authorized_tool_names=frozenset()),registry=registry)
