"""Real write/read ledgers and original ToolRuntime; no ERP HTTP is sent."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock
from uuid import uuid4
from dataclasses import replace

import psycopg
from psycopg.types.json import Jsonb
import pytest

pytestmark = pytest.mark.usefixtures('erp_write_adapter_enabled')

from services.task_execution.controller import TaskController
from services.task_execution.native_results import lifecycle_for
from services.task_execution.reducer import finish
from services.tools import ToolCall
from services.agent.agent_result import AgentResult
from services.agent.tool_executor import ToolExecutor
from services.kuaimai.dispatcher import ErpDispatcher
from tests.tool_runtime_support import IdentityDB
from tests.test_task_execution_reach_operations import execute
from tests.test_task_execution_native_writes_postgres import (write_db,native_result_db,operation_db,task_db,composition_db,
    ecom_db,isolated_db,scoped,save,setup)
ROOT=Path(__file__).parents[1]


@pytest.fixture(scope='module')
def postcondition_db(write_db):
    with psycopg.connect(write_db) as c:c.execute((ROOT/'migrations/360_agent_task_erp_postconditions.sql').read_text())
    yield write_db
    with psycopg.connect(write_db) as c:
        c.execute('DELETE FROM agent_task_native_writes')
        c.execute((ROOT/'migrations/rollback/360_agent_task_erp_postconditions_rollback.sql').read_text())


async def prepared(dsn,mode='ok',organization_client=False,parameters=None):
    f,runtime,handler,ctx,call,store,http,io,service,cp,bindings,rpc=await setup(dsn,
        mode=mode,organization_client=organization_client,write_action='product_add_update_simple',parameters=parameters if parameters is not None else {'outer_id':'P-001','title':'原始文具8入'})
    class Invocations:
        def lookup(self,**args):
            from psycopg.rows import dict_row
            with scoped(f) as c:
                with c.cursor(row_factory=dict_row) as cursor:
                    cursor.execute('SELECT * FROM tool_invocations WHERE task_id=%s AND turn_id=%s AND tool_call_id=%s',
                        (args['task_id'],args['turn_id'],args['tool_call_id']))
                    return cursor.fetchone()
        def begin(self,**args):return rpc('begin_tool_invocation',{'p_'+k:v for k,v in args.items()}).execute().data
        def complete(self,**args):return rpc('complete_tool_invocation',{'p_'+k:v for k,v in args.items()}).execute().data
        def mark_stale(self,**_):return {}
    handler._actor_invocation_store=Invocations()
    async def commit(candidate,expected):
        with scoped(f) as c:
            old=c.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s',(f['task'],)).fetchone()[0]
            return save(c,f,{**old,'_task_execution':candidate.model_dump(mode='json')},expected=expected)
    def controller(context=None):return TaskController(runtime.task_execution_state,owner_task_id=f['task'],
        result_context=context or ctx,operation_store=store,commit=commit)
    async def write():return await execute(runtime,handler,ctx,call,service)
    async def read(*,data=None,call_id='read-product',outer='P-001',source_token='local',force_network=False):
        client=SimpleNamespace(_app_key='local',_app_secret='local',_access_token=source_token,_base_url='https://gw.superboss.cc/router',_org_id=f['org'] if organization_client else None,request_with_retry=AsyncMock(return_value=data if data is not None else
            {'success':True,'outerId':outer,'title':'原始文具8入'}),close=AsyncMock())
        dispatcher=ErpDispatcher(client)
        executor=ToolExecutor(IdentityDB(f['user'],f['org']),f['user'],f['conv'],f['org'],task_id=f['task'],tool_entrypoint='model',agent_domain='erp')
        executor._task_execution_state=runtime.task_execution_state;executor._get_erp_dispatcher=AsyncMock(return_value=dispatcher)
        lifecycle=lifecycle_for(handler,executor.tool_runtime.context(call_id),executor.tool_runtime.registry,'erp_product_query')
        cache=Mock() if runtime.task_execution_state.operations and not force_network else None
        if cache is not None:cache.get.return_value=AgentResult('旧缓存结果不可作新回执')
        result=await executor.tool_runtime.execute('erp_product_query',{'action':'product_detail','params':{'outer_id':outer}},
            call_id=call_id,lifecycle=lifecycle,cache=cache)
        assert result.status=='success',repr(result.exception)
        if cache is not None:cache.get.assert_not_called()
        client.request_with_retry.assert_awaited_once()
        return result
    return f,runtime,handler,ctx,call,store,http,io,cp,controller,write,read


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['ok','format_loss'])
async def test_actual_write_followed_by_fresh_matching_read_completes_without_rewrite_or_context_loss(postcondition_db,mode):
    f,runtime,handler,ctx,call,store,http,io,cp,controller,write,read=await prepared(postcondition_db,mode=mode)
    result=await write();assert result.status==('success' if mode=='ok' else 'error'),repr(result.exception)
    op=runtime.task_execution_state.operations[-1];assert op.status==('accepted' if mode=='ok' else 'uncertain')
    with scoped(f) as c:original=c.execute("SELECT result FROM tool_invocations WHERE tool_call_id=%s AND task_id=%s",(call.call_id,f['task'])).fetchone()[0]
    ctrl=controller();waiting=await ctrl.control('read_task_write_result',{'expected_revision':ctrl.state.revision,'operation_id':op.id},call_id='inspect')
    assert waiting.result['ok'] and waiting.result['operation_status']=='accepted',waiting.result
    assert waiting.result['follow_up']['arguments']=={'action':'product_detail','params':{'outer_id':'P-001'}}
    runtime.task_execution_state=ctrl.state
    await read()
    ctrl=controller();found=await ctrl.control('read_task_write_result',{'expected_revision':ctrl.state.revision,'operation_id':op.id},call_id='verify')
    assert found.result['ok'] and found.result['operation_status']=='completed',found.result
    imported=await ctrl.control('import_task_receipt',{'expected_revision':ctrl.state.revision,'operation_id':op.id},call_id='import')
    assert imported.result['ok'],imported.result
    done=await ctrl.control('complete_task_step',{'expected_revision':ctrl.state.revision,'step_id':op.step_id,
        'artifact_ids':[imported.result['imported_artifact_id']]},call_id='done')
    assert done.result['ok'] and finish(ctrl.state).status=='completed',done.result
    with scoped(f) as c:
        assert c.execute('SELECT result FROM tool_invocations WHERE task_id=%s AND tool_call_id=%s',(f['task'],call.call_id)).fetchone()[0]==original
        saved=c.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s',(f['task'],)).fetchone()[0]
        assert saved['messages']==cp['messages'] and saved['opaque_context']==cp['opaque_context']
    io.assert_awaited_once();http.post.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['before_write','wrong_title','wrong_product','cached','revoked','late_read_changed','different_erp_session'])
async def test_stale_other_target_cached_or_revoked_read_does_not_prove_original_write(postcondition_db,mode):
    f,runtime,handler,ctx,call,store,http,io,cp,controller,write,read=await prepared(postcondition_db)
    if mode=='before_write':await read()
    await write();op=runtime.task_execution_state.operations[-1]
    if mode!='before_write':await read(data={'success':True,'outerId':'OTHER' if mode=='wrong_product' else 'P-001',
        'title':'wrong' if mode=='wrong_title' else '原始文具8入'},source_token='switched-session' if mode=='different_erp_session' else 'local')
    if mode=='cached':
        with scoped(f) as c:c.execute("UPDATE tool_invocations SET result=jsonb_set(result,'{tool_result,execution,cached}','true') WHERE task_id=%s AND tool_call_id='read-product'",(f['task'],))
    if mode=='late_read_changed':await read(data={'success':True,'outerId':'P-001','title':'a later title'},call_id='later-read')
    if mode=='revoked':
        with psycopg.connect(postcondition_db) as c:c.execute("UPDATE org_members SET status='inactive' WHERE user_id=%s AND org_id=%s",(f['user'],f['org']))
    ctrl=controller()
    if mode=='revoked':
        from services.task_execution.operation_store import TaskOperationPersistenceError
        with pytest.raises(TaskOperationPersistenceError):await ctrl.control('read_task_write_result',{'expected_revision':ctrl.state.revision,'operation_id':op.id},call_id='verify')
    else:
        result=await ctrl.control('read_task_write_result',{'expected_revision':ctrl.state.revision,'operation_id':op.id},call_id='verify')
        assert not result.result['ok'] or result.result.get('operation_status')!='completed',result.result
        if mode=='different_erp_session':
            assert result.result['follow_up']['state']=='requires_connection_verification'
            assert 'tool_name' not in result.result['follow_up']
    assert ctrl.state.operations[-1].status=='accepted'
    io.assert_awaited_once();http.post.assert_awaited_once()


@pytest.mark.asyncio
async def test_rollback_checks_all_tenants_for_original_postcondition_evidence(postcondition_db):
    f,runtime,handler,ctx,call,store,http,io,cp,controller,write,read=await prepared(postcondition_db)
    await write()
    with psycopg.connect(postcondition_db) as c:
        with pytest.raises(psycopg.Error,match='NOT_DRAINED'):
            with c.transaction():c.execute((ROOT/'migrations/rollback/360_agent_task_erp_postconditions_rollback.sql').read_text())
        assert c.execute("SELECT relforcerowsecurity FROM pg_class WHERE oid='agent_task_native_writes'::regclass").fetchone()[0]


@pytest.mark.asyncio
async def test_later_native_read_change_blocks_completion_of_an_imported_receipt(postcondition_db):
    f,runtime,handler,ctx,call,store,http,io,cp,controller,write,read=await prepared(postcondition_db)
    await write();await read();op=runtime.task_execution_state.operations[-1]
    ctrl=controller();found=await ctrl.control('read_task_write_result',{'expected_revision':ctrl.state.revision,'operation_id':op.id},call_id='verify')
    assert found.result['ok'] and found.result['operation_status']=='completed'
    imported=await ctrl.control('import_task_receipt',{'expected_revision':ctrl.state.revision,'operation_id':op.id},call_id='import')
    assert imported.result['ok']
    runtime.task_execution_state=ctrl.state
    await read(data={'success':True,'outerId':'P-001','title':'later edit'},call_id='later-native-read',force_network=True)
    completed=await ctrl.control('complete_task_step',{'expected_revision':ctrl.state.revision,'step_id':op.step_id,
        'artifact_ids':[imported.result['imported_artifact_id']]},call_id='complete')
    assert not completed.result['ok']
    io.assert_awaited_once();http.post.assert_awaited_once()


@pytest.mark.asyncio
async def test_unknown_submission_is_not_reclassified_as_accepted_by_a_matching_product_read(postcondition_db):
    f,runtime,handler,ctx,call,store,http,io,cp,controller,write,read=await prepared(postcondition_db,mode='timeout')
    await write();op=runtime.task_execution_state.operations[-1];assert op.status=='uncertain'
    await read()
    ctrl=controller();found=await ctrl.control('read_task_write_result',{'expected_revision':ctrl.state.revision,'operation_id':op.id},call_id='verify')
    assert found.result['ok'] and found.result['operation_status']=='uncertain',found.result
    assert not ctrl.state.artifacts
    io.assert_awaited_once();http.post.assert_awaited_once()
