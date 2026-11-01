"""Real Actor/checkpoint/native ChangeSet schemas; no planner/provider calls."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
import pytest

from services.agent.agent_result import AgentResult
from services.scheduler.chat_task_manager import FormBlockResult
from services.task_execution.contracts import TaskState
from services.task_execution.controller import TaskController
from services.task_execution.operation_store import TaskOperationStore
from services.task_execution.reducer import finish
from services.tools import ToolCall,ToolDispatcher,ToolExecutionService
from tests.test_task_execution_scheduled import case,execute
from tests.test_task_execution_native_results_postgres import native_result_db
from tests.test_task_execution_operations_postgres import operation_db,task_db,composition_db,ecom_db,isolated_db,seed
from tests.test_task_execution_postgres import task_payload,save

ROOT=Path(__file__).parents[1]


@pytest.fixture(scope='module')
def scheduled_db(native_result_db):
    with psycopg.connect(native_result_db) as c:
        # The original domain schemas, without starting its paid planner or scheduler.
        c.execute('ALTER TABLE change_sets RENAME TO fixture_image_change_sets')
        c.execute('GRANT REFERENCES ON organizations,users TO everydayai')
        c.execute('SET LOCAL ROLE everydayai')
        c.execute((ROOT/'migrations/248_change_sets.sql').read_text())
        receipt=(ROOT/'migrations/249_scheduled_task_changeset_adapter.sql').read_text().split('CREATE TABLE IF NOT EXISTS public.scheduled_task_change_receipts',1)[1].split('CREATE INDEX',1)[0]
        c.execute('CREATE TABLE public.scheduled_task_change_receipts'+receipt)
        c.execute('ALTER TABLE scheduled_task_change_receipts ENABLE ROW LEVEL SECURITY')
        c.execute('ALTER TABLE scheduled_task_change_receipts FORCE ROW LEVEL SECURITY')
        c.execute("CREATE POLICY test_scheduled_receipt_scope ON scheduled_task_change_receipts USING(org_id=NULLIF(current_setting('app.org_id',true),'')::uuid)")
        c.execute((ROOT/'migrations/351_agent_task_effect_results.sql').read_text())
        c.execute((ROOT/'migrations/352_agent_task_scheduled_receipts.sql').read_text())
    yield native_result_db
    with psycopg.connect(native_result_db) as c:
        c.execute('DELETE FROM agent_task_scheduled_bindings')
        c.execute((ROOT/'migrations/rollback/352_agent_task_scheduled_receipts_rollback.sql').read_text())
        c.execute((ROOT/'migrations/rollback/351_agent_task_effect_results_rollback.sql').read_text())
        c.execute('DROP TABLE scheduled_task_change_receipts')
        c.execute('DELETE FROM change_checks; DELETE FROM change_events; DELETE FROM change_sets')
        c.execute((ROOT/'migrations/rollback/248_change_sets_rollback.sql').read_text())
        c.execute('ALTER TABLE fixture_image_change_sets RENAME TO change_sets')


def scoped(f):
    c=psycopg.connect(psycopg.conninfo.make_conninfo(f['dsn'],user='everydayai'))
    c.execute("SELECT set_config('app.actor_user_id',%s,true),set_config('app.org_id',%s,true),set_config('app.access_kind','runtime',true)",(f['user'],f['org']))
    return c


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['form','direct','lost_transition','wrong_actor','missing_receipt','wrong_action','wrong_resource','lost_return','ambiguous'])
async def test_native_scheduled_receipt_binding_resume_and_no_duplicate(scheduled_db,mode):
    f=seed(scheduled_db);f['org']=str(uuid4());assistant=str(uuid4())
    runtime,handler,context,call,_,_,_=case()
    raw=task_payload(f)['_task_execution'];raw.update(org_id=f['org'],image_binding_version=3,
        steps=[s.model_dump(mode='json') for s in runtime.task_execution_state.steps])
    raw['requirements'][0]['artifact_kind']='receipt';state=TaskState.model_validate(raw)
    runtime.task_execution_state=state;runtime.task_id=f['task'];runtime.replay_save_sequence=0
    handler._actor_turn_id=f['turn'];handler._actor_execution_token=f['token']
    context=replace(context,actor_user_id=f['user'],workspace_owner_id=f['user'],org_id=f['org'],conversation_id=f['conv'],task_id=f['task'])
    with psycopg.connect(scheduled_db) as c:
        c.execute('INSERT INTO organizations(id) VALUES(%s)',(f['org'],))
        c.execute('INSERT INTO org_members(org_id,user_id) VALUES(%s,%s)',(f['org'],f['user']))
        c.execute("INSERT INTO messages(id,conversation_id,role) VALUES(%s,%s,'assistant')",(assistant,f['conv']))
        c.execute('UPDATE tasks SET org_id=%s,assistant_message_id=%s WHERE id=%s',(f['org'],assistant,f['task']))
    cp={'messages':[{'role':'system','content':'保留的原历史摘要'},{'role':'user','content':'原始任务'}],
        'opaque_context':{'unchanged':True},'_task_execution':state.model_dump(mode='json')}
    with scoped(f) as c:save(c,f,cp)
    class DB:
        def rpc(self,name,params):
            async def run():
                query=sql.SQL('SELECT {}({})').format(sql.Identifier(name),sql.SQL(',').join(sql.SQL('{}=>%s').format(sql.Identifier(k)) for k in params))
                with scoped(f) as c:value=c.execute(query,tuple(params.values())).fetchone()[0]
                return SimpleNamespace(data=value)
            return SimpleNamespace(execute=run)
    from services.tools.catalog import build_capability_catalog
    registry=build_capability_catalog();store=TaskOperationStore(DB(),task_id=f['task'],execution_token=f['token'],turn_id=f['turn'],registry=registry)
    runtime.task_operation_store=store;change_id=str(uuid4());business_task=str(uuid4());receipt_id=str(uuid4())
    async def native(args):
        op=runtime.task_execution_state.operations[-1]
        with scoped(f) as c:
            key=c.execute('SELECT agent_task_scheduled_request_key(%s,%s,%s,%s,%s)',(f['task'],f['token'],call.call_id,args['action'],op.arguments_sha256)).fetchone()[0]
            assert key=='agent-task-scheduled:'+op.id
            with pytest.raises(psycopg.Error):
                with c.transaction():c.execute('SELECT agent_task_scheduled_request_key(%s,%s,%s,%s,%s)',(f['task'],str(uuid4()),call.call_id,args['action'],op.arguments_sha256))
            for wrong_action,wrong_hash in [('delete',op.arguments_sha256),('create','0'*64)]:
                with pytest.raises(psycopg.Error):
                    with c.transaction():c.execute('SELECT agent_task_scheduled_request_key(%s,%s,%s,%s,%s)',
                        (f['task'],f['token'],call.call_id,wrong_action,wrong_hash))
            if mode=='form':return FormBlockResult({'form_type':'scheduled_task_create','form_id':'task_effect_'+op.id,'fields':[]},'请确认')
            c.execute("INSERT INTO change_sets(id,org_id,resource_type,resource_id,operation,base_revision,status,idempotency_key,created_by) VALUES(%s,%s,'scheduled_task',%s,'create','0','awaiting_approval',%s,%s)",
                (change_id,f['org'],business_task,key,f['user']))
        if mode=='lost_return':raise RuntimeError('native return lost after original ChangeSet persisted')
        return AgentResult('等待原检查与确认',metadata={'change_set':{'id':change_id}})
    io=AsyncMock(side_effect=native);service=ToolExecutionService(registry,ToolDispatcher({('legacy','manage_scheduled_task'):io}))
    native_result=await execute(runtime,handler,context,call,service)
    if mode!='lost_return':assert native_result.exception is None,repr(native_result.exception)
    state=runtime.task_execution_state;op=state.operations[-1]
    assert op.status==('uncertain' if mode=='lost_return' else 'accepted')
    with scoped(f) as c:
        stored=c.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s',(f['task'],)).fetchone()[0]
        assert stored['opaque_context']==cp['opaque_context'] and stored['messages']==cp['messages']
        if mode=='form':
            c.execute("INSERT INTO change_sets(id,org_id,resource_type,resource_id,operation,base_revision,status,idempotency_key,created_by) VALUES(%s,%s,'scheduled_task',%s,'create','0','applied',%s,%s)",
                (change_id,f['org'],business_task,f'chat-form:{f["conv"]}:{assistant}:task_effect_{op.id}',f['user']))
        if mode=='ambiguous':
            c.execute("INSERT INTO change_sets(org_id,resource_type,resource_id,operation,base_revision,status,idempotency_key,created_by) VALUES(%s,'scheduled_task',%s,'create','0','applied',%s,%s)",
                (f['org'],business_task,f'chat-form:{f["conv"]}:{assistant}:task_effect_{op.id}',f['user']))
        if mode=='wrong_actor':c.execute('UPDATE change_sets SET created_by=%s WHERE id=%s',(str(uuid4()),change_id))
        if mode=='wrong_action':c.execute("UPDATE change_sets SET operation='delete' WHERE id=%s",(change_id,))
        if mode=='wrong_resource':c.execute('UPDATE change_sets SET resource_id=%s WHERE id=%s',(str(uuid4()),change_id))
        if mode=='lost_transition':c.execute("UPDATE change_sets SET status='failed' WHERE id=%s",(change_id,))
        if mode!='missing_receipt':
            c.execute("INSERT INTO scheduled_task_change_receipts(id,org_id,change_set_id,idempotency_key,task_id,operation,base_revision,new_revision,result) VALUES(%s,%s,%s,%s,%s,'create',0,1,%s)",
                (receipt_id,f['org'],change_id,'commit:'+change_id,business_task,Jsonb({'outcome':'created','task_id':business_task,'new_revision':1})))
    if mode=='direct':
        # The table owner must see foreign-tenant bindings during rollback.
        with scoped(f) as c:
            c.execute("SELECT set_config('app.actor_user_id',%s,true)",(str(uuid4()),))
            assert c.execute('SELECT count(*) FROM agent_task_scheduled_bindings').fetchone()[0]==0
            with pytest.raises(psycopg.Error,match='TASK_SCHEDULED_BINDINGS_NOT_DRAINED'):
                with c.transaction():c.execute((ROOT/'migrations/rollback/352_agent_task_scheduled_receipts_rollback.sql').read_text())
            assert c.execute("SELECT relforcerowsecurity FROM pg_class WHERE oid='agent_task_scheduled_bindings'::regclass").fetchone()[0] is True
    async def commit(candidate,expected):
        with scoped(f) as c:
            current=c.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s',(f['task'],)).fetchone()[0]
            return save(c,f,{**current,'_task_execution':candidate.model_dump(mode='json')},expected=expected)
    ctrl=TaskController(state,owner_task_id=f['task'],operation_store=store,result_context=context,commit=commit)
    result=await ctrl.control('read_task_scheduled_result',{'expected_revision':state.revision,'operation_id':op.id},call_id='check')
    assert result.result['ok'],result.result
    if mode in {'wrong_actor','wrong_action','wrong_resource','missing_receipt','ambiguous'}:
        assert ctrl.state==state and result.result['operation_status']=='accepted';return
    assert result.result['operation_status']=='completed'
    imported=await ctrl.control('import_task_receipt',{'expected_revision':ctrl.state.revision,'operation_id':op.id},call_id='import')
    assert imported.result['ok'],imported.result
    completed=await ctrl.control('complete_task_step',{'expected_revision':ctrl.state.revision,'step_id':'schedule',
        'artifact_ids':[imported.result['imported_artifact_id']]},call_id='complete')
    assert completed.result['ok'] and finish(ctrl.state).status=='completed'
    with scoped(f) as c:
        restored=c.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s',(f['task'],)).fetchone()[0]
        assert restored['messages']==cp['messages'] and restored['opaque_context']==cp['opaque_context']
        assert c.execute('SELECT count(*) FROM tool_invocations WHERE task_id=%s',(f['task'],)).fetchone()[0]==1
    io.assert_awaited_once()
