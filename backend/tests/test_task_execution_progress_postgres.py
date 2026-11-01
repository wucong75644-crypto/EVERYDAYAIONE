from pathlib import Path
from uuid import uuid4
import psycopg
import pytest
from tests.test_task_execution_postgres import task_db,composition_db,ecom_db,isolated_db,seed,connect,task_payload,save
ROOT=Path(__file__).parents[1]


@pytest.fixture(scope='module')
def progress_db(task_db):
    with psycopg.connect(task_db) as c:c.execute((ROOT/'migrations/362_agent_task_public_progress.sql').read_text())
    yield task_db
    with psycopg.connect(task_db) as c:c.execute((ROOT/'migrations/rollback/362_agent_task_public_progress_rollback.sql').read_text())


def prepared(dsn):
    f=seed(dsn);f['assistant']=str(uuid4())
    with psycopg.connect(dsn) as c:
        c.execute("INSERT INTO messages(id,conversation_id,role,content) VALUES(%s,%s,'assistant','[]')",(f['assistant'],f['conv']))
        c.execute('UPDATE tasks SET assistant_message_id=%s WHERE id=%s',(f['assistant'],f['task']))
    with connect(f) as c:save(c,f,task_payload(f))
    return f


def view(c,f,message=None):
    return c.execute('SELECT read_agent_task_progress(%s,%s)',(f['task'],message or f.get('requested_message',f['assistant']))).fetchone()[0]


def test_authenticated_message_progress_is_read_only_and_survives_completed_actor_window(progress_db):
    f=prepared(progress_db)
    with connect(f) as c:
        before=c.execute('SELECT state,revision,owner_task_id FROM agent_task_states WHERE root_task_id=%s',(f['task'],)).fetchone()
        assert view(c,f)['state']==before[0]
        c.execute("UPDATE tasks SET status='completed',execution_token=NULL,lease_expires_at=NULL WHERE id=%s",(f['task'],))
        assert view(c,f)['state']==before[0]
        assert c.execute('SELECT state,revision,owner_task_id FROM agent_task_states WHERE root_task_id=%s',(f['task'],)).fetchone()==before
        assert view(c,f,f['message'])=={'outcome':'not_found'}
        assert view(c,f,str(uuid4()))=={'outcome':'not_found'}


@pytest.mark.parametrize('fault',['other_user','other_org','inactive_user','deleted_message','wrong_conversation','other_root','channel'])
def test_progress_never_uses_marker_as_authority_or_bypasses_current_scope(progress_db,fault):
    f=prepared(progress_db)
    if fault in {'inactive_user','deleted_message','wrong_conversation','other_root','channel'}:
        with psycopg.connect(progress_db) as c:
            if fault=='inactive_user':c.execute("UPDATE users SET status='disabled' WHERE id=%s",(f['user'],))
            if fault=='deleted_message':c.execute('UPDATE tasks SET assistant_message_id=NULL WHERE id=%s',(f['task'],))
            if fault=='wrong_conversation':
                other=str(uuid4());c.execute('INSERT INTO conversations(id,user_id) VALUES(%s,%s)',(other,f['user']))
                c.execute('UPDATE messages SET conversation_id=%s WHERE id=%s',(other,f['assistant']))
            if fault=='other_root':
                f['requested_message']=str(uuid4())
                c.execute("INSERT INTO messages(id,conversation_id,role,content) VALUES(%s,%s,'assistant','[]')",(f['requested_message'],f['conv']))
                c.execute("INSERT INTO tasks(id,user_id,conversation_id,type,status,assistant_message_id) VALUES(%s,%s,%s,'chat','completed',%s)",
                    (str(uuid4()),f['user'],f['conv'],f['requested_message']))
            if fault=='channel':
                c.execute("UPDATE agent_task_states SET state=jsonb_set(state,'{execution_identity,context_scope}','\"channel\"') WHERE root_task_id=%s",(f['task'],))
                c.execute("UPDATE conversation_turn_checkpoints SET state=jsonb_set(state,'{_task_execution,execution_identity,context_scope}','\"channel\"') WHERE task_id=%s",(f['task'],))
    with connect(f) as c:
        if fault=='other_user':c.execute("SELECT set_config('app.actor_user_id',%s,true)",(str(uuid4()),))
        if fault=='other_org':c.execute("SELECT set_config('app.org_id',%s,true)",(str(uuid4()),))
        assert view(c,f)=={'outcome':'not_found'}


def test_actual_progress_service_returns_allowlist_and_scope_revocation_hides_state(progress_db):
    from core.local_db import LocalDBClient
    from services.task_execution.progress import read
    from core.exceptions import NotFoundError
    f=prepared(progress_db)
    db=LocalDBClient(psycopg.conninfo.make_conninfo(f['dsn'],user='everydayai'),min_size=1,max_size=1)
    try:
        value=read(db,root_task_id=f['task'],message_id=f['assistant'],user_id=f['user'],org_id=None)
        assert value['root_task_id']==f['task'] and value['status']=='active'
        assert set(value)=={'root_task_id','revision','status','steps','completed_steps','total_steps',
            'completed_requirements','total_requirements','pending_results','unconfirmed_results'}
        assert 'original_goal' not in value and 'operations' not in value and 'execution_identity' not in value
        with pytest.raises(NotFoundError):read(db,root_task_id=f['task'],message_id=f['message'],user_id=f['user'],org_id=None)
    finally:db.pool.close()


@pytest.mark.parametrize('fault',['active','revoked_member','inactive_org','other_actor_scope','worker_scope'])
def test_current_org_membership_and_runtime_scope_are_required_even_for_completed_windows(progress_db,fault):
    f=seed(progress_db);org=str(uuid4());assistant=str(uuid4())
    with psycopg.connect(progress_db) as c:
        c.execute('INSERT INTO organizations(id) VALUES(%s)',(org,))
        c.execute('INSERT INTO org_members(org_id,user_id) VALUES(%s,%s)',(org,f['user']))
        c.execute('UPDATE conversations SET org_id=%s WHERE id=%s',(org,f['conv']))
        c.execute("INSERT INTO messages(id,conversation_id,role,content) VALUES(%s,%s,'assistant','[]')",(assistant,f['conv']))
        c.execute('UPDATE tasks SET org_id=%s,assistant_message_id=%s WHERE id=%s',(org,assistant,f['task']))
    f['assistant']=assistant
    with connect(f) as c:
        c.execute("SELECT set_config('app.org_id',%s,true)",(org,))
        payload=task_payload(f);payload['_task_execution']['org_id']=org
        save(c,f,payload)
    with psycopg.connect(progress_db) as c:
        c.execute("UPDATE tasks SET status='completed',execution_token=NULL,lease_expires_at=NULL WHERE id=%s",(f['task'],))
        if fault=='revoked_member':c.execute("UPDATE org_members SET status='inactive' WHERE org_id=%s AND user_id=%s",(org,f['user']))
        if fault=='inactive_org':c.execute("UPDATE organizations SET status='inactive' WHERE id=%s",(org,))
    with connect(f) as c:
        c.execute("SELECT set_config('app.org_id',%s,true)",(org,))
        if fault=='other_actor_scope':c.execute("SELECT set_config('app.actor_user_id',%s,true)",(str(uuid4()),))
        if fault=='worker_scope':c.execute("SELECT set_config('app.access_kind','worker',true)")
        assert view(c,f)['outcome']==('found' if fault=='active' else 'not_found')


@pytest.mark.asyncio
async def test_real_http_route_binds_message_and_returns_only_uncached_progress(progress_db):
    from types import SimpleNamespace
    import httpx
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from api.deps import get_org_context,get_scoped_db
    from api.routes.task import router
    from core.local_db import LocalDBClient
    from core.exceptions import AppException
    f=prepared(progress_db)
    db=LocalDBClient(psycopg.conninfo.make_conninfo(f['dsn'],user='everydayai'),min_size=1,max_size=1)
    app=FastAPI();app.include_router(router)
    app.dependency_overrides[get_org_context]=lambda:SimpleNamespace(user_id=f['user'],org_id=None)
    app.dependency_overrides[get_scoped_db]=lambda:db
    @app.exception_handler(AppException)
    async def exception_handler(request,error):return JSONResponse({'code':error.code},status_code=error.status_code)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://local-test') as client:
            response=await client.get(f"/tasks/{f['task']}/execution",params={'message_id':f['assistant']})
            assert response.status_code==200,response.text
            assert response.headers['cache-control']=='no-store'
            assert response.json()['status']=='active'
            assert 'state' not in response.json() and 'original_goal' not in response.text
            denied=await client.get(f"/tasks/{f['task']}/execution",params={'message_id':f['message']})
            assert denied.status_code==404,denied.text
    finally:db.pool.close()
