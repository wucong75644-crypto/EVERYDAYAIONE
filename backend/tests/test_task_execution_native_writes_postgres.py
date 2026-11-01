"""Real fenced journal, actual ERP dispatcher and SDK with HTTP mocked locally."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
import pytest

pytestmark = pytest.mark.usefixtures('erp_write_adapter_enabled')

from services.kuaimai.client import KuaiMaiClient
from services.kuaimai.dispatcher import ErpDispatcher
from services.task_execution.contracts import TaskState
from services.task_execution.controller import TaskController
from services.task_execution.native_writes import prepare_erp
from services.task_execution.operation_store import TaskOperationStore
from services.task_execution.reducer import finish
from services.tools import ToolCall,ToolDispatcher,ToolExecutionService
from services.tools.result import UncertainToolInvocationError
from tests.test_task_execution_native_writes import case
from tests.test_task_execution_reach_operations import execute
from tests.test_task_execution_native_results_postgres import native_result_db
from tests.test_task_execution_operations_postgres import operation_db,task_db,composition_db,ecom_db,isolated_db,connect,seed
from tests.test_task_execution_postgres import task_payload,save

ROOT=Path(__file__).parents[1]


@pytest.fixture(scope='module')
def write_db(native_result_db):
    with psycopg.connect(native_result_db) as c:
        c.execute((ROOT/'migrations/351_agent_task_effect_results.sql').read_text())
        c.execute((ROOT/'migrations/354_agent_task_native_writes.sql').read_text())
    yield native_result_db
    with psycopg.connect(native_result_db) as c:
        c.execute('DELETE FROM agent_task_native_writes')
        c.execute((ROOT/'migrations/rollback/354_agent_task_native_writes_rollback.sql').read_text())
        c.execute((ROOT/'migrations/rollback/351_agent_task_effect_results_rollback.sql').read_text())


def scoped(f):
    c=psycopg.connect(psycopg.conninfo.make_conninfo(f['dsn'],user='everydayai'))
    c.execute("SELECT set_config('app.actor_user_id',%s,true),set_config('app.org_id',%s,true),set_config('app.access_kind','runtime',true)",(f['user'],f['org']))
    return c


async def setup(dsn,*,mode='ok',write_action='tag_add',parameters=None,organization_client=False):
    f=seed(dsn);f['org']=str(uuid4())
    runtime,handler,ctx,call,_,_,_,_=case(mode='missing' if mode=='missing' else 'ok')
    if write_action!='tag_add':call=ToolCall(call.call_id,'erp_execute',{'category':'product','action':write_action,'params':parameters})
    value=task_payload(f)['_task_execution'];value.update(org_id=f['org'],image_binding_version=3,
        steps=[s.model_dump(mode='json') for s in runtime.task_execution_state.steps])
    value['requirements'][0]['artifact_kind']='receipt';state=TaskState.model_validate(value)
    runtime.task_execution_state=state;runtime.task_id=f['task'];runtime.replay_save_sequence=0
    handler._actor_turn_id=f['turn'];handler._actor_execution_token=f['token']
    ctx=replace(ctx,actor_user_id=f['user'],workspace_owner_id=f['user'],org_id=f['org'],conversation_id=f['conv'],task_id=f['task'])
    with psycopg.connect(dsn) as c:
        c.execute('INSERT INTO organizations(id) VALUES(%s)',(f['org'],))
        c.execute('INSERT INTO org_members(org_id,user_id) VALUES(%s,%s)',(f['org'],f['user']))
        c.execute('UPDATE tasks SET org_id=%s WHERE id=%s',(f['org'],f['task']))
    cp={'messages':[{'role':'system','content':'原历史摘要'},{'role':'user','content':'原输入'}],
        'opaque_context':{'keep':True},'_task_execution':state.model_dump(mode='json')}
    with scoped(f) as c:save(c,f,cp)
    def rpc(name,params):
        def run():
            query=sql.SQL('SELECT {}({})').format(sql.Identifier(name),sql.SQL(',').join(sql.SQL('{}=>%s').format(sql.Identifier(k)) for k in params))
            with scoped(f) as c:value=c.execute(query,tuple(Jsonb(v) if isinstance(v,dict) else v for v in params.values())).fetchone()[0]
            return SimpleNamespace(data=value)
        return SimpleNamespace(execute=run)
    async def async_execute(name,params):return rpc(name,params).execute()
    db=SimpleNamespace(rpc=lambda name,params:SimpleNamespace(execute=lambda:async_execute(name,params)))
    from services.tools.catalog import build_capability_catalog
    registry=build_capability_catalog();store=TaskOperationStore(db,task_id=f['task'],execution_token=f['token'],turn_id=f['turn'],registry=registry)
    runtime.task_operation_store=store
    client=KuaiMaiClient(app_key='local',app_secret='local',access_token='local',
        org_id=f['org'] if organization_client else None,token_persister=AsyncMock() if organization_client else None)
    response=SimpleNamespace(raise_for_status=lambda:None,json=lambda:{'success':True,'data':{'id':'native-tag-1'}})
    http=SimpleNamespace(post=AsyncMock(return_value=response),is_closed=False);client._client=http
    dispatcher=ErpDispatcher(client);bindings=[]
    async def native(args):
        executor=SimpleNamespace(_task_execution_state=runtime.task_execution_state,_task_id=f['task'],
            image_execution_token=f['token'],db=SimpleNamespace(rpc=rpc))
        session=await prepare_erp(executor,args);bindings.append(session.bound)
        dispatcher._native_write_observer=session.observe
        if mode=='timeout':http.post.side_effect=httpx.ReadTimeout('lost acknowledgement')
        if mode=='format_loss':dispatcher._format_response=lambda *_:(_ for _ in ()).throw(RuntimeError('format lost'))
        if mode in {'expired','cancelled'}:
            with psycopg.connect(dsn) as c:
                c.execute('UPDATE tasks SET '+("lease_expires_at=now()-interval '1 second'" if mode=='expired' else "status='cancelled'")+' WHERE id=%s',(f['task'],))
        return await dispatcher.execute('erp_product_query',write_action,args['params'])
    io=AsyncMock(side_effect=native);service=ToolExecutionService(registry,ToolDispatcher({('legacy','erp_execute'):io}))
    return f,runtime,handler,ctx,call,store,http,io,service,cp,bindings,rpc


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['ok','format_loss','timeout','missing'])
@pytest.mark.parametrize('close_new_domain',[False,True])
async def test_original_native_ledger_and_context_survive_write_recovery(write_db,mode,close_new_domain,monkeypatch):
    f,runtime,handler,ctx,call,store,http,io,service,cp,bindings,rpc=await setup(write_db,mode=mode)
    result=await execute(runtime,handler,ctx,call,service)
    if close_new_domain:
        from core.config import get_settings
        monkeypatch.setattr(get_settings(),'agent_task_erp_write_enabled',False)
    state=runtime.task_execution_state;op=state.operations[-1]
    expected={'ok':'completed','format_loss':'uncertain','timeout':'uncertain','missing':'failed_before_dispatch'}[mode]
    assert op.status==expected,repr(result.exception)
    proof=await store.native_write_receipt(state,op.id)
    assert 'dispatch_token' not in proof and 'execution_token' not in proof
    assert proof['phase']=={'ok':'returned','format_loss':'returned','timeout':'dispatched','missing':'prepared'}[mode]
    with scoped(f) as c:
        old=c.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s',(f['task'],)).fetchone()[0]
        assert old['messages']==cp['messages'] and old['opaque_context']==cp['opaque_context']
        original=c.execute('SELECT result FROM tool_invocations WHERE task_id=%s',(f['task'],)).fetchone()[0]
    async def commit(candidate,revision):
        with scoped(f) as c:
            saved=c.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s',(f['task'],)).fetchone()[0]
            saved['_task_execution']=candidate.model_dump(mode='json')
            return save(c,f,saved,expected=revision)
    ctrl=TaskController(state,owner_task_id=f['task'],result_context=ctx,operation_store=store,commit=commit)
    read=await ctrl.control('read_task_write_result',{'expected_revision':state.revision,'operation_id':op.id},call_id='read')
    assert read.result['ok'],read.result
    imported=await ctrl.control('import_task_receipt',{'expected_revision':ctrl.state.revision,'operation_id':op.id},call_id='import')
    if mode in {'ok','format_loss'}:
        assert imported.result['ok'],imported.result
        done=await ctrl.control('complete_task_step',{'expected_revision':ctrl.state.revision,'step_id':'write',
            'artifact_ids':[imported.result['imported_artifact_id']]},call_id='done')
        assert done.result['ok'] and finish(ctrl.state).status=='completed',done.result
        http.post.assert_awaited_once()
    else:
        assert not imported.result['ok']
        if mode=='missing':http.post.assert_not_awaited()
        else:
            with pytest.raises(UncertainToolInvocationError):await execute(runtime,handler,ctx,ToolCall('fresh',call.name,dict(call.arguments)),service)
            http.post.assert_awaited_once()
    with scoped(f) as c:
        assert c.execute('SELECT result FROM tool_invocations WHERE task_id=%s',(f['task'],)).fetchone()[0]==original
        saved=c.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s',(f['task'],)).fetchone()[0]
        assert saved['messages']==cp['messages'] and saved['opaque_context']==cp['opaque_context']
    io.assert_awaited_once()


@pytest.mark.asyncio
async def test_dispatch_nonce_is_one_shot_and_wrong_or_stale_nonce_cannot_authorize_io(write_db):
    f,runtime,handler,ctx,call,store,http,io,service,cp,bindings,rpc=await setup(write_db)
    await execute(runtime,handler,ctx,call,service);op=runtime.task_execution_state.operations[-1]
    for nonce in [str(uuid4()),bindings[0]['dispatch_token']]:
        with pytest.raises(psycopg.Error):rpc('record_agent_task_native_write',{'p_operation_id':op.id,'p_dispatch_token':nonce,
            'p_phase':'dispatched','p_value':{'method':'erp.item.tag.add'}}).execute()
    http.post.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['expired','cancelled'])
async def test_dispatch_rechecks_live_owner_before_http_after_validation(write_db,mode):
    from services.conversation_state import ConversationStopRequested
    f,runtime,handler,ctx,call,store,http,io,service,cp,bindings,rpc=await setup(write_db,mode=mode)
    with pytest.raises(ConversationStopRequested):await execute(runtime,handler,ctx,call,service)
    http.post.assert_not_awaited()
    with scoped(f) as c:assert c.execute('SELECT phase FROM agent_task_native_writes WHERE owner_task_id=%s',(f['task'],)).fetchone()[0]=='prepared'


@pytest.mark.asyncio
async def test_rollback_refuses_foreign_tenant_undrained_proof_and_restores_force_rls(write_db):
    f,runtime,handler,ctx,call,store,http,io,service,cp,bindings,rpc=await setup(write_db)
    await execute(runtime,handler,ctx,call,service)
    with scoped(f) as c:
        c.execute("SELECT set_config('app.actor_user_id',%s,true)",(str(uuid4()),))
        assert c.execute('SELECT count(*) FROM agent_task_native_writes').fetchone()[0]==0
        with pytest.raises(psycopg.Error,match='TASK_NATIVE_WRITES_NOT_DRAINED'):
            with c.transaction():c.execute((ROOT/'migrations/rollback/354_agent_task_native_writes_rollback.sql').read_text())
        assert c.execute("SELECT relforcerowsecurity FROM pg_class WHERE oid='agent_task_native_writes'::regclass").fetchone()[0]


@pytest.mark.asyncio
async def test_known_not_started_can_correct_parameters_once_under_original_approval(write_db):
    f,runtime,handler,ctx,call,store,http,io,service,cp,bindings,rpc=await setup(write_db,mode='missing')
    await execute(runtime,handler,ctx,call,service)
    original=runtime.task_execution_state.operations[-1]
    assert original.status=='failed_before_dispatch'
    corrected=ToolCall('corrected-native-call',call.name,{**dict(call.arguments),'params':{'name':'补齐标签'}})
    fixed=await execute(runtime,handler,replace(ctx,call_id=corrected.call_id),corrected,service,approved=False)
    assert fixed.status=='confirmation_required' and len(bindings)==1
    http.post.assert_not_awaited()
    result=await execute(runtime,handler,replace(ctx,call_id=corrected.call_id),corrected,service)
    assert result.status=='success',repr(result.exception)
    operation=runtime.task_execution_state.operations[-1]
    assert operation.id==original.id and operation.status=='completed' and len(operation.previous_attempts)==1
    assert bindings[0]['dispatch_token']!=bindings[1]['dispatch_token']
    with pytest.raises(psycopg.Error):rpc('record_agent_task_native_write',{'p_operation_id':operation.id,
        'p_dispatch_token':bindings[0]['dispatch_token'],'p_phase':'returned','p_value':{'method':'erp.item.tag.add','body':{'success':True}}}).execute()
    with scoped(f) as c:
        rows=c.execute('SELECT tool_call_id,status FROM tool_invocations WHERE task_id=%s ORDER BY started_at',(f['task'],)).fetchall()
        assert rows==[(call.call_id,'succeeded'),(corrected.call_id,'succeeded')]
    http.post.assert_awaited_once()
