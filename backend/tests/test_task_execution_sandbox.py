"""Real sandbox host, files and effect contracts; worker/upload are local doubles."""
import hashlib
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock
from uuid import uuid4

import pytest

from services.file_executor import FileExecutor
from services.sandbox.executor import SandboxExecutor
from services.sandbox.kernel_manager import KernelManager
from services.task_execution.contracts import PlanStep,TaskError
from services.task_execution.controller import TaskController
from services.task_execution.native_results import lifecycle_for
from services.task_execution.reducer import finish
from services.tools import ToolCall,ToolDispatcher,ToolExecutionService
from services.tools.resource_access import ResourceAccessBoundary,ResourceRule
from tests.test_task_execution_reach_operations import case as reach_case,Store as BaseStore,execute


class Store(BaseStore):
    async def effect_result(self,state,operation_id):
        op=next(op for op in state.operations if op.id==operation_id)
        return {'id':str(uuid4()),'task_id':str(op.owner_task_id),'turn_id':str(op.owner_turn_id),
            'tool_call_id':op.invocation_id,'tool_name':op.tool_name,'args_hash':op.arguments_sha256,
            'status':'succeeded' if op.status in {'completed','failed_before_dispatch'} else 'uncertain','result':self.payloads.get(op.id)}


class Kernel:
    def __init__(self,path,*,mode='ok'):self.path=path;self.mode=mode;self.calls=0;self.destroy=AsyncMock()
    async def get_or_create(self,*_,**__):return self.mode!='unavailable'
    async def execute(self,conversation,code,timeout,**options):
        self.calls+=1
        if self.mode=='crashed':return 'crashed','❌ worker crashed',[]
        if 'response_observer' in options:options['response_observer']({'request_id':'local-worker-reply',
            'code_sha256':hashlib.sha256(code.encode()).hexdigest(),'status':self.mode})
        if self.mode=='error':return 'error','❌ NameError: missing variable',[]
        import zipfile
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with zipfile.ZipFile(self.path,'w') as archive:archive.writestr('report.txt','8入')
        return 'ok','export complete',[{'kind':'file','name':self.path.name,'path':'下载/'+self.path.name,
            'size':self.path.stat().st_size,'workspace_path':'下载/'+self.path.name}]


def case(tmp_path,monkeypatch,*,mode='ok'):
    from core.config import get_settings
    monkeypatch.setattr(get_settings(),'file_workspace_root',str(tmp_path))
    runtime,handler,context,_,_,_,_=reach_case()
    old=runtime.task_execution_state
    state=old.model_copy(update={'requirements':(old.requirements[0].model_copy(update={'artifact_kind':'file'}),),
        'steps':(PlanStep(id='code',title='导出文件',capability='sandbox.code.execute',requirement_ids=('report',)),)})
    runtime.task_execution_state=state
    store=Store(state,uuid4());handler._actor_turn_id=str(store.turn);runtime.task_operation_store=store
    access=ResourceAccessBoundary((ResourceRule(('read','list'),directories=('.',)),),'local-test',True)
    context=replace(context,feature_flags={'sandbox_enabled':True},resource_access=access.as_dict())
    files=FileExecutor(str(tmp_path),context.workspace_owner_id,context.org_id)
    path=Path(files.workspace_root)/'下载/report.zip';kernel=Kernel(path,mode=mode)
    sandbox=SandboxExecutor(output_dir=str(path.parent),workspace_dir=files.workspace_root,conversation_id=context.conversation_id,
        kernel_manager=kernel,managed_task=True)
    sandbox._upload_payload_files=AsyncMock()  # No OSS/network in this component test.
    async def run(args):return await sandbox.execute(args['code'],args.get('description',''))
    io=AsyncMock(side_effect=run)
    service=ToolExecutionService(store.registry,ToolDispatcher({('legacy','code_execute'):io}))
    call=ToolCall('original-code','code_execute',{'code':"print('export')",'description':'导出原报告'})
    return runtime,handler,context,call,service,io,store,kernel,path


@pytest.mark.asyncio
async def test_code_output_is_real_file_and_replays_without_running_worker_twice(tmp_path,monkeypatch):
    runtime,handler,context,call,service,io,store,kernel,path=case(tmp_path,monkeypatch)
    result=await execute(runtime,handler,context,call,service)
    assert result.status=='success' and runtime.task_execution_state.operations[-1].status=='completed'
    new=ToolCall('new-id',call.name,dict(call.arguments))
    replay=await execute(runtime,handler,replace(context,call_id=new.call_id),new,service)
    assert replay.execution.replayed and kernel.calls==1;io.assert_awaited_once()
    controller=TaskController(runtime.task_execution_state,owner_task_id=runtime.task_execution_state.root_task_id,
        operation_store=store,result_context=context,commit=AsyncMock(return_value={'outcome':'saved'}))
    op=controller.state.operations[-1]
    found=await controller.control('read_task_code_result',{'expected_revision':controller.state.revision,'operation_id':op.id},call_id='inspect')
    assert found.result['outputs'][0]['workspace_path']=='下载/report.zip'
    assert 'path' not in found.result['outputs'][0]
    imported=await controller.control('import_task_code_result',{'expected_revision':controller.state.revision,'operation_id':op.id},call_id='import')
    assert imported.result['ok'],imported.result
    done=await controller.control('complete_task_step',{'expected_revision':controller.state.revision,'step_id':'code',
        'artifact_ids':[imported.result['imported_artifact_id']]},call_id='complete')
    assert done.result['ok'] and finish(controller.state).status=='completed'


@pytest.mark.asyncio
@pytest.mark.parametrize('damage',['bytes','missing','permission'])
async def test_changed_or_revoked_emitted_file_cannot_complete(tmp_path,monkeypatch,damage):
    runtime,handler,context,call,service,io,store,kernel,path=case(tmp_path,monkeypatch)
    await execute(runtime,handler,context,call,service)
    controller=TaskController(runtime.task_execution_state,owner_task_id=runtime.task_execution_state.root_task_id,
        operation_store=store,result_context=context,commit=AsyncMock(return_value={'outcome':'saved'}))
    imported=await controller.control('import_task_code_result',{'expected_revision':controller.state.revision,
        'operation_id':controller.state.operations[-1].id},call_id='import')
    assert imported.result['ok'],imported.result
    if damage=='bytes':path.write_bytes(b'replaced')
    if damage=='missing':path.unlink()
    if damage=='permission':controller.result_context=replace(context,resource_access=ResourceAccessBoundary((),'revoked',True).as_dict())
    previous=controller.state
    done=await controller.control('complete_task_step',{'expected_revision':previous.revision,'step_id':'code',
        'artifact_ids':[imported.result['imported_artifact_id']]},call_id='complete')
    assert not done.result['ok'] and controller.state==previous and kernel.calls==1


@pytest.mark.asyncio
async def test_acknowledged_python_error_can_be_corrected_but_does_not_deliver_file(tmp_path,monkeypatch):
    runtime,handler,context,call,service,io,store,kernel,path=case(tmp_path,monkeypatch,mode='error')
    result=await execute(runtime,handler,context,call,service)
    assert result.status=='error' and runtime.task_execution_state.operations[-1].status=='completed'
    assert not runtime.task_execution_state.results and not path.exists()
    kernel.mode='ok'
    fixed=ToolCall('corrected-code',call.name,{'code':"print('corrected export')",'description':'修正变量'})
    result=await execute(runtime,handler,replace(context,call_id=fixed.call_id),fixed,service)
    assert result.status=='success' and kernel.calls==2 and len(runtime.task_execution_state.operations)==2


@pytest.mark.asyncio
async def test_managed_crash_never_auto_reruns_or_allows_different_code_to_bypass_unknown(tmp_path,monkeypatch):
    runtime,handler,context,call,service,io,store,kernel,path=case(tmp_path,monkeypatch,mode='crashed')
    await execute(runtime,handler,context,call,service)
    assert kernel.calls==1 and runtime.task_execution_state.operations[-1].status=='uncertain'
    fixed=ToolCall('changed-after-crash',call.name,{'code':"print('new code')"})
    with pytest.raises(TaskError,match='PREVIOUS_EXECUTION_UNCERTAIN'):
        await execute(runtime,handler,replace(context,call_id=fixed.call_id),fixed,service)
    assert kernel.calls==1


@pytest.mark.asyncio
async def test_narrowed_personal_context_cannot_read_or_import_previous_code_output(tmp_path,monkeypatch):
    runtime,handler,context,call,service,io,store,kernel,path=case(tmp_path,monkeypatch)
    await execute(runtime,handler,context,call,service)
    state=runtime.task_execution_state;commit=AsyncMock(return_value={'outcome':'saved'})
    controller=TaskController(state,owner_task_id=state.root_task_id,operation_store=store,
        result_context=replace(context,personal_context_allowed=False),commit=commit)
    for control in ('read_task_code_result','import_task_code_result'):
        result=await controller.control(control,{'expected_revision':state.revision,'operation_id':state.operations[-1].id},call_id='narrowed-context')
        assert not result.result['ok'] and result.result['code']=='TASK_SANDBOX_RESULT_SCOPE_DENIED'
        assert controller.state==state
    commit.assert_not_awaited();io.assert_awaited_once()


@pytest.mark.asyncio
async def test_worker_unavailable_before_submission_allows_fenced_retry_after_recovery(tmp_path,monkeypatch):
    runtime,handler,context,call,service,io,store,kernel,path=case(tmp_path,monkeypatch,mode='unavailable')
    await execute(runtime,handler,context,call,service)
    assert kernel.calls==0 and runtime.task_execution_state.operations[-1].status=='failed_before_dispatch'
    kernel.mode='ok'
    retry=ToolCall('retry-after-worker-ready',call.name,dict(call.arguments))
    result=await execute(runtime,handler,replace(context,call_id=retry.call_id),retry,service)
    assert result.status=='success' and kernel.calls==1
    assert len(runtime.task_execution_state.operations[-1].previous_attempts)==1


@pytest.mark.asyncio
async def test_same_size_substitution_before_first_import_is_not_the_original_code_artifact(tmp_path,monkeypatch):
    runtime,handler,context,call,service,io,store,kernel,path=case(tmp_path,monkeypatch)
    await execute(runtime,handler,context,call,service)
    size=path.stat().st_size;path.write_bytes(b'X'*size)
    state=runtime.task_execution_state;commit=AsyncMock(return_value={'outcome':'saved'})
    controller=TaskController(state,owner_task_id=state.root_task_id,operation_store=store,result_context=context,commit=commit)
    imported=await controller.control('import_task_code_result',{'expected_revision':state.revision,'operation_id':state.operations[-1].id},call_id='substituted-file')
    assert not imported.result['ok'] and controller.state==state
    commit.assert_not_awaited();io.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('valid',[True,False])
async def test_managed_kernel_acknowledgement_matches_actual_request_id(valid):
    from services.sandbox.kernel_manager import Kernel
    captured={}
    def write(value):captured.update(json.loads(value))
    async def response():return json.dumps({'id':captured['id'] if valid else 'other-request','status':'ok','stdout':'native output','emit_payloads':[]}).encode()
    process=SimpleNamespace(stdin=SimpleNamespace(write=write,drain=AsyncMock()),stdout=SimpleNamespace(readline=response))
    observer=Mock()
    manager=KernelManager()
    status,*_=await manager._send_and_recv(Kernel('local-conversation',process),'print(1)',1,response_observer=observer)
    assert status==('ok' if valid else 'error')
    if valid:assert observer.call_args.args[0]['code_sha256']==hashlib.sha256(b'print(1)').hexdigest()
    else:observer.assert_not_called()
