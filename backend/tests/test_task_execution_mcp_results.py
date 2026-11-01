"""The existing reviewed synthetic read Connector; no real transport or token."""
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from services.task_execution.contracts import TaskError
from services.task_execution.controller import ImportNativeResult
from services.task_execution.controller import TaskController
from services.task_execution.contracts import PlanStep,WorkPlan
from services.task_execution.native_results import lifecycle_for,import_result,read_result
from services.task_execution.reducer import register_artifacts,complete_step,finish
from services.task_execution.reducer import revise_plan
from services.tools import ToolCall,ToolDispatcher,ToolExecutionService
from services.tools.catalog import build_capability_catalog
from services.tools.mcp_allowlist import TOOL_NAME
from services.tools.mcp_boundary import normalize_result
from services.tools.mcp_executor import MCPExecutor
from tests.test_task_execution_native_results import case


async def mcp_result():
    state,context,runtime,handler,_,_,_,_=case()
    registry=build_capability_catalog();spec=registry.require(TOOL_NAME);org=uuid4()
    state=state.model_copy(update={'org_id':org,'steps':(state.steps[0].model_copy(update={'capability':spec.capability}),),
        'requirements':(state.requirements[0].model_copy(update={'artifact_kind':'text'}),)})
    runtime.task_execution_state=state
    context=replace(context,org_id=str(org),feature_flags={'mcp_connectors_enabled':True,'mcp_connector_test_readonly_enabled':True})
    call=ToolCall('native-mcp',TOOL_NAME,{'record_id':'sample'})
    io=AsyncMock(return_value=normalize_result({'content':[{'type':'text','text':'synthetic native sample'}]}))
    connector=MCPExecutor(lambda _:context)
    connector.execute=io  # Local double for the reviewed transport boundary.
    service=ToolExecutionService(registry,ToolDispatcher({},mcp_executor=connector))
    decision=service.policy.decide(call.name,context,call.arguments)
    assert decision.outcome=='allow'
    lifecycle=lifecycle_for(handler,context,registry,call.name)
    await lifecycle.replay(call,context,decision)
    result=await service.execute(call,context,before_dispatch=lifecycle.begin,on_result=lifecycle.complete)
    assert result.status=='success'
    record=handler._actor_invocation_store.rows[call.call_id]
    record['arguments']=runtime.task_read_arguments['calls'][call.call_id]['arguments']
    value=ImportNativeResult(expected_revision=state.revision,step_id='query',tool_call_id=call.call_id,kind='text')
    return state,context,registry,record,value,io


@pytest.mark.asyncio
async def test_reviewed_mcp_read_is_native_text_evidence_with_original_connector_identity():
    state,context,registry,record,value,io=await mcp_result()
    artifact,body=import_result(state,value,record,owner_task_id=state.root_task_id,context=context,registry=registry)
    assert 'synthetic native sample' in body['text']
    assert read_result(state,artifact,record,context=context)['text']==body['text']
    assert finish(complete_step(register_artifacts(state,[artifact]),'query',[artifact.id])).status=='completed'
    io.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('damage',['connector','capability','remote_tool','feature_revoked','no_context','wrong_actor'])
async def test_connector_rebinding_or_current_authorization_changes_cannot_reuse_imported_evidence(damage):
    state,context,registry,record,value,io=await mcp_result()
    artifact,_=import_result(state,value,record,owner_task_id=state.root_task_id,context=context,registry=registry)
    audit=record['result']['tool_result']['audit']
    if damage=='connector':audit['connector_id']='another-connector'
    if damage=='capability':audit['capability']='another.read'
    if damage=='remote_tool':audit['remote_tool_name']='another_remote'
    if damage=='feature_revoked':context=replace(context,feature_flags={'mcp_connectors_enabled':False})
    if damage=='no_context':context=None
    if damage=='wrong_actor':context=replace(context,actor_user_id='other',workspace_owner_id='other')
    with pytest.raises(TaskError):read_result(state,artifact,record,context=context)
    io.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('control',['read_task_text','complete_task_step','reuse_task_text'])
@pytest.mark.parametrize('enabled',[True,False])
async def test_original_text_controls_preserve_connector_checks_and_validation_tags(control,enabled):
    state,context,registry,record,value,io=await mcp_result()
    artifact,_=import_result(state,value,record,owner_task_id=state.root_task_id,context=context,registry=registry)
    state=register_artifacts(state,[artifact])
    if control=='reuse_task_text':
        state=complete_step(state,'query',[artifact.id])
        state=revise_plan(state,WorkPlan(expected_revision=state.revision,
            steps=(*state.steps,PlanStep(id='reuse',title='复用真实查询'))))
    if not enabled:context=replace(context,feature_flags={'mcp_connectors_enabled':False})
    commit=AsyncMock(return_value={'outcome':'saved'})
    controller=TaskController(state,owner_task_id=state.root_task_id,commit=commit,result_context=context,
        artifact_reader=SimpleNamespace(native_result=AsyncMock(return_value={'record':record})))
    args={'expected_revision':state.revision}
    if control=='read_task_text':args['artifact_id']=artifact.id
    if control=='complete_task_step':args.update(step_id='query',artifact_ids=[artifact.id])
    if control=='reuse_task_text':args.update(step_id='reuse',source_logical_task_id=str(state.root_task_id),source_artifact_id=artifact.id)
    outcome=await controller.control(control,args,call_id='current-control')
    assert outcome.result['ok'] is enabled,outcome.result
    if not enabled:
        assert controller.state==state;commit.assert_not_awaited()
    elif control=='reuse_task_text':
        reused=next(a for a in controller.state.artifacts if a.id==outcome.result['reused_artifact_id'])
        assert set(artifact.validations)<=set(reused.validations)
    io.assert_awaited_once()


@pytest.mark.asyncio
async def test_cross_root_text_reuse_cannot_discard_native_connector_identity():
    state,context,registry,record,value,io=await mcp_result()
    source,_=import_result(state,value,record,owner_task_id=state.root_task_id,context=context,registry=registry)
    reader=SimpleNamespace(load_text=AsyncMock(return_value=source),native_result=AsyncMock())
    commit=AsyncMock(return_value={'outcome':'saved'})
    controller=TaskController(state,owner_task_id=state.root_task_id,commit=commit,result_context=context,artifact_reader=reader)
    outcome=await controller.control('reuse_task_text',{'expected_revision':state.revision,'step_id':'query',
        'source_logical_task_id':str(uuid4()),'source_artifact_id':source.id},call_id='cross-root-reuse')
    assert not outcome.result['ok'] and outcome.result['code']=='TASK_NATIVE_CONNECTOR_REUSE_REQUIRES_IMPORT'
    assert outcome.result['instruction'] and controller.state==state
    commit.assert_not_awaited();reader.native_result.assert_not_awaited();io.assert_awaited_once()
