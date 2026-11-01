"""Original SDK refresh observer and native read/write ledgers on disposable PG."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4
import psycopg
import pytest

pytestmark = pytest.mark.usefixtures('erp_write_adapter_enabled')
from core.local_db import LocalDBClient
from core.db_scope import ScopedDatabaseClient,DatabaseScope,DatabaseAccessKind
from services.kuaimai.client import KuaiMaiClient
from services.kuaimai.session_rotations import rotation_recorder
from services.task_execution.erp_postconditions import source_fingerprint
from services.task_execution.reducer import finish
from tests.test_task_execution_erp_postconditions_postgres import (ROOT,postcondition_db,write_db,native_result_db,
    operation_db,task_db,composition_db,ecom_db,isolated_db,scoped,prepared)


@pytest.fixture(scope='module')
def rotation_db(postcondition_db):
    with psycopg.connect(postcondition_db) as c:c.execute((ROOT/'migrations/361_agent_erp_session_rotations.sql').read_text())
    yield postcondition_db
    with psycopg.connect(postcondition_db) as c:
        c.execute('DELETE FROM agent_erp_session_rotations')
        c.execute((ROOT/'migrations/rollback/361_agent_erp_session_rotations_rollback.sql').read_text())


async def refresh(f,old='local',new='rotated-session'):
    db=LocalDBClient(psycopg.conninfo.make_conninfo(f['dsn'],user='everydayai'),min_size=1,max_size=1)
    try:
        scoped_db=ScopedDatabaseClient(db,DatabaseScope(f['user'],f['org'],DatabaseAccessKind.RUNTIME))
        client=KuaiMaiClient(app_key='local',app_secret='local',access_token=old,org_id=f['org'],
            token_persister=AsyncMock(),token_rotation_recorder=rotation_recorder(scoped_db,f['org'],f['user']))
        before=source_fingerprint(client)
        client._client=SimpleNamespace(is_closed=False,post=AsyncMock(return_value=SimpleNamespace(
            raise_for_status=lambda:None,json=lambda:{'success':True,'session':{'accessToken':new}})))
        client._cache_token=AsyncMock();client._persist_token_to_db=AsyncMock()
        assert await client._do_refresh_token()
        client._client.post.assert_awaited_once();client._cache_token.assert_awaited_once();client._persist_token_to_db.assert_awaited_once()
        return before,source_fingerprint(client)
    finally:db.pool.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('hops',[1,2])
async def test_real_refresh_chain_completes_original_operation_without_rewrite_or_context_loss(rotation_db,hops):
    f,runtime,handler,ctx,call,store,http,io,cp,controller,write,read=await prepared(rotation_db,organization_client=True)
    await write();op=runtime.task_execution_state.operations[-1]
    with scoped(f) as c:original=c.execute('SELECT result FROM tool_invocations WHERE task_id=%s AND tool_call_id=%s',(f['task'],call.call_id)).fetchone()[0]
    await refresh(f)
    if hops==2:await refresh(f,'rotated-session','second-session')
    await read(source_token='second-session' if hops==2 else 'rotated-session')
    ctrl=controller();found=await ctrl.control('read_task_write_result',{'expected_revision':ctrl.state.revision,'operation_id':op.id},call_id='verify')
    assert found.result['ok'] and found.result['operation_status']=='completed',found.result
    imported=await ctrl.control('import_task_receipt',{'expected_revision':ctrl.state.revision,'operation_id':op.id},call_id='import')
    assert imported.result['ok'],imported.result
    done=await ctrl.control('complete_task_step',{'expected_revision':ctrl.state.revision,'step_id':op.step_id,'artifact_ids':[imported.result['imported_artifact_id']]},call_id='done')
    assert done.result['ok'] and finish(ctrl.state).status=='completed',done.result
    with scoped(f) as c:
        assert c.execute('SELECT result FROM tool_invocations WHERE task_id=%s AND tool_call_id=%s',(f['task'],call.call_id)).fetchone()[0]==original
        saved=c.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s',(f['task'],)).fetchone()[0]
        assert saved['messages']==cp['messages'] and saved['opaque_context']==cp['opaque_context']
        assert c.execute('SELECT count(*) FROM agent_erp_session_rotations WHERE org_id=%s',(f['org'],)).fetchone()[0]==hops
    io.assert_awaited_once();http.post.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['manual_switch','wrong_tenant','future_fact','revoked','replayed_read'])
async def test_unproved_session_or_invalid_native_read_cannot_finish_or_resend(rotation_db,mode):
    f,runtime,handler,ctx,call,store,http,io,cp,controller,write,read=await prepared(rotation_db,organization_client=True)
    await write();op=runtime.task_execution_state.operations[-1]
    if mode!='manual_switch':await refresh(f)
    await read(source_token='rotated-session')
    if mode in {'wrong_tenant','future_fact'}:
        with psycopg.connect(rotation_db) as c:
            c.execute('ALTER TABLE agent_erp_session_rotations DISABLE TRIGGER agent_erp_session_rotation_immutable')
            if mode=='wrong_tenant':
                other=str(uuid4());c.execute('INSERT INTO organizations(id) VALUES(%s)',(other,))
                c.execute('UPDATE agent_erp_session_rotations SET org_id=%s WHERE org_id=%s',(other,f['org']))
            else:c.execute("UPDATE agent_erp_session_rotations SET observed_at=clock_timestamp()+interval '1 day' WHERE org_id=%s",(f['org'],))
            c.execute('ALTER TABLE agent_erp_session_rotations ENABLE TRIGGER agent_erp_session_rotation_immutable')
    if mode=='replayed_read':
        with scoped(f) as c:c.execute("UPDATE tool_invocations SET result=jsonb_set(result,'{tool_result,execution,replayed}','true') WHERE task_id=%s AND tool_call_id='read-product'",(f['task'],))
    if mode=='revoked':
        with psycopg.connect(rotation_db) as c:c.execute("UPDATE org_members SET status='inactive' WHERE org_id=%s AND user_id=%s",(f['org'],f['user']))
    ctrl=controller()
    if mode=='revoked':
        from services.task_execution.operation_store import TaskOperationPersistenceError
        with pytest.raises(TaskOperationPersistenceError):await ctrl.control('read_task_write_result',{'expected_revision':ctrl.state.revision,'operation_id':op.id},call_id='verify')
    else:
        result=await ctrl.control('read_task_write_result',{'expected_revision':ctrl.state.revision,'operation_id':op.id},call_id='verify')
        assert not result.result['ok'] or result.result.get('operation_status')!='completed',result.result
    assert ctrl.state.operations[-1].status=='accepted'
    io.assert_awaited_once();http.post.assert_awaited_once()


@pytest.mark.asyncio
async def test_record_is_idempotent_immutable_scoped_and_rollback_checks_other_tenants(rotation_db):
    f,*_=await prepared(rotation_db,organization_client=True)
    before,after=await refresh(f)
    with scoped(f) as c:
        timestamp=c.execute('SELECT observed_at FROM agent_erp_session_rotations WHERE org_id=%s',(f['org'],)).fetchone()[0]
        assert c.execute('SELECT record_agent_erp_session_rotation(%s,%s,%s)',(f['org'],before,after)).fetchone()[0]['outcome']=='recorded'
        assert c.execute('SELECT observed_at FROM agent_erp_session_rotations WHERE org_id=%s',(f['org'],)).fetchone()[0]==timestamp
        with pytest.raises(psycopg.Error,match='IMMUTABLE'):
            with c.transaction():c.execute('UPDATE agent_erp_session_rotations SET observed_at=clock_timestamp() WHERE org_id=%s',(f['org'],))
        with pytest.raises(psycopg.Error,match='CONFLICT'):
            with c.transaction():c.execute('SELECT record_agent_erp_session_rotation(%s,%s,%s)',(f['org'],before,'a'*64))
        with pytest.raises(psycopg.Error,match='DENIED'):
            with c.transaction():c.execute('SELECT record_agent_erp_session_rotation(%s,%s,%s)',(str(uuid4()),before,after))
    with psycopg.connect(rotation_db) as c:
        with pytest.raises(psycopg.Error,match='NOT_DRAINED'):
            with c.transaction():c.execute((ROOT/'migrations/rollback/361_agent_erp_session_rotations_rollback.sql').read_text())
        assert c.execute("SELECT relforcerowsecurity FROM pg_class WHERE oid='agent_erp_session_rotations'::regclass").fetchone()[0]


@pytest.mark.asyncio
async def test_background_refresh_fact_is_readable_by_original_actor_after_client_restart(rotation_db):
    from core.local_db import AsyncLocalDBClient
    from core.db_scope import AsyncScopedDatabaseClient
    f,*_=await prepared(rotation_db,organization_client=True)
    db=AsyncLocalDBClient(psycopg.conninfo.make_conninfo(f['dsn'],user='everydayai'),min_size=1,max_size=1)
    await db._pool.open()
    try:
        worker=AsyncScopedDatabaseClient(db,DatabaseScope(None,None,DatabaseAccessKind.WORKER))
        record=rotation_recorder(worker,f['org'])
        await record(f['org'],'1'*64,'2'*64)
        with scoped(f) as c:
            path=c.execute('SELECT agent_erp_session_rotation_path(%s,%s,%s,clock_timestamp())',(f['org'],'1'*64,'2'*64)).fetchone()[0]
            assert len(path)==1 and path[0]['org_id']==f['org']
        with pytest.raises(PermissionError):await record(str(uuid4()),'2'*64,'3'*64)
    finally:await db._pool.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault',['gap','cycle','too_long','reverse_time'])
async def test_sql_path_does_not_promote_incomplete_or_impossible_chain(rotation_db,fault):
    f,*_=await prepared(rotation_db,organization_client=True)
    count=33 if fault=='too_long' else 2
    points=[format(i,'064x') for i in range(1,count+2)]
    with scoped(f) as c:
        for i in range(count):
            target=points[0] if fault=='cycle' and i==1 else points[i+1]
            c.execute('SELECT record_agent_erp_session_rotation(%s,%s,%s)',(f['org'],points[i],target))
    if fault=='reverse_time':
        with psycopg.connect(rotation_db) as c:
            c.execute('ALTER TABLE agent_erp_session_rotations DISABLE TRIGGER agent_erp_session_rotation_immutable')
            c.execute("UPDATE agent_erp_session_rotations SET observed_at=observed_at-interval '1 day' WHERE org_id=%s AND old_fingerprint=%s",(f['org'],points[1]))
            c.execute('ALTER TABLE agent_erp_session_rotations ENABLE TRIGGER agent_erp_session_rotation_immutable')
    with scoped(f) as c:
        target='f'*64 if fault in {'gap','cycle'} else points[-1]
        assert c.execute('SELECT agent_erp_session_rotation_path(%s,%s,%s,clock_timestamp())',(f['org'],points[0],target)).fetchone()[0] is None
