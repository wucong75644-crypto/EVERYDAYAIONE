"""Actual Reach/Actor tables and fenced RPCs; platform submission is local fake."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
import pytest

from services.agent.agent_result import AgentResult
from services.task_execution.contracts import TaskState
from services.task_execution.controller import TaskController
from services.task_execution.native_results import lifecycle_for
from services.task_execution.operation_store import TaskOperationStore
from services.task_execution.reducer import finish
from services.tools import ToolCall
from services.tools.result import UncertainToolInvocationError
from tests.test_task_execution_reach_operations import case,execute
from tests.test_task_execution_native_results_postgres import native_result_db
from tests.test_task_execution_operations_postgres import operation_db,task_db,composition_db,ecom_db,isolated_db,seed
from tests.test_task_execution_postgres import task_payload,save

ROOT=Path(__file__).parents[1]


@pytest.fixture(scope='module')
def reach_db(native_result_db):
    with psycopg.connect(native_result_db) as c:
        if not c.execute("SELECT 1 FROM pg_roles WHERE rolname='everydayai_runtime'").fetchone():
            c.execute('CREATE ROLE everydayai_runtime NOLOGIN')
        # The minimal image fixture omits the identity reads used by the
        # existing Reach SECURITY DEFINER membership helper.
        c.execute('GRANT SELECT ON org_members,organizations,users TO everydayai_owner')
        c.execute((ROOT/'migrations/291_agent_reach_connections.sql').read_text())
        c.execute((ROOT/'migrations/294_agent_reach_org_shared.sql').read_text())
        c.execute((ROOT/'migrations/349_agent_task_reach_receipts.sql').read_text())
        c.execute((ROOT/'migrations/350_agent_task_reach_recovery.sql').read_text())
    yield native_result_db
    with psycopg.connect(native_result_db) as c:
        # Only this fixture's disposable database is cleared; production
        # rollback must retain every recovery link or refuse to run.
        c.execute('DELETE FROM reach_operations')
        c.execute((ROOT/'migrations/rollback/350_agent_task_reach_recovery_rollback.sql').read_text())
        c.execute((ROOT/'migrations/rollback/349_agent_task_reach_receipts_rollback.sql').read_text())
        c.execute('DROP TABLE reach_operations,reach_connection_grants,reach_connections CASCADE')
        c.execute('DROP FUNCTION reach_active_member(uuid,boolean)')


def scoped(f):
    c=psycopg.connect(psycopg.conninfo.make_conninfo(f['dsn'],user='everydayai'))
    c.execute("SELECT set_config('app.actor_user_id',%s,true),set_config('app.org_id',%s,true),set_config('app.access_kind','runtime',true)",(f['user'],f['org']))
    return c


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['returned','legacy_collision','domain_unknown','lost_return','lost_receipt','legacy_unlinked','partial','missing_receipt','empty_receipt','not_dispatched','retry_exhausted','retry_wait','failed_after_dispatch'])
async def test_native_domain_receipt_survives_checkpoint_and_never_resends_for_new_call_id(reach_db,request,mode):
    import asyncio
    from psycopg_pool import ConnectionPool
    from core.db_scope import DatabaseScope,DatabaseAccessKind
    from services.agent.agent_reach.connections import Connections
    f=seed(reach_db);f['org']=str(uuid4())
    runtime,handler,context,call,service,io,_=case()
    raw=task_payload(f)['_task_execution']
    raw.update(org_id=f['org'],image_binding_version=3,steps=[s.model_dump(mode='json') for s in runtime.task_execution_state.steps])
    raw['requirements'][0]['artifact_kind']='receipt'
    state=TaskState.model_validate(raw);runtime.task_id=f['task'];runtime.task_execution_state=state
    handler._actor_runtime=runtime;handler._actor_turn_id=f['turn'];handler._actor_execution_token=f['token']
    context=replace(context,actor_user_id=f['user'],workspace_owner_id=f['user'],org_id=f['org'],
        conversation_id=f['conv'],task_id=f['task'])
    connection_id=call.arguments['connection_id']
    with psycopg.connect(reach_db) as c:
        c.execute('INSERT INTO organizations(id) VALUES(%s)',(f['org'],))
        c.execute('INSERT INTO org_members(org_id,user_id) VALUES(%s,%s)',(f['org'],f['user']))
        c.execute('UPDATE tasks SET org_id=%s WHERE id=%s',(f['org'],f['task']))
        c.execute("INSERT INTO reach_connections(id,org_id,platform,account_id,created_by) VALUES(%s,%s,'twitter','local-account',%s)",
            (connection_id,f['org'],f['user']))
    with scoped(f) as c:save(c,f,{'messages':[],'_task_execution':state.model_dump(mode='json'),'opaque_context':{'preserved':True}})
    class DB:
        def rpc(self,name,params):
            async def run():
                query=sql.SQL('SELECT {}({})').format(sql.Identifier(name),sql.SQL(',').join(sql.SQL('{}=>%s').format(sql.Identifier(k)) for k in params))
                with scoped(f) as c:value=c.execute(query,tuple(params.values())).fetchone()[0]
                return SimpleNamespace(data=value)
            return SimpleNamespace(execute=run)
    store=TaskOperationStore(DB(),task_id=f['task'],execution_token=f['token'],turn_id=f['turn'],registry=service.registry)
    runtime.task_operation_store=store
    receipt={'action':'create_comment','remote_id':'native-platform-comment','target':call.arguments['url']}
    if mode=='missing_receipt':receipt.pop('remote_id')
    if mode=='empty_receipt':receipt={}
    pool=ConnectionPool(psycopg.conninfo.make_conninfo(reach_db,user='everydayai'),min_size=1,max_size=2)
    request.addfinalizer(pool.close)
    native=Connections(pool,DatabaseScope(f['user'],f['org'],DatabaseAccessKind.RUNTIME))
    if mode=='legacy_collision':
        previous,_=native.reserve(call.call_id,{'id':connection_id,'credential_version':1},'a'*64)
        native.settle(previous,'succeeded',{**receipt,'remote_id':'older-unrelated-operation'})
    async def platform_fake(_):
        from services.tools.dispatcher import current_dispatch_call_id
        native_call_id=current_dispatch_call_id()
        options={} if mode=='legacy_unlinked' else {'native_owner':{'task_id':f['task'],'execution_token':f['token']}}
        native_id,_=await asyncio.to_thread(native.reserve,native_call_id,{'id':connection_id,'credential_version':1},'a'*64,**options)
        if mode in {'retry_exhausted','retry_wait'} or (mode=='not_dispatched' and native_call_id==call.call_id) or mode=='failed_after_dispatch':
            stage='dispatched_or_unknown' if mode=='failed_after_dispatch' else 'not_dispatched'
            code='ACCESS_BUDGET_WAIT' if mode=='retry_wait' else 'CONFIG_REQUIRED'
            metadata={}
            if mode=='retry_wait':
                from datetime import datetime,timedelta,timezone
                metadata['next_eligible_at']=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()
            await asyncio.to_thread(native.settle,native_id,'failed',{'stage':stage,'code':code})
            return AgentResult('本地预算或失败模拟',status='error',error_message=code,metadata=metadata)
        await asyncio.to_thread(native.settle,native_id,'uncertain' if mode=='domain_unknown' else 'succeeded',receipt)
        if mode in {'lost_return','legacy_unlinked'}:raise ConnectionError('local simulated return lost')
        if mode=='lost_receipt':return AgentResult('本地模拟返回缺少receipt字段')
        return AgentResult('本地平台模拟响应',status='partial' if mode=='partial' else 'success',metadata={'platform':'twitter','receipt':receipt})
    io.side_effect=platform_fake
    result=await execute(runtime,handler,context,call,service)
    if mode not in {'lost_return','legacy_unlinked'}:assert result.exception is None,result.exception
    current=runtime.task_execution_state;op=current.operations[-1]
    confirmed=mode in {'returned','legacy_collision','lost_return','lost_receipt'}
    safe_retry=mode in {'not_dispatched','retry_exhausted','retry_wait'}
    assert op.status==('completed' if confirmed else 'failed_before_dispatch' if safe_retry else 'uncertain')
    if mode=='lost_return':
        with pytest.raises(psycopg.errors.RaiseException,match='ROLLBACK_HAS_NATIVE_LINKS'):
            with psycopg.connect(reach_db) as c:
                c.execute((ROOT/'migrations/rollback/350_agent_task_reach_recovery_rollback.sql').read_text())
        with psycopg.connect(reach_db) as c:
            assert c.execute("SELECT relforcerowsecurity FROM pg_class WHERE oid='reach_operations'::regclass").fetchone()[0]
            assert c.execute('SELECT count(*) FROM reach_operations WHERE native_invocation_id IS NOT NULL').fetchone()[0]>0
            assert c.execute("SELECT to_regprocedure('reconcile_agent_reach_operation(uuid,uuid,uuid,bigint,text)') IS NOT NULL").fetchone()[0]
    params=(f['task'],f['token'],f['task'],current.revision,op.id)
    with scoped(f) as c:
        denied=c.execute('SELECT read_agent_task_reach_receipt(%s,%s,%s,%s,%s)',
            (f['task'],str(uuid4()),f['task'],current.revision,op.id)).fetchone()[0]
        assert denied['outcome']=='ownership_lost'
        stale=c.execute('SELECT read_agent_task_reach_receipt(%s,%s,%s,%s,%s)',
            (f['task'],f['token'],f['task'],current.revision+1,op.id)).fetchone()[0]
        assert stale['outcome']=='stale'
        proof=c.execute('SELECT read_agent_task_reach_receipt(%s,%s,%s,%s,%s)',params).fetchone()[0]['proof']
        assert 'secret' not in str(proof) and proof['connection_id']==connection_id
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with scoped({**f,'user':str(uuid4())}) as c:
            c.execute('SELECT read_agent_task_reach_receipt(%s,%s,%s,%s,%s)',params)
    with scoped(f) as c:
        cp=c.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s',(f['task'],)).fetchone()[0]
        assert cp['opaque_context']=={'preserved':True}
        restored=TaskState.model_validate(cp['_task_execution'])
        assert restored==current
    runtime.task_execution_state=restored
    new=ToolCall('new-model-id',call.name,dict(call.arguments));context=replace(context,call_id=new.call_id)
    lifecycle=lifecycle_for(handler,context,service.registry,new.name)
    if safe_retry:
        from services.task_execution.contracts import TaskError
        new=ToolCall('corrected-attempt',call.name,{**dict(call.arguments),'content':'已重新确认的最终内容'})
        context=replace(context,call_id=new.call_id)
        declined=await execute(runtime,handler,context,new,service,approved=False)
        assert declined.status!='success' and io.await_count==1
        if mode=='retry_wait':
            with pytest.raises(TaskError,match='WAIT_UNTIL_ELIGIBLE'):
                await execute(runtime,handler,context,new,service)
            assert runtime.task_execution_state==current;io.assert_awaited_once()
            with scoped(f) as c:
                assert c.execute('SELECT count(*) FROM tool_invocations WHERE task_id=%s',(f['task'],)).fetchone()[0]==1
                assert c.execute('SELECT count(*) FROM reach_operations WHERE actor_user_id=%s',(f['user'],)).fetchone()[0]==1
            return
        for attempt in range(8 if mode=='retry_exhausted' else 1):
            candidate=ToolCall('retry-'+str(attempt),new.name,dict(new.arguments))
            await execute(runtime,handler,replace(context,call_id=candidate.call_id),candidate,service)
        op=runtime.task_execution_state.operations[-1]
        assert len(op.previous_attempts)==(8 if mode=='retry_exhausted' else 1)
        if mode=='retry_exhausted':
            exhausted=ToolCall('exhausted-attempt',new.name,dict(new.arguments))
            with pytest.raises(TaskError,match='RETRY_EXHAUSTED'):
                await execute(runtime,handler,replace(context,call_id=exhausted.call_id),exhausted,service)
        else:assert op.status=='completed'
        with scoped(f) as c:
            assert c.execute('SELECT count(*) FROM tool_invocations WHERE task_id=%s',(f['task'],)).fetchone()[0]==io.await_count
            assert c.execute('SELECT count(*) FROM reach_operations WHERE actor_user_id=%s',(f['user'],)).fetchone()[0]==io.await_count
        return
    if confirmed:
        replay=await lifecycle.replay(new,context,service.policy.decide(new.name,context,new.arguments))
        assert replay.execution.replayed and not replay.execution.handler_started
        if mode in {'lost_return','lost_receipt'}:
            assert replay.metadata['reconciled'] is True
            again=await lifecycle_for(handler,context,service.registry,new.name).replay(new,context,service.policy.decide(new.name,context,new.arguments))
            assert again.execution.replayed
            with scoped(f) as c:
                assert c.execute('SELECT status FROM tool_invocations WHERE task_id=%s',(f['task'],)).fetchone()[0]=='uncertain'
        async def commit(candidate,expected):
            with scoped(f) as c:return save(c,f,{**cp,'_task_execution':candidate.model_dump(mode='json')},expected=expected)
        controller=TaskController(current,owner_task_id=f['task'],commit=commit,operation_store=store)
        imported=await controller.control('import_task_receipt',{'expected_revision':current.revision,'operation_id':op.id},call_id='import')
        assert imported.result['ok'],imported.result
        done=await controller.control('complete_task_step',{'expected_revision':controller.state.revision,'step_id':'write',
            'artifact_ids':[imported.result['imported_artifact_id']]},call_id='complete')
        assert done.result['ok'] and finish(controller.state).status=='completed'
    else:
        with pytest.raises(UncertainToolInvocationError):await lifecycle.replay(new,context,service.policy.decide(new.name,context,new.arguments))
    io.assert_awaited_once()
    with scoped(f) as c:
        assert c.execute('SELECT count(*) FROM tool_invocations WHERE task_id=%s',(f['task'],)).fetchone()[0]==1
        assert c.execute('SELECT count(*) FROM reach_operations WHERE actor_user_id=%s',(f['user'],)).fetchone()[0]==(2 if mode=='legacy_collision' else 1)
