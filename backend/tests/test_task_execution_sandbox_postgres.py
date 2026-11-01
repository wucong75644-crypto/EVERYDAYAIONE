"""Original fenced SQL + sandbox host outputs; worker and OSS are local doubles."""
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock

import psycopg
import pytest

from core.config import get_settings
from services.file_executor import FileExecutor
from services.sandbox.executor import SandboxExecutor
from services.task_execution.contracts import TaskState,TaskError
from services.task_execution.controller import TaskController
from services.task_execution.operation_store import TaskOperationStore
from services.task_execution.reducer import finish
from services.tools import ToolCall,ToolDispatcher,ToolExecutionService
from services.tools.resource_access import ResourceAccessBoundary,ResourceRule
from tests.test_task_execution_sandbox import Kernel
from tests.test_task_execution_reach_operations import execute
from tests.test_task_execution_native_results_postgres import native_result_db,real_case
from tests.test_task_execution_operations_postgres import operation_db,task_db,composition_db,ecom_db,isolated_db,connect
from tests.test_task_execution_postgres import save

ROOT=Path(__file__).parents[1]


@pytest.fixture(scope='module')
def code_db(native_result_db):
    with psycopg.connect(native_result_db) as c:c.execute((ROOT/'migrations/351_agent_task_effect_results.sql').read_text())
    yield native_result_db
    with psycopg.connect(native_result_db) as c:c.execute((ROOT/'migrations/rollback/351_agent_task_effect_results_rollback.sql').read_text())


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['ok','error','crashed','ast','missing_ack'])
async def test_original_ledger_output_import_and_resume_do_not_rerun_sandbox(code_db,monkeypatch,tmp_path,mode):
    monkeypatch.setattr(get_settings(),'file_workspace_root',str(tmp_path))
    f,state,context,runtime,handler,_,_,_,db=real_case(code_db,artifact_kind='file',capability='sandbox.code.execute')
    runtime.replay_save_sequence=0
    access=ResourceAccessBoundary((ResourceRule(('read','list'),directories=('.',)),),'native-test',True)
    context=replace(context,feature_flags={'sandbox_enabled':True},resource_access=access.as_dict())
    files=FileExecutor(str(tmp_path),context.workspace_owner_id,context.org_id)
    path=Path(files.workspace_root)/'下载/report.zip'
    kernel=Kernel(path,mode=mode if mode in {'ok','error','crashed'} else 'ok')
    sandbox=SandboxExecutor(workspace_dir=files.workspace_root,output_dir=str(path.parent),conversation_id=context.conversation_id,
        kernel_manager=kernel,managed_task=True)
    sandbox._upload_payload_files=AsyncMock()
    async def run(args):
        result=await sandbox.execute(args['code'],args.get('description',''))
        if mode=='missing_ack':result.metadata.clear()
        return result
    from services.tools.catalog import build_capability_catalog
    registry=build_capability_catalog();io=AsyncMock(side_effect=run)
    service=ToolExecutionService(registry,ToolDispatcher({('legacy','code_execute'):io}))
    store=TaskOperationStore(db,task_id=f['task'],execution_token=f['token'],turn_id=f['turn'],registry=registry)
    runtime.task_operation_store=store
    code='import subprocess' if mode=='ast' else "print('native report')"
    call=ToolCall('original-code','code_execute',{'code':code,'description':'导出报告'})
    with connect(f) as c:save(c,f,{'messages':[{'role':'system','content':'原有历史摘要'}],'opaque_context':{'keep':True},
        '_task_execution':state.model_dump(mode='json')},expected=state.revision)
    result=await execute(runtime,handler,replace(context,call_id=call.call_id),call,service)
    op=runtime.task_execution_state.operations[-1]
    assert op.status==('failed_before_dispatch' if mode=='ast' else 'uncertain' if mode in {'crashed','missing_ack'} else 'completed')
    with connect(f) as c:
        cp=c.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s',(f['task'],)).fetchone()[0]
        assert cp['opaque_context']=={'keep':True} and cp['messages'][0]['content']=='原有历史摘要'
        runtime.task_execution_state=TaskState.model_validate(cp['_task_execution'])
        assert c.execute('SELECT count(*) FROM tool_invocations WHERE task_id=%s',(f['task'],)).fetchone()[0]==1
    state=runtime.task_execution_state
    if mode in {'crashed','missing_ack'}:
        changed=ToolCall('new-code',call.name,{'code':"print('new action')"})
        with pytest.raises(TaskError,match='PREVIOUS_EXECUTION_UNCERTAIN'):
            await execute(runtime,handler,replace(context,call_id=changed.call_id),changed,service)
        assert kernel.calls==1;return
    if mode in {'error','ast'}:
        assert not state.results
        kernel.mode='ok'
        call=ToolCall('fixed-code',call.name,{'code':"print('fixed report')",'description':'修正代码'})
        result=await execute(runtime,handler,replace(context,call_id=call.call_id),call,service)
        assert result.status=='success' and runtime.task_execution_state.operations[-1].status=='completed'
        state=runtime.task_execution_state;op=state.operations[-1]
    again=ToolCall('replay-code',call.name,dict(call.arguments))
    count=kernel.calls
    replay=await execute(runtime,handler,replace(context,call_id=again.call_id),again,service)
    assert replay.execution.replayed and kernel.calls==count
    async def commit(candidate,expected):
        with connect(f) as c:return save(c,f,{**cp,'_task_execution':candidate.model_dump(mode='json')},expected=expected)
    controller=TaskController(state,owner_task_id=f['task'],commit=commit,operation_store=store,result_context=context)
    artifact=await controller.control('import_task_code_result',{'expected_revision':state.revision,'operation_id':op.id},call_id='import')
    assert artifact.result['ok'],artifact.result
    done=await controller.control('complete_task_step',{'expected_revision':controller.state.revision,'step_id':'query',
        'artifact_ids':[artifact.result['imported_artifact_id']]},call_id='complete')
    assert done.result['ok'] and finish(controller.state).status=='completed'
