"""Actual domain record -> existing queue -> frozen Actor history. No provider."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID,uuid4

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
import pytest

from services.agent.agent_result import AgentResult
from services.scheduler.chat_task_manager import FormBlockResult
from services.task_execution.contracts import TaskState
from services.task_execution.controller import TaskController
from services.task_execution.operation_store import TaskOperationStore
from services.task_execution.receipt_continuation import continuation_payload
from services.task_execution.reducer import finish
from services.tools import ToolDispatcher,ToolExecutionService
from services.tools.catalog import build_capability_catalog
from tests.test_task_execution_scheduled import case,execute
from tests.test_task_execution_scheduled_postgres import scheduled_db,scoped
from tests.test_task_execution_continuation_postgres import continuation_db
from tests.test_task_execution_children_postgres import children_db
from tests.test_task_execution_controls_postgres import controls_db
from tests.test_task_execution_native_results_postgres import native_result_db
from tests.test_task_execution_operations_postgres import operation_db,task_db,composition_db,ecom_db,isolated_db,seed
from tests.test_task_execution_postgres import task_payload,save

ROOT=Path(__file__).parents[1]


@pytest.fixture(scope='module')
def future_db(scheduled_db,controls_db):
    assert scheduled_db==controls_db
    with psycopg.connect(scheduled_db) as c:c.execute((ROOT/'migrations/353_agent_task_receipt_continuations.sql').read_text())
    yield scheduled_db
    with psycopg.connect(scheduled_db) as c:
        c.execute("DELETE FROM agent_task_events WHERE payload->>'source_kind'='scheduled'")
        c.execute((ROOT/'migrations/rollback/353_agent_task_receipt_continuations_rollback.sql').read_text())


def database(f):
    class DB:
        def rpc(self,name,params):
            async def run():
                query=sql.SQL('SELECT {}({})').format(sql.Identifier(name),sql.SQL(',').join(sql.SQL('{}=>%s').format(sql.Identifier(k)) for k in params))
                with scoped(f) as c:value=c.execute(query,tuple(params.values())).fetchone()[0]
                return SimpleNamespace(data=value)
            return SimpleNamespace(execute=run)
    return DB()


def claim_internal(f,task_id):
    from services.conversation_execution import GenerationClaim
    with scoped(f) as c:
        claimed=c.execute('SELECT claim_next_serial_generation_turn(%s,%s,%s)',(f['conv'],300,3)).fetchone()[0]
        row=c.execute('SELECT to_jsonb(tasks) FROM tasks WHERE id=%s',(task_id,)).fetchone()[0]
    claim=GenerationClaim.from_rpc(claimed,f['conv'],'serial')
    assert claim is not None and claim.task_id==task_id
    return row,claim


async def source_case(dsn,mode):
    from dataclasses import replace
    f=seed(dsn);f['org']=str(uuid4());assistant=str(uuid4())
    runtime,handler,context,call,_,_,_=case()
    raw=task_payload(f)['_task_execution'];raw.update(org_id=f['org'],image_binding_version=3,
        steps=[s.model_dump(mode='json') for s in runtime.task_execution_state.steps])
    raw['requirements'][0]['artifact_kind']='receipt';state=TaskState.model_validate(raw)
    runtime.task_execution_state=state;runtime.task_id=f['task'];runtime.replay_save_sequence=0
    handler._actor_turn_id=f['turn'];handler._actor_execution_token=f['token']
    context=replace(context,actor_user_id=f['user'],workspace_owner_id=f['user'],org_id=f['org'],conversation_id=f['conv'],task_id=f['task'])
    with psycopg.connect(dsn) as c:
        c.execute('INSERT INTO organizations(id) VALUES(%s)',(f['org'],))
        c.execute('INSERT INTO org_members(org_id,user_id) VALUES(%s,%s)',(f['org'],f['user']))
        c.execute('UPDATE conversations SET org_id=%s WHERE id=%s',(f['org'],f['conv']))
        c.execute('UPDATE messages SET org_id=%s WHERE id=%s',(f['org'],f['message']))
        c.execute("INSERT INTO messages(id,conversation_id,org_id,role,turn_id,reply_to_message_id,status,context_revision,content) VALUES(%s,%s,%s,'assistant',%s,%s,'completed',1,'[]')",(assistant,f['conv'],f['org'],f['turn'],f['message']))
        c.execute('UPDATE tasks SET org_id=%s,assistant_message_id=%s WHERE id=%s',(f['org'],assistant,f['task']))
    history=[{'role':'system','content':'原摘要与长期记忆，不得丢失'},{'role':'user','content':state.original_goal},
        {'role':'user','content':state.original_goal}]
    cp={'messages':history,'opaque_context':{'must_keep':[1,2]},'_task_execution':state.model_dump(mode='json'),
        'content_blocks':[],'next_model_round':4}
    with scoped(f) as c:save(c,f,cp)
    registry=build_capability_catalog();store=TaskOperationStore(database(f),task_id=f['task'],execution_token=f['token'],turn_id=f['turn'],registry=registry)
    runtime.task_operation_store=store;change_id=str(uuid4());business_task=str(uuid4());receipt_id=str(uuid4())
    async def native(args):
        op=runtime.task_execution_state.operations[-1]
        with scoped(f) as c:
            key=c.execute('SELECT agent_task_scheduled_request_key(%s,%s,%s,%s,%s)',(f['task'],f['token'],call.call_id,args['action'],op.arguments_sha256)).fetchone()[0]
            if mode=='form_cancelled':return FormBlockResult({'form_type':'scheduled_task_create','form_id':'task_effect_'+op.id,'fields':[]},'请确认')
            c.execute("INSERT INTO change_sets(id,org_id,resource_type,resource_id,operation,base_revision,status,idempotency_key,created_by) VALUES(%s,%s,'scheduled_task',%s,'create','0','awaiting_approval',%s,%s)",
                (change_id,f['org'],business_task,key,f['user']))
        if mode=='lost_return':raise RuntimeError('lost native acknowledgement')
        return AgentResult('原变更卡片已受理',metadata={'change_set':{'id':change_id}})
    io=AsyncMock(side_effect=native);service=ToolExecutionService(registry,ToolDispatcher({('legacy','manage_scheduled_task'):io}))
    await execute(runtime,handler,context,call,service)
    current=runtime.task_execution_state;op=current.operations[-1]
    waiting=current.model_copy(update={'status':'waiting_approval','revision':current.revision+1})
    messages=[*history,{'role':'assistant','tool_calls':[{'id':call.call_id,'type':'function','function':{'name':call.name,'arguments':json.dumps({'action':'create'})}}]},
        {'role':'tool','tool_call_id':call.call_id,'content':'原生表单或变更卡片受理返回，尚未创建完成'}]
    with scoped(f) as c:
        save(c,f,{**cp,'messages':messages,'_task_execution':waiting.model_dump(mode='json')},expected=current.revision)
        assert c.execute("SELECT request_params#>>'{_agent_task_scheduled_v1,pending}' FROM tasks WHERE id=%s",(f['task'],)).fetchone()[0]=='true'
    with psycopg.connect(dsn) as c:
        c.execute("UPDATE messages SET turn_id=%s,status='completed',context_revision=1 WHERE id=%s",(f['turn'],f['message']))
        c.execute("UPDATE tasks SET status='completed' WHERE id=%s",(f['task'],))
        c.execute("UPDATE conversation_turn_checkpoints SET status='consumed' WHERE task_id=%s",(f['task'],))
        c.execute('UPDATE conversations SET context_revision=1 WHERE id=%s',(f['conv'],))
        if mode=='form_cancelled':
            c.execute('UPDATE messages SET content=%s WHERE id=%s',(Jsonb([{'type':'form','form_id':'task_effect_'+op.id,'form_type':'scheduled_task_create','status':'cancelled','fields':[]}]),assistant))
        elif mode=='failed':c.execute("UPDATE change_sets SET status='failed' WHERE id=%s",(change_id,))
        elif mode!='pending':
            c.execute("INSERT INTO scheduled_task_change_receipts(id,org_id,change_set_id,idempotency_key,task_id,operation,base_revision,new_revision,result) VALUES(%s,%s,%s,%s,%s,'create',0,1,%s)",
                (receipt_id,f['org'],change_id,'commit:'+change_id,business_task,Jsonb({'outcome':'created','task_id':business_task,'new_revision':1})))
    return f,waiting,context,io,messages


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['positive','lost_return','form_cancelled','failed','pending','paused','cancelled','new_input','revoked'])
async def test_real_background_receipt_enters_original_queue_preserves_history_and_does_not_reexecute(future_db,mode):
    f,state,context,io,history=await source_case(future_db,mode)
    if mode in {'paused','cancelled'}:
        with psycopg.connect(future_db) as c:
            candidate=state.model_copy(update={'status':mode,'revision':state.revision+1}).model_dump(mode='json')
            c.execute('UPDATE agent_task_states SET revision=%s,state=%s WHERE root_task_id=%s',(candidate['revision'],Jsonb(candidate),f['task']))
            c.execute("UPDATE conversation_turn_checkpoints SET state=jsonb_set(state,'{_task_execution}',%s) WHERE task_id=%s",(Jsonb(candidate),f['task']))
    if mode=='new_input':
        with psycopg.connect(future_db) as c:c.execute("INSERT INTO messages(id,conversation_id,org_id,role,content) VALUES(%s,%s,%s,'user','新的任务')",(str(uuid4()),f['conv'],f['org']))
    if mode=='revoked':
        with psycopg.connect(future_db) as c:c.execute("UPDATE org_members SET status='inactive' WHERE org_id=%s AND user_id=%s",(f['org'],f['user']))
    if mode=='revoked':
        with scoped(f) as c:
            with pytest.raises(psycopg.Error):c.execute('SELECT refresh_agent_task_receipt_events(%s)',(f['task'],))
        return
    with scoped(f) as c:queued=c.execute('SELECT refresh_agent_task_receipt_events(%s)',(f['task'],)).fetchone()[0]
    if mode in {'pending','paused','cancelled','new_input'}:
        assert queued['outcome'] in {'waiting','obsolete'},queued
        with psycopg.connect(future_db) as c:assert c.execute("SELECT count(*) FROM tasks WHERE delivery_context->>'source_kind'='scheduled' AND conversation_id=%s",(f['conv'],)).fetchone()[0]==0
        io.assert_awaited_once();return
    assert queued['outcome']=='queued',queued
    with scoped(f) as c:
        c.execute('SELECT refresh_agent_task_receipt_events(%s)',(f['task'],))
        assert c.execute("SELECT count(*) FROM tasks WHERE delivery_context->>'source_kind'='scheduled' AND conversation_id=%s",(f['conv'],)).fetchone()[0]==1
    task,claim=claim_internal(f,queued['task_id'])
    with scoped(f) as c:bundle=c.execute('SELECT load_agent_receipt_continuation(%s,%s)',(claim.task_id,claim.execution_token)).fetchone()[0]
    scope=SimpleNamespace(actor_user_id=f['user'],**state.execution_identity.model_dump())
    payload,candidate=continuation_payload(bundle,task=task,claim=claim,execution_scope=scope)
    assert payload['messages'][:-1]==history and payload['opaque_context']=={'must_keep':[1,2]}
    assert candidate.requirements==state.requirements and candidate.operations==state.operations and candidate.original_goal==state.original_goal
    with scoped(f) as c:
        malformed=copy.deepcopy(payload);malformed['messages'][1]['content']='篡改原用户要求'
        with pytest.raises(psycopg.Error):
            with c.transaction():c.execute('SELECT apply_agent_receipt_continuation(%s,%s,%s,%s)',(claim.task_id,claim.execution_token,state.revision,Jsonb(malformed)))
        malformed=copy.deepcopy(payload);malformed['opaque_context']={'must_keep':[]}
        with pytest.raises(psycopg.Error):
            with c.transaction():c.execute('SELECT apply_agent_receipt_continuation(%s,%s,%s,%s)',(claim.task_id,claim.execution_token,state.revision,Jsonb(malformed)))
        applied=c.execute('SELECT apply_agent_receipt_continuation(%s,%s,%s,%s)',(claim.task_id,claim.execution_token,state.revision,Jsonb(payload))).fetchone()[0]
    assert applied['outcome']=='saved'
    store=TaskOperationStore(database(f),task_id=claim.task_id,execution_token=claim.execution_token,turn_id=claim.turn_id,registry=build_capability_catalog())
    async def commit(value,expected):
        with scoped(f) as c:return c.execute('SELECT save_agent_task_checkpoint(%s,%s,%s,%s,%s)',(claim.task_id,claim.execution_token,'after_tool',Jsonb({**payload,'_task_execution':value.model_dump(mode='json')}),expected)).fetchone()[0]
    ctrl=TaskController(candidate,owner_task_id=claim.task_id,operation_store=store,result_context=context,commit=commit)
    op=candidate.operations[-1]
    found=await ctrl.control('read_task_scheduled_result',{'expected_revision':ctrl.state.revision,'operation_id':op.id},call_id='inspect')
    assert found.result['ok'],found.result
    if mode in {'positive','lost_return'}:
        imported=await ctrl.control('import_task_receipt',{'expected_revision':ctrl.state.revision,'operation_id':op.id},call_id='import')
        assert imported.result['ok'],imported.result
        done=await ctrl.control('complete_task_step',{'expected_revision':ctrl.state.revision,'step_id':'schedule','artifact_ids':[imported.result['imported_artifact_id']]},call_id='complete')
        assert done.result['ok'] and finish(ctrl.state).status=='completed'
    else:
        assert found.result['native_terminal_failure'] is True and ctrl.state.operations[-1].status!='completed'
    with scoped(f) as c:
        result=c.execute('SELECT commit_generation_turn(%s,%s,%s,%s,%s,%s,%s)',(claim.task_id,claim.execution_token,task['assistant_message_id'],Jsonb([{'type':'text','text':'核实原任务结果'}]),Jsonb({}),0,None)).fetchone()[0]
        assert result['outcome']=='committed'
        assert c.execute('SELECT status FROM agent_task_events WHERE continuation_task_id=%s',(claim.task_id,)).fetchone()[0]=='applied'
        assert c.execute('SELECT context_revision,turn_id FROM messages WHERE id=%s',(f['message'],)).fetchone()==(1,UUID(f['turn']))
    io.assert_awaited_once()


@pytest.mark.asyncio
async def test_native_pool_consumer_restart_routes_only_original_actor_and_deduplicates(future_db):
    from core.local_db import AsyncLocalDBClient
    from core.db_scope import AsyncScopedDatabaseClient,DatabaseScope,DatabaseAccessKind
    from services.task_execution.continuation_consumer import TaskContinuationConsumer
    f,state,context,io,history=await source_case(future_db,'positive')
    client=AsyncLocalDBClient(psycopg.conninfo.make_conninfo(f['dsn'],user='everydayai'),min_size=1,max_size=1)
    await client.open()
    try:
        # The stricter test fixture grants discovery within its worker tenant.
        # Domain reads/writes still rebind only the native row's actual actor.
        worker=AsyncScopedDatabaseClient(client,DatabaseScope(None,f['org'],DatabaseAccessKind.WORKER))
        assert await TaskContinuationConsumer(worker).scan_once()==1
        assert await TaskContinuationConsumer(worker).scan_once()==0
        with scoped(f) as c:
            assert c.execute("SELECT count(*) FROM tasks WHERE delivery_context->>'source_kind'='scheduled' AND conversation_id=%s",(f['conv'],)).fetchone()[0]==1
            assert c.execute("SELECT request_params ? '_agent_task_scheduled_v1' FROM tasks WHERE id=%s",(f['task'],)).fetchone()[0] is False
        async with client._pool.connection() as connection:
            cursor=await connection.execute("SELECT current_setting('app.actor_user_id',true) actor,current_setting('app.access_kind',true) access")
            row=await cursor.fetchone();assert not row['actor'] and not row['access']
    finally:await client.close()
    io.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('after_queue',[False,True])
@pytest.mark.parametrize('action',['pause','cancel'])
async def test_original_user_control_retains_schedule_receipt_and_handles_closed_or_queued_owner(future_db,after_queue,action):
    f,state,context,io,history=await source_case(future_db,'positive')
    queued=None
    if after_queue:
        with scoped(f) as c:queued=c.execute('SELECT refresh_agent_task_receipt_events(%s)',(f['task'],)).fetchone()[0]
        assert queued['outcome']=='queued'
    with scoped(f) as c:
        result=c.execute('SELECT control_agent_task(%s,%s,%s,%s)',(f['task'],f['user'],f['org'],action)).fetchone()[0]
        assert result['outcome']==('paused' if action=='pause' else 'cancelled'),result
        deferred=c.execute('SELECT refresh_agent_task_receipt_events(%s)',(f['task'],)).fetchone()[0]
        assert deferred['outcome'] in {'waiting','obsolete'}
        if action=='pause':
            resumed=c.execute('SELECT control_agent_task(%s,%s,%s,%s)',(f['task'],f['user'],f['org'],'resume')).fetchone()[0]
            assert resumed['outcome']=='enqueued',resumed
            saved=c.execute('SELECT state FROM agent_task_states WHERE root_task_id=%s',(f['task'],)).fetchone()[0]
            assert saved['status']=='waiting_approval' and saved['original_goal']==state.original_goal
            if not after_queue:
                again=c.execute('SELECT refresh_agent_task_receipt_events(%s)',(f['task'],)).fetchone()[0]
                assert again['outcome']=='queued'
            else:assert c.execute('SELECT status FROM tasks WHERE id=%s',(queued['task_id'],)).fetchone()[0]=='pending'
        else:
            assert c.execute('SELECT status FROM agent_task_events WHERE root_task_id=%s',(f['task'],)).fetchall() in ([],[('obsolete',)])
        assert c.execute('SELECT count(*) FROM scheduled_task_change_receipts WHERE org_id=%s',(f['org'],)).fetchone()[0]==1
        assert c.execute('SELECT context_revision,turn_id FROM messages WHERE id=%s',(f['message'],)).fetchone()==(1,UUID(f['turn']))
    io.assert_awaited_once()
