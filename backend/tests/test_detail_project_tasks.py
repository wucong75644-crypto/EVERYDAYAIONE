"""Multi-project identities, capacity waiting, list projections and recovery clocks on real PostgreSQL."""
import base64
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
import pytest
from core.local_db import LocalDBClient
from core.exceptions import AppException
from services.detail_project_service import DetailProjectService
from services.detail_project_tasks import DetailProjectTasks, execution_state
from services.handlers.chat_image_lifecycle import ChatImageLifecycle
from tests.test_chat_image_lifecycle_postgres import isolated_db
from tests.test_ecommerce_recovery_postgres import ecom_db
from tests.test_detail_page_generation_postgres import page_db, seed, scoped, ready_item
from tests.test_detail_page_delivery_recovery import delivery_db, opt_in, job_claim, due, settle

ROOT=Path(__file__).parents[1]
@pytest.fixture(scope='module')
def multi_db(delivery_db):
    with psycopg.connect(delivery_db) as db:
        db.execute((ROOT/'migrations/286_detail_project_tasks.sql').read_text())
        db.execute((ROOT/'migrations/rollback/286_detail_project_tasks_rollback.sql').read_text())
        assert db.execute("SELECT COUNT(*) FROM pg_proc WHERE proname='create_detail_project'").fetchone()[0]==0
        db.execute((ROOT/'migrations/286_detail_project_tasks.sql').read_text())
    return delivery_db


def new_user(dsn):
    user=str(uuid4())
    with psycopg.connect(dsn) as db: db.execute('INSERT INTO users(id,credits) VALUES(%s,1000)',(user,))
    return user


def create(dsn,user,request=None):
    request=request or str(uuid4())
    with scoped(dsn,user) as db:
        return str(db.execute('SELECT create_detail_project(%s)',(request,)).fetchone()[0])


def test_creation_replays_concurrently_but_allows_independent_drafts(multi_db):
    user=new_user(multi_db); request=str(uuid4())
    with ThreadPoolExecutor(2) as pool:
        assert list(pool.map(lambda _:create(multi_db,user,request),range(2)))==[request,request]
    other=create(multi_db,user)
    assert other!=request
    with scoped(multi_db,user) as db:
        with pytest.raises(psycopg.Error,match='DETAIL_PROJECT_AMBIGUOUS'):
            db.execute("SELECT attach_detail_project_image(%s,NULL,'a.png','product')",(user,))
    outsider=new_user(multi_db)
    with pytest.raises(psycopg.Error,match='DENIED'):create(multi_db,outsider,request)


def test_explicit_attach_limits_order_and_project_scope(multi_db):
    user=new_user(multi_db); a=create(multi_db,user);b=create(multi_db,user)
    def attach(i):
        with scoped(multi_db,user) as db:
            return db.execute("SELECT * FROM attach_detail_project_image_by_id(%s,%s,'product')",(a,f'{i}.png')).fetchone()
    with ThreadPoolExecutor(4) as pool: list(pool.map(attach,range(9)))
    with psycopg.connect(multi_db) as db:
        assert db.execute('SELECT sort_order FROM detail_project_images WHERE project_id=%s ORDER BY sort_order',(a,)).fetchall()==[(i,) for i in range(9)]
        assert db.execute('SELECT COUNT(*) FROM detail_project_images WHERE project_id=%s',(b,)).fetchone()[0]==0
    with pytest.raises(psycopg.Error,match='LIMIT'):attach(10)
    with scoped(multi_db,new_user(multi_db)) as db:
        with pytest.raises(psycopg.Error,match='DENIED'):
            db.execute("SELECT * FROM attach_detail_project_image_by_id(%s,'stolen.png','product')",(a,))


def test_multiple_projects_can_start_with_same_owner_and_keep_request_replays(multi_db):
    user,a,run,plan=seed(multi_db,1)
    b=create(multi_db,user); request=str(uuid4())
    with psycopg.connect(multi_db) as db:
        db.execute("UPDATE detail_projects SET content_type='main_image',image_count=1 WHERE id=%s",(b,))
        row=db.execute('SELECT input_snapshot,prompt_versions,model_settings,target_size FROM ecom_image_plans WHERE id=%s',(plan,)).fetchone()
    entries=[dict(zip(['input_snapshot','prompt_versions','model_settings','target_size'],row))|{
        'id':str(uuid4()),'project_id':b,'generation_run_id':request,'user_id':user,'org_id':None,
        'invocation_key':'main_images','input_digest':'b'*64,'image_count':1}]
    with scoped(multi_db,user) as db:
        for _ in range(2):
            assert db.execute('SELECT start_detail_page_run(%s,1,%s,%s)',(b,request,Jsonb(entries))).fetchone()[0]['status']=='analyzing'
    with psycopg.connect(multi_db) as db:
        assert db.execute('SELECT COUNT(*) FROM detail_projects WHERE user_id=%s AND status=\'analyzing\'',(user,)).fetchone()[0]==2
        assert db.execute('SELECT COUNT(*) FROM ecom_image_plans WHERE project_id=%s',(b,)).fetchone()[0]==1


def test_manual_recovery_clock_begins_once_on_first_reservation(multi_db):
    user,project,run,plan=seed(multi_db,1);request=str(uuid4());lease=str(uuid4())
    with psycopg.connect(multi_db) as db:
        db.execute("UPDATE ecom_image_plans SET status='failed' WHERE id=%s",(plan,))
    with scoped(multi_db,user) as db:
        db.execute('SELECT resume_detail_page_plan(%s,%s,%s)',(project,plan,request))
        state=db.execute('SELECT recovery_state FROM ecom_image_plans WHERE id=%s',(plan,)).fetchone()[0]
        assert state['pending_start'] and 'deadline' not in state
        assert db.execute('SELECT claim_ecom_image_plan(%s,NULL,%s,600)',(plan,lease)).fetchone()[0]['claimed']
        receipt=db.execute('SELECT reserve_ecom_plan_attempt(%s,%s,%s,1,1200)',(plan,lease,str(uuid4()))).fetchone()[0]
        deadline=receipt['deadline']
        replay=db.execute('SELECT resume_detail_page_plan(%s,%s,%s)',(project,plan,request)).fetchone()[0]
        assert replay['recovery_state']['deadline']==deadline and 'pending_start' not in replay['recovery_state']


def test_auto_recovery_clock_is_also_pending(multi_db):
    user,project,run,plan=seed(multi_db,1);opt_in(multi_db,plan)
    with psycopg.connect(multi_db) as db: db.execute("UPDATE ecom_image_plans SET status='failed' WHERE id=%s",(plan,))
    token=str(uuid4());job_claim(multi_db,user,plan,'plan','initial:0:1');due(multi_db,plan,'plan');job_claim(multi_db,user,plan,'plan','initial:0:1',token)
    with scoped(multi_db,user) as db:
        db.execute('SELECT resume_detail_delivery_plan(%s,%s)',(plan,token))
        state=db.execute('SELECT recovery_state FROM ecom_image_plans WHERE id=%s',(plan,)).fetchone()[0]
        assert state['pending_start'] and 'deadline' not in state


def test_summary_pagination_scopes_and_stable_creation_order(multi_db,tmp_path,monkeypatch):
    from services import detail_project_service as module
    monkeypatch.setattr(module,'get_settings',lambda:SimpleNamespace(file_workspace_root=str(tmp_path)))
    user=new_user(multi_db); ids=[create(multi_db,user) for _ in range(3)];create(multi_db,new_user(multi_db))
    raw=LocalDBClient(psycopg.conninfo.make_conninfo(multi_db,user='everydayai'),min_size=1,max_size=2)
    try:
        projects=DetailProjectService(raw,user,None); service=DetailProjectTasks(projects)
        first=service.list(limit=2); assert len(first['items'])==2 and first['next_cursor']
        with psycopg.connect(multi_db) as db:db.execute("UPDATE detail_projects SET updated_at=NOW()+interval '1 day' WHERE id=%s",(ids[0],))
        second=service.list(first['next_cursor'],2)
        assert [str(p['id']) for p in first['items']+second['items']]==ids[::-1]
        assert first['items']==service.list(limit=2)['items']
        assert service.status([str(uuid4())])==[]
        assert first['items'][0]['expected_count']==14 and first['items'][0]['display_status']=='draft'
        with pytest.raises(AppException):service.list('not-a-cursor')
        for fields in ([1,str(uuid4())],['2026-10-10',None],{},[1]):
            cursor=base64.urlsafe_b64encode(json.dumps(fields).encode()).decode()
            with pytest.raises(AppException):service.list(cursor)
        # Editing older drafts returns that draft, never the newest one.
        saved=projects.update_settings(ids[0],1,{'requirement':'A6'})
        assert str(saved['id'])==ids[0] and saved['requirement']=='A6'
    finally:raw.pool.close()


def test_waiting_projection_and_guarded_rollback(multi_db):
    user,project,run,plan=seed(multi_db,1)
    assert execution_state({'status':'planning','lease_expires_at':None})=='waiting'
    assert execution_state({'status':'planning','lease_expires_at':datetime.now(timezone.utc)+timedelta(seconds=60)})=='running'
    with psycopg.connect(multi_db) as db:
        funcs=db.execute("SELECT prosecdef,has_function_privilege('image_untrusted',oid,'EXECUTE') FROM pg_proc WHERE proname IN ('create_detail_project','attach_detail_project_image_by_id')").fetchall()
        assert funcs==[(False,False)]*2
        with pytest.raises(psycopg.Error,match='ROLLBACK_UNSAFE'):
            db.execute((ROOT/'migrations/rollback/286_detail_project_tasks_rollback.sql').read_text())


@pytest.mark.parametrize('destination,submitted',[('detail_project',True),('chat',False)])
async def test_capacity_waiting_is_not_page_failure_but_chat_deadline_remains(destination,submitted):
    lifecycle=ChatImageLifecycle(None,SimpleNamespace(chat_image_queue_timeout_seconds=600))
    lifecycle.submit=AsyncMock();lifecycle.definite_failure=AsyncMock()
    task={'created_at':(datetime.now(timezone.utc)-timedelta(hours=2)).isoformat(),
        'request_params':{'_media_lifecycle_v1':{'phase':'queued'},'_media_request_v1':{'origin':{'destination':destination}}}}
    await lifecycle.advance(task)
    assert lifecycle.submit.await_count==int(submitted)
    assert lifecycle.definite_failure.await_count==int(not submitted)


def test_personal_and_org_creation_and_attachment_are_isolated(multi_db,tmp_path,monkeypatch):
    from services import detail_project_service as module
    monkeypatch.setattr(module,'get_settings',lambda:SimpleNamespace(file_workspace_root=str(tmp_path)))
    user=new_user(multi_db);org=str(uuid4());personal=create(multi_db,user)
    with psycopg.connect(multi_db) as db:
        db.execute('INSERT INTO organizations(id) VALUES(%s)',(org,))
        db.execute('INSERT INTO org_members(org_id,user_id) VALUES(%s,%s)',(org,user))
    with scoped(multi_db,user) as db:
        db.execute("SELECT set_config('app.org_id',%s,true)",(org,))
        project=str(db.execute('SELECT create_detail_project(%s)',(str(uuid4()),)).fetchone()[0])
    with scoped(multi_db,user) as db:
        db.execute("SELECT set_config('app.org_id',%s,true)",(org,))
        with pytest.raises(psycopg.Error,match='DENIED'):
            db.execute("SELECT * FROM attach_detail_project_image_by_id(%s,'personal.png','product')",(personal,))
    raw=LocalDBClient(psycopg.conninfo.make_conninfo(multi_db,user='everydayai'),min_size=1,max_size=2)
    try:
        personal_service=DetailProjectTasks(DetailProjectService(raw,user,None));org_service=DetailProjectTasks(DetailProjectService(raw,user,org))
        assert [str(p['id']) for p in personal_service.list()['items']]==[personal]
        assert [str(p['id']) for p in org_service.list()['items']]==[project]
        assert personal_service.status([project])==[] and org_service.status([personal])==[]
    finally:raw.pool.close()


async def test_multi_task_migration_retains_full_headless_worker_three_stage_delivery(multi_db,monkeypatch,tmp_path):
    from tests.test_detail_page_generation_postgres import test_page_worker_resumes_failed_stage_and_accepts_images_with_nine_ordered_originals as scenario
    await scenario(multi_db,monkeypatch,tmp_path,'kimi-k3','detail_page',15,2)


async def test_sidebar_counts_match_latest_retried_images_and_detail_groups(multi_db,monkeypatch,tmp_path):
    from services import detail_project_service as module
    from tests.test_detail_page_delivery_recovery import test_combined_run_only_replays_failed_images_preserves_success_and_final_charge as scenario
    monkeypatch.setattr(module,'get_settings',lambda:SimpleNamespace(file_workspace_root=str(tmp_path)))
    await scenario(multi_db,monkeypatch)
    with psycopg.connect(multi_db) as db:
        user,project=db.execute("SELECT user_id,id FROM detail_projects WHERE content_type='default' AND status='completed' ORDER BY created_at DESC LIMIT 1").fetchone()
    raw=LocalDBClient(psycopg.conninfo.make_conninfo(multi_db,user='everydayai'),min_size=1,max_size=2)
    try:
        projects=DetailProjectService(raw,str(user),None)
        result=DetailProjectTasks(projects).status([str(project)])[0]
        assert result['completed_count']==result['expected_count']==14
        assert result['display_status']=='completed'
        assert 'groups' not in result and 'request_params' not in result
        from services.detail_page_generation import DetailPageGeneration
        detail=DetailPageGeneration(raw,str(user),None).read(str(project))
        assert [group['kind'] for group in detail['groups']]==['main_images','detail_page']
    finally:raw.pool.close()


def test_two_workers_only_one_valid_claim_for_same_plan(multi_db):
    user,project,run,plan=seed(multi_db,1)
    def claim(_):
        with scoped(multi_db,user) as db:
            return db.execute('SELECT claim_ecom_image_plan(%s,NULL,%s,600)',(plan,str(uuid4()))).fetchone()[0]['claimed']
    with ThreadPoolExecutor(4) as pool:assert sum(pool.map(claim,range(4)))==1
