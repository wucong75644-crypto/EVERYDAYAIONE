"""Actual read lifecycle, original ledger, typed import and owner-fenced SQL."""
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
import pytest

from services.task_execution.contracts import TaskState
from services.task_execution.artifact_reader import TaskArtifactReader
from services.task_execution.native_results import lifecycle_for
from services.task_execution.reducer import register_artifacts,complete_step
from services.task_execution.controller import ImportNativeResult
from services.tools import ToolCall
from services.tools.result_payload import encode_result
from tests.test_task_execution_operations_postgres import operation_db,task_db,composition_db,ecom_db,isolated_db,connect,seed
from tests.test_task_execution_postgres import task_payload,save
from tests.test_task_execution_native_results import case

ROOT=Path(__file__).parents[1]


@pytest.fixture(scope='module')
def native_result_db(operation_db):
    with psycopg.connect(operation_db) as c:
        c.execute('SET LOCAL ROLE everydayai_owner')
        c.execute((ROOT/'migrations/348_agent_task_native_results.sql').read_text())
    yield operation_db
    with psycopg.connect(operation_db) as c:
        c.execute('SET LOCAL ROLE everydayai_owner')
        c.execute((ROOT/'migrations/rollback/348_agent_task_native_results_rollback.sql').read_text())


def real_case(dsn,*,artifact_kind='data',capability='platform.web_search'):
    from dataclasses import replace
    from psycopg import sql
    f=seed(dsn)
    value=task_payload(f)['_task_execution']
    value['image_binding_version']=3
    value['requirements'][0]['artifact_kind']=artifact_kind
    value['steps']=[{'id':'query','title':'查询数据','capability':capability,'requirement_ids':['report']}]
    state=TaskState.model_validate(value)
    _,context,runtime,handler,service,call,_,io=case()
    context=replace(context,actor_user_id=f['user'],workspace_owner_id=f['user'],task_id=f['task'],conversation_id=f['conv'])
    runtime.task_id=f['task'];runtime.task_execution_state=state
    handler._actor_turn_id=f['turn'];handler._actor_execution_token=f['token']
    class Store:
        def mark_stale(self,**_):return {}
        def lookup(self,**args):
            with connect(f) as c:
                from psycopg.rows import dict_row
                with c.cursor(row_factory=dict_row) as cursor:
                    cursor.execute('SELECT * FROM tool_invocations WHERE task_id=%s AND turn_id=%s AND tool_call_id=%s',
                        (args['task_id'],args['turn_id'],args['tool_call_id']))
                    return cursor.fetchone()
        def begin(self,**args):
            with connect(f) as c:return c.execute('SELECT begin_tool_invocation(%s,%s,%s,%s,%s,%s,%s)',
                tuple(args[k] for k in ('task_id','conversation_id','turn_id','execution_token','tool_call_id','tool_name','args_hash'))).fetchone()[0]
        def complete(self,**args):
            with connect(f) as c:return c.execute('SELECT complete_tool_invocation(%s,%s,%s,%s,%s,%s,%s)',
                tuple(Jsonb(args[k]) if k=='result' else args[k] for k in
                    ('task_id','turn_id','tool_call_id','execution_token','status','result','error_message'))).fetchone()[0]
    class DB:
        def rpc(self,name,params):
            async def execute():
                statement=sql.SQL('SELECT {}({})').format(sql.Identifier(name),sql.SQL(',').join(
                    sql.SQL('{}=>%s').format(sql.Identifier(key)) for key in params))
                with connect(f) as c:result=c.execute(statement,tuple(params.values())).fetchone()[0]
                return SimpleNamespace(data=result)
            return SimpleNamespace(execute=execute)
    handler._actor_invocation_store=Store()
    with connect(f) as c:save(c,f,{'messages':[],'_task_execution':state.model_dump(mode='json')})
    return f,state,context,runtime,handler,service,call,io,DB()


@pytest.mark.asyncio
@pytest.mark.parametrize('empty,kind',[(False,'data'),(True,'data'),(False,'file')])
async def test_actual_safe_read_journal_and_argument_witness_survive_context_compaction(native_result_db,monkeypatch,tmp_path,empty,kind):
    from services.task_execution.native_results import import_result,read_result
    f,state,context,runtime,handler,service,call,io,db=real_case(native_result_db,artifact_kind=kind)
    if empty:
        from services.agent.agent_result import AgentResult
        io.return_value=AgentResult('未找到匹配数据',status='empty',data=[])
    if kind=='file':
        from dataclasses import replace
        from core.config import get_settings
        from services.file_executor import FileExecutor
        from services.agent.agent_result import AgentResult
        from services.agent.tool_output import FileRef,OutputFormat
        from services.tools.resource_access import ResourceAccessBoundary,ResourceRule
        monkeypatch.setattr(get_settings(),'file_workspace_root',str(tmp_path))
        files=FileExecutor(str(tmp_path),context.workspace_owner_id,context.org_id)
        path=Path(files.workspace_root)/'staging/report.csv';path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text('capacity\n8\n')
        ref=FileRef(str(path),'report.csv','csv',1,path.stat().st_size,[])
        io.return_value=AgentResult('真实文件导出',format=OutputFormat.FILE_REF,file_ref=ref)
        context=replace(context,resource_access=ResourceAccessBoundary((ResourceRule(('read',),directories=('staging',)),),'test-export',True).as_dict())
    lifecycle=lifecycle_for(handler,context,service.registry,call.name)
    decision=service.policy.decide(call.name,context,call.arguments)
    assert await lifecycle.replay(call,context,decision) is None
    result=await service.execute(call,context,before_dispatch=lifecycle.begin,on_result=lifecycle.complete)
    assert result.status==('empty' if empty else 'success')
    # The original call text can disappear from model messages; its exact
    # normalized input remains in the same owner checkpoint, not a new ledger column.
    with connect(f) as c:save(c,f,{'messages':[{'role':'system','content':'已有历史摘要'}],
        '_task_execution':state.model_dump(mode='json'),'_task_read_arguments':runtime.task_read_arguments},expected=state.revision)
    reader=TaskArtifactReader(db,task_id=f['task'],execution_token=f['token'])
    listing=await reader.native_result(state)
    assert listing['results'][0]['tool_call_id']==call.call_id
    record=(await reader.native_result(state,tool_call_id=call.call_id))['record']
    assert record['arguments']==dict(call.arguments)
    artifact,body=import_result(state,ImportNativeResult(expected_revision=state.revision,step_id='query',tool_call_id=call.call_id,kind=kind),
        record,owner_task_id=f['task'],context=context,registry=service.registry)
    next_state=complete_step(register_artifacts(state,[artifact]),'query',[artifact.id],owner_task_id=f['task'])
    with connect(f) as c:save(c,f,{'messages':[{'role':'system','content':'已有历史摘要'}],
        '_task_execution':next_state.model_dump(mode='json'),'_task_read_arguments':runtime.task_read_arguments},expected=state.revision)
    recovered=(await reader.native_result(next_state,artifact_id=artifact.id))['record']
    recovered_body=read_result(next_state,artifact,recovered,context=context)
    if kind=='file':assert recovered_body['content_sha256']==body['content_sha256']
    else:assert recovered_body['data']==body['data']
    assert recovered_body['result_status']==result.status
    if empty:assert body['data']==[] and body['result_status']=='empty'
    io.assert_awaited_once()
    with connect(f) as c:
        assert c.execute('SELECT count(*) FROM tool_invocations WHERE task_id=%s',(f['task'],)).fetchone()[0]==1


@pytest.mark.asyncio
@pytest.mark.parametrize('damage',['token','actor','lease','stale'])
async def test_result_read_never_bypasses_current_owner_scope_or_revision(native_result_db,damage):
    from services.task_execution.contracts import TaskError
    from services.conversation_state import ConversationStopRequested
    f,state,context,runtime,handler,service,call,io,db=real_case(native_result_db)
    if damage=='token':f['token']=str(uuid4())
    if damage=='lease':
        with psycopg.connect(f['dsn']) as c:c.execute("UPDATE tasks SET lease_expires_at=now()-interval '1 second' WHERE id=%s",(f['task'],))
    if damage=='actor':f['user']=str(uuid4())
    if damage=='stale':state=state.model_copy(update={'revision':state.revision+1})
    with pytest.raises((TaskError,ConversationStopRequested)):
        await TaskArtifactReader(db,task_id=f['task'],execution_token=f['token']).native_result(state)
