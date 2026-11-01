"""Native read exports, real workspace bytes and the completion boundary."""
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.agent.agent_result import AgentResult
from services.agent.tool_output import FileRef,OutputFormat
from services.file_executor import FileExecutor
from services.task_execution.contracts import TaskError
from services.task_execution.controller import ImportNativeResult,TaskController
from services.task_execution.native_results import import_result,read_result
from services.task_execution.reducer import register_artifacts,finish
from services.tools.resource_access import ResourceAccessBoundary,ResourceRule
from tests.test_task_execution_native_results import case


async def exported(tmp_path,monkeypatch):
    from core.config import get_settings
    monkeypatch.setattr(get_settings(),'file_workspace_root',str(tmp_path))
    state,context,runtime,handler,service,call,lifecycle,io=case()
    state=state.model_copy(update={'requirements':(state.requirements[0].model_copy(update={'artifact_kind':'file'}),)})
    runtime.task_execution_state=state
    access=ResourceAccessBoundary((ResourceRule(('read',),directories=('staging',)),),'test-native-export',True)
    context=replace(context,resource_access=access.as_dict())
    files=FileExecutor(str(tmp_path),context.workspace_owner_id,context.org_id)
    path=Path(files.workspace_root)/'staging/report.csv';path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text('capacity\n8\n')
    ref=FileRef(str(path),'report.csv','csv',1,path.stat().st_size,[],created_at=time.time(),ttl_seconds=3600)
    io.return_value=AgentResult('查询导出文件',format=OutputFormat.FILE_REF,file_ref=ref)
    await lifecycle.replay(call,context,service.policy.decide(call.name,context,call.arguments))
    await service.execute(call,context,before_dispatch=lifecycle.begin,on_result=lifecycle.complete)
    record=handler._actor_invocation_store.rows[call.call_id]
    record['arguments']=runtime.task_read_arguments['calls'][call.call_id]['arguments']
    value=ImportNativeResult(expected_revision=state.revision,step_id='query',tool_call_id=call.call_id,kind='file')
    return state,context,service,value,record,path,io


@pytest.mark.asyncio
async def test_real_export_import_read_and_complete_without_repeating_query(tmp_path,monkeypatch):
    state,context,service,value,record,path,io=await exported(tmp_path,monkeypatch)
    artifact,body=import_result(state,value,record,owner_task_id=state.root_task_id,context=context,registry=service.registry)
    assert body['workspace_path']=='staging/report.csv' and str(tmp_path) not in str(body)
    assert read_result(state,artifact,record,context=context)['content_sha256']==body['content_sha256']
    state=register_artifacts(state,[artifact])
    commit=AsyncMock(return_value={'outcome':'saved'})
    reader=SimpleNamespace(native_result=AsyncMock(return_value={'record':record}))
    controller=TaskController(state,owner_task_id=state.root_task_id,commit=commit,artifact_reader=reader,result_context=context)
    outcome=await controller.control('complete_task_step',{'expected_revision':state.revision,'step_id':'query','artifact_ids':[artifact.id]},call_id='complete-export')
    assert outcome.result['ok'] and finish(controller.state).status=='completed'
    io.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('damage',['bytes','missing','expired','boundary','metadata','symlink'])
async def test_changed_or_revoked_export_cannot_complete_delivery(tmp_path,monkeypatch,damage):
    state,context,service,value,record,path,io=await exported(tmp_path,monkeypatch)
    artifact,_=import_result(state,value,record,owner_task_id=state.root_task_id,context=context,registry=service.registry)
    state=register_artifacts(state,[artifact])
    if damage=='bytes':path.write_text('changed bytes')
    if damage=='missing':path.unlink()
    if damage=='expired':record['result']['tool_result']['raw']['file_ref']['created_at']=1
    if damage=='boundary':context=replace(context,resource_access=ResourceAccessBoundary((), 'revoked',True).as_dict())
    if damage=='metadata':record['result']['tool_result']['raw']['file_ref']['row_count']=999
    if damage=='symlink':
        other=tmp_path/'outside.csv';other.write_text('private bytes')
        path.unlink();path.symlink_to(other)
    commit=AsyncMock(return_value={'outcome':'saved'})
    controller=TaskController(state,owner_task_id=state.root_task_id,commit=commit,
        artifact_reader=SimpleNamespace(native_result=AsyncMock(return_value={'record':record})),result_context=context)
    outcome=await controller.control('complete_task_step',{'expected_revision':state.revision,'step_id':'query','artifact_ids':[artifact.id]},call_id='changed-export')
    assert outcome.result['ok'] is False and controller.state==state
    commit.assert_not_awaited();io.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('damage',['path','locator','missing_boundary','actor'])
async def test_export_import_requires_exact_native_locator_and_current_authorization(tmp_path,monkeypatch,damage):
    state,context,service,value,record,path,io=await exported(tmp_path,monkeypatch)
    ref=record['result']['tool_result']['raw']['file_ref']
    if damage=='path':ref['path']=str(tmp_path/'outside.csv')
    if damage=='locator':ref['filename']='other.csv'
    if damage=='missing_boundary':context=replace(context,resource_access={})
    if damage=='actor':context=replace(context,actor_user_id='another-user',workspace_owner_id='another-user')
    with pytest.raises(TaskError):import_result(state,value,record,owner_task_id=state.root_task_id,context=context,registry=service.registry)
    io.assert_awaited_once()
