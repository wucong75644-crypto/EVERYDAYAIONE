"""Durable delivery, fencing and billing in an isolated role-accurate PostgreSQL DB."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
import pytest

from core.local_db import LocalDBClient
from services.detail_page_generation import DetailPageGeneration, DetailPageWorker
from services.detail_page_recovery import POLICY, DetailDeliveryRecovery, recovery_projection
from tests.test_chat_image_lifecycle_postgres import isolated_db
from tests.test_ecommerce_recovery_postgres import ecom_db
from tests.test_detail_page_generation_postgres import page_db, seed, scoped, ready_item, snapshot


@pytest.fixture(scope='module')
def delivery_db(page_db):
    with psycopg.connect(page_db) as db:
        db.execute((Path(__file__).parents[1]/'migrations/285_detail_page_delivery_recovery.sql').read_text())
    return page_db


def opt_in(dsn, plan):
    with psycopg.connect(dsn) as db:
        db.execute("UPDATE ecom_image_plans SET model_settings=model_settings||%s,input_snapshot=input_snapshot||jsonb_build_object('task_type',invocation_key) WHERE id=%s",
            (Jsonb({'delivery_policy': POLICY}), plan))


def due(dsn, plan, key, *, expired=False):
    with psycopg.connect(dsn) as db:
        db.execute('UPDATE ecom_image_plans SET delivery_recovery=jsonb_set(delivery_recovery,%s,to_jsonb(NOW()-interval \'1 second\')) WHERE id=%s',
            ([key, 'lease_expires_at' if expired else 'next_retry_at'], plan))


def job_claim(dsn, user, plan, key, source, token=None, blocked=False):
    with scoped(dsn,user) as db:
        return db.execute('SELECT claim_detail_delivery_retry(%s,%s,%s,%s,%s,%s)',
            (plan,key,source,token or str(uuid4()),'TEST_TEMPORARY',blocked)).fetchone()[0]


def settle(dsn, user, task, success=False):
    with scoped(dsn,user,True) as db:
        assert db.execute('SELECT claim_chat_image_submission(%s,%s,60)',(task,str(uuid4()))).fetchone()[0]['outcome']=='claimed'
        db.execute('SELECT record_chat_image_provider_result(%s,%s)',
            (task,Jsonb({'status':'success' if success else 'failed'})))
        return db.execute('SELECT publish_chat_image_result(%s,%s,%s,%s)',
            (task,Jsonb([{'type':'image','url':'result','failed':not success}]),'completed' if success else 'failed','temporary')).fetchone()[0]


@pytest.fixture
def delivery_service(delivery_db):
    user,project,run,plan=seed(delivery_db)
    opt_in(delivery_db,plan)
    raw=LocalDBClient(psycopg.conninfo.make_conninfo(delivery_db,user='everydayai'),min_size=1,max_size=4)
    service=DetailPageGeneration(raw,user,None)
    yield service,user,project,run,plan
    raw.pool.close()


def test_unused_migration_roundtrip_preserves_invoker_permissions(delivery_db):
    migrations=Path(__file__).parents[1]/'migrations'
    with psycopg.connect(delivery_db) as db:
        db.execute((migrations/'rollback/285_detail_page_delivery_recovery_rollback.sql').read_text())
        assert db.execute("SELECT COUNT(*) FROM information_schema.columns WHERE table_name='ecom_image_plans' AND column_name='delivery_recovery'").fetchone()[0]==0
        db.execute((migrations/'285_detail_page_delivery_recovery.sql').read_text())
        functions=db.execute("SELECT prosecdef,has_function_privilege('image_untrusted',oid,'EXECUTE'),has_function_privilege('everydayai',oid,'EXECUTE') FROM pg_proc WHERE proname IN ('lock_detail_delivery_plan','claim_detail_delivery_retry','finish_detail_delivery_retry','resume_detail_delivery_plan','resume_detail_delivery_acceptance','stop_detail_delivery_recovery')").fetchall()
        assert len(functions)==6 and all(row==(False,False,True) for row in functions)


def test_claim_backoff_single_executor_and_crash_reuses_request(delivery_db):
    user,project,run,plan=seed(delivery_db)
    opt_in(delivery_db,plan)
    with psycopg.connect(delivery_db) as db:
        db.execute("UPDATE ecom_image_plans SET status='failed' WHERE id=%s",(plan,))
    source='initial:0:1'
    first=job_claim(delivery_db,user,plan,'plan',source)
    assert first['outcome']=='waiting' and first['attempts']==0
    assert job_claim(delivery_db,user,plan,'plan',source)['outcome']=='waiting'
    due(delivery_db,plan,'plan')
    with ThreadPoolExecutor(4) as pool:
        claims=list(pool.map(lambda _:job_claim(delivery_db,user,plan,'plan',source),range(4)))
    assert sum(j['outcome']=='execute' for j in claims)==1
    winner=next(j for j in claims if j['outcome']=='execute')
    due(delivery_db,plan,'plan',expired=True)
    next_job=job_claim(delivery_db,user,plan,'plan',source)
    assert next_job['request_id']==winner['request_id'] and next_job['attempts']==2
    assert next_job['claim_token']!=winner['claim_token']
    with scoped(delivery_db,user) as db:
        assert db.execute("SELECT finish_detail_delivery_retry(%s,'plan',%s,'submitted','TEST')",(plan,winner['claim_token'])).fetchone()[0] is False
        assert db.execute("SELECT finish_detail_delivery_retry(%s,'plan',%s,'waiting','TEST')",(plan,next_job['claim_token'])).fetchone()[0] is True
    assert job_claim(delivery_db,user,plan,'plan',source)['outcome']=='waiting'


async def test_auto_resume_archives_uncertainty_keeps_outputs_and_drafts(delivery_service,delivery_db):
    service,user,project,run,plan=delivery_service
    prior={'version':1,'window_task_id':run,'counts':{'3':3},'attempts':{'old':{
        'outcome':'uncertain','usage':{'input_tokens':12},'completed_at':'2026-10-10T00:00:00Z'}}}
    outputs={'1':{'facts':'saved'},'2':'saved direction'}
    drafts={'3':{'output':{'images':[{'position':1}]}}}
    with psycopg.connect(delivery_db) as db:
        db.execute("UPDATE ecom_image_plans SET status='failed',current_stage=3,recovery_state=%s,stage_outputs=%s,stage_drafts=%s WHERE id=%s",
            (Jsonb(prior),Jsonb(outputs),Jsonb(drafts),plan))
    def row():return service.db.table('ecom_image_plans').select('*').eq('id',plan).single().execute().data
    project_row=service.projects.get_by_id(project)
    await DetailDeliveryRecovery(service).advance(row(),project_row,[])
    due(delivery_db,plan,'plan')
    await asyncio.gather(*[DetailDeliveryRecovery(service).advance(row(),project_row,[]) for _ in range(2)])
    saved=row()
    assert saved['status']=='planning' and saved['current_stage']==3
    assert saved['stage_outputs']==outputs and saved['stage_drafts']==drafts
    assert saved['recovery_state']['attempts']=={} and saved['recovery_state']['counts']=={}
    archived=saved['recovery_state']['previous_windows'][0]
    assert archived['attempts']==prior['attempts'] and archived['retry_cost']=='platform'
    assert saved['recovery_state']['window_task_id']==saved['delivery_recovery']['plan']['request_id']


@pytest.mark.parametrize('case',['foreign_actor','foreign_org','stale_run','stopped','legacy','live_lease'])
def test_auto_resume_respects_owner_current_run_stop_and_lease(delivery_db,case):
    user,project,run,plan=seed(delivery_db)
    opt_in(delivery_db,plan)
    with psycopg.connect(delivery_db) as db:
        db.execute("UPDATE ecom_image_plans SET status='failed' WHERE id=%s",(plan,))
        if case=='stale_run':db.execute("UPDATE detail_projects SET run_state=jsonb_set(run_state,'{run_id}',%s) WHERE id=%s",(Jsonb(str(uuid4())),project))
        if case=='stopped':db.execute("UPDATE detail_projects SET run_state=run_state||'{\"delivery_stopped\":true}' WHERE id=%s",(project,))
        if case=='legacy':db.execute("UPDATE ecom_image_plans SET model_settings='{}' WHERE id=%s",(plan,))
        if case=='live_lease':db.execute("UPDATE ecom_image_plans SET lease_expires_at=NOW()+interval '1 minute' WHERE id=%s",(plan,))
    with scoped(delivery_db,str(uuid4()) if case=='foreign_actor' else user) as db:
        if case=='foreign_org':db.execute("SELECT set_config('app.org_id',%s,true)",(str(uuid4()),))
        if case=='live_lease':
            assert db.execute("SELECT claim_detail_delivery_retry(%s,'plan','initial:0:1',%s,'TEST')",(plan,str(uuid4()))).fetchone()[0]['outcome']=='stale'
        else:
            with pytest.raises(psycopg.Error,match='SCOPE_DENIED'):
                db.execute("SELECT claim_detail_delivery_retry(%s,'plan','initial:0:1',%s,'TEST')",(plan,str(uuid4())))


@pytest.mark.parametrize('opted_in',[False,True])
def test_billing_failed_attempts_platform_valid_stages_once(delivery_db,opted_in):
    user,project,run,plan=seed(delivery_db)
    if opted_in:opt_in(delivery_db,plan)
    lease=str(uuid4())
    with scoped(delivery_db,user) as db:
        db.execute('SELECT claim_ecom_image_plan(%s,NULL,%s,600)',(plan,lease))
    def finish(stage,valid,credits):
        attempt=str(uuid4())
        with scoped(delivery_db,user) as db:
            db.execute('SELECT reserve_ecom_plan_attempt(%s,%s,%s,%s,600)',(plan,lease,attempt,stage))
            params=(plan,lease,stage,attempt,Jsonb({'input_tokens':100,'user_credits':credits}),
                'completed' if valid else 'validation_failed',Jsonb({'stage':stage}) if valid else None,
                'ready' if stage==3 and valid else 'planning',credits)
            query='SELECT finish_ecom_plan_attempt(%s,%s,%s,%s,%s,%s,%s,%s,NULL,NULL,%s)'
            assert db.execute(query,params).fetchone()[0]['outcome']=='saved'
            assert db.execute(query,params).fetchone()[0]['outcome']=='replay'
    finish(1,False,9)
    finish(1,True,2)
    finish(2,True,3)
    with psycopg.connect(delivery_db) as db:
        assert db.execute('SELECT credits FROM users WHERE id=%s',(user,)).fetchone()[0]==(1000 if opted_in else 986)
    finish(3,True,4)
    with psycopg.connect(delivery_db) as db:
        assert db.execute('SELECT credits FROM users WHERE id=%s',(user,)).fetchone()[0]==(991 if opted_in else 982)
        attempts=db.execute('SELECT stage_attempts FROM ecom_image_plans WHERE id=%s',(plan,)).fetchone()[0]
        if opted_in:
            assert attempts[0]['usage']['billing_party']=='platform'
            assert attempts[1]['usage']['billing_party']=='pending_delivery'
            assert attempts[-1]['usage']['delivered_stage_credits']==9
            assert db.execute('SELECT COUNT(*) FROM credits_history WHERE user_id=%s',(user,)).fetchone()[0]==1


async def test_combined_run_only_replays_failed_images_preserves_success_and_final_charge(delivery_db,monkeypatch):
    from services.handlers.chat_image_controls import ChatImageControls
    user,project,run,plan=seed(delivery_db,combined=True)
    raw=LocalDBClient(psycopg.conninfo.make_conninfo(delivery_db,user='everydayai'),min_size=1,max_size=4)
    service=DetailPageGeneration(raw,user,None)
    recovery=DetailDeliveryRecovery(service)
    monkeypatch.setattr('services.handlers.chat_image_controls.chat_image_acceptance_allowed',lambda *_:True)
    monkeypatch.setattr('services.detail_page_generation.image_resolver',lambda *_:SimpleNamespace(verify=lambda _:None))
    failed=[];initial=[]
    try:
        plans=service.db.table('ecom_image_plans').select('*').eq('project_id',project).execute().data
        for p in plans:
            opt_in(delivery_db,p['id'])
            items=[]
            for position in range(1,8):
                item=str(uuid4());s=snapshot(user,project,run,p['id'],item)
                items.append({'item_id':item,'position':position,'request_text':'exact',
                    'request_text_sha256':s['prompt_sha256'],'aspect_ratio':'1:1','snapshot':s})
            with psycopg.connect(delivery_db) as db:
                db.execute("UPDATE ecom_image_plans SET status='ready',items=%s WHERE id=%s",(Jsonb(items),p['id']))
            for item in items:
                task=service.db.rpc('accept_detail_page_image',{'p_project_id':project,'p_plan_id':p['id'],
                    'p_item_id':item['item_id'],'p_snapshot':item['snapshot'],'p_org_id':None}).execute().data['task_id']
                settle(delivery_db,user,task,success=item['position']!=3)
                initial.append(task)
                if item['position']==3:failed.append((p['id'],item['item_id'],task))
        groups=service.read(project)['groups']
        assert len(groups)==2 and all(g['auto_recovery']['status']=='waiting' for g in groups)
        with pytest.raises(Exception,match='自动恢复'):
            await ChatImageControls(service.db,user,None).replay(failed[0][2],str(uuid4()))
        async def advance():
            for p in service.db.table('ecom_image_plans').select('*').eq('project_id',project).execute().data:
                tasks=service.db.table('tasks').select('*').eq("request_params->'_media_request_v1'->'origin'->'plan_source'->>'plan_id'",p['id']).execute().data
                await recovery.advance(p,service.projects.get_by_id(project),tasks)
        await advance()
        for plan_id,item,_ in failed:due(delivery_db,plan_id,'image:'+item)
        await asyncio.gather(advance(),advance())
        all_tasks=service.db.table('tasks').select('*').eq('user_id',user).execute().data
        assert len(all_tasks)==16
        retried=[t for t in all_tasks if t['id'] not in initial]
        assert len(retried)==2
        for task in retried:
            frozen=task['request_params']['_media_request_v1']
            assert frozen['prompt']=='exact' and frozen['references']==[]
            assert frozen['aspect_ratio']=='1:1' and frozen['resolution']=='1K'
            settle(delivery_db,user,task['id'],success=True)
        plans=service.db.table('ecom_image_plans').select('*').eq('project_id',project).execute().data
        DetailPageWorker.aggregate(service,service.projects.get_by_id(project),plans)
        assert service.read(project)['status']=='completed'
        assert all(g['auto_recovery']['status']=='active' for g in service.read(project)['groups'])
        with psycopg.connect(delivery_db) as db:
            assert db.execute('SELECT credits FROM users WHERE id=%s',(user,)).fetchone()[0]==1000-14*retried[0]['request_params']['_media_request_v1']['estimated_credits']
            assert db.execute("SELECT COUNT(*) FROM tasks WHERE user_id=%s AND status='completed'",(user,)).fetchone()[0]==14
    finally:raw.pool.close()


async def test_replay_receipt_loss_new_coordinator_never_duplicates_and_stop_fences_insert(delivery_db,monkeypatch):
    from services.handlers.chat_image_controls import ChatImageControls
    user,project,run,plan,items=ready_item(delivery_db)
    opt_in(delivery_db,plan)
    raw=LocalDBClient(psycopg.conninfo.make_conninfo(delivery_db,user='everydayai'),min_size=1,max_size=3)
    service=DetailPageGeneration(raw,user,None)
    monkeypatch.setattr('services.handlers.chat_image_controls.chat_image_acceptance_allowed',lambda *_:True)
    monkeypatch.setattr('services.detail_page_generation.image_resolver',lambda *_:SimpleNamespace(verify=lambda _:None))
    try:
        task=service.db.rpc('accept_detail_page_image',{'p_project_id':project,'p_plan_id':plan,'p_item_id':items[0]['item_id'],
            'p_snapshot':items[0]['snapshot'],'p_org_id':None}).execute().data['task_id']
        settle(delivery_db,user,task)
        key='image:'+items[0]['item_id']
        job_claim(delivery_db,user,plan,key,task)
        due(delivery_db,plan,key)
        first=job_claim(delivery_db,user,plan,key,task)
        controls=ChatImageControls(service.db,user,None)
        saved=await controls.replay(task,first['request_id'],delivery_token=first['claim_token'])
        # Worker dies after INSERT but before ack; the receipt is still discoverable after restart/stop.
        service.stop_recovery(project)
        replay=await controls.replay(task,first['request_id'],delivery_token=str(uuid4()))
        assert replay['task_id']==saved['task_id'] and replay['outcome']=='replay'
        settle(delivery_db,user,saved['task_id'],success=True)
        assert service.read(project)['groups'][0]['auto_recovery'] is None
        with pytest.raises(Exception,match='DETAIL_REPLAY_DENIED'):
            await controls.replay(task,str(uuid4()),delivery_token=first['claim_token'])
        assert len(service.db.table('tasks').select('id').eq('user_id',user).execute().data)==2
    finally:raw.pool.close()


async def test_uncertain_image_waits_for_existing_settlement_then_replays(delivery_db,monkeypatch):
    user,project,run,plan,items=ready_item(delivery_db)
    opt_in(delivery_db,plan)
    raw=LocalDBClient(psycopg.conninfo.make_conninfo(delivery_db,user='everydayai'),min_size=1,max_size=3)
    service=DetailPageGeneration(raw,user,None)
    monkeypatch.setattr('services.handlers.chat_image_controls.chat_image_acceptance_allowed',lambda *_:True)
    monkeypatch.setattr('services.detail_page_generation.image_resolver',lambda *_:SimpleNamespace(verify=lambda _:None))
    try:
        task=service.db.rpc('accept_detail_page_image',{'p_project_id':project,'p_plan_id':plan,'p_item_id':items[0]['item_id'],
            'p_snapshot':items[0]['snapshot'],'p_org_id':None}).execute().data['task_id']
        token=str(uuid4())
        with scoped(delivery_db,user,True) as db:
            db.execute('SELECT claim_chat_image_submission(%s,%s,60)',(task,token))
            db.execute('SELECT mark_chat_image_dispatch(%s,%s)',(task,token))
            db.execute('SELECT mark_chat_image_uncertain(%s,%s,60)',(task,token))
        def rows():return service.db.table('tasks').select('*').eq('user_id',user).execute().data
        def plan_row():return service.db.table('ecom_image_plans').select('*').eq('id',plan).single().execute().data
        await DetailDeliveryRecovery(service).advance(plan_row(),service.projects.get_by_id(project),rows())
        assert plan_row()['delivery_recovery']=={} and len(rows())==1
        with psycopg.connect(delivery_db) as db:
            db.execute("UPDATE tasks SET request_params=jsonb_set(request_params,'{_media_lifecycle_v1,uncertain_deadline}',to_jsonb(NOW()-interval '1 second')) WHERE id=%s",(task,))
        with scoped(delivery_db,user,True) as db:
            db.execute("SELECT publish_chat_image_result(%s,'[{\"type\":\"image\",\"failed\":true}]','failed','uncertain',true)",(task,))
        await DetailDeliveryRecovery(service).advance(plan_row(),service.projects.get_by_id(project),rows())
        due(delivery_db,plan,'image:'+items[0]['item_id'])
        await DetailDeliveryRecovery(service).advance(plan_row(),service.projects.get_by_id(project),rows())
        assert len(rows())==2
        with scoped(delivery_db,user,True) as db:
            db.execute('SELECT record_chat_image_provider_result(%s,%s)',(task,Jsonb({'status':'success'})))
            assert db.execute("SELECT publish_chat_image_result(%s,'[{\"type\":\"image\",\"url\":\"late\"}]','completed')",(task,)).fetchone()[0]['outcome']=='replay'
        with psycopg.connect(delivery_db) as db:
            assert db.execute('SELECT credits FROM users WHERE id=%s',(user,)).fetchone()[0]==1000
            assert db.execute('SELECT status FROM tasks WHERE id=%s',(task,)).fetchone()[0]=='failed'
    finally:raw.pool.close()


@pytest.mark.parametrize('status,error,result,blocked',[
    ('failed','供应商暂时不可用',{},False),('failed','当前权限或积分不足，未提交供应商',{},True),
    ('failed','参考原图超过模型允许大小',{},True),('cancelled','',{},True),
    ('failed','blocked',{'fail_code':'SAFETY'},True),
])
def test_hard_blockers_pause_but_transient_failures_are_waiting(status,error,result,blocked):
    task={'id':'task','status':status,'error_message':error,'result':result,'created_at':'now',
        'request_params':{'_media_request_v1':{'origin':{'plan_source':{'item_id':'item'}}}}}
    plan={'status':'ready','model_settings':{'delivery_policy':POLICY}}
    assert recovery_projection(plan,{},[task])['items']['item']['status']==('blocked' if blocked else 'waiting')
    assert recovery_projection({**plan,'model_settings':{}},{},[task]) is None
    assert recovery_projection(plan,{'run_state':{'delivery_stopped':True}},[task]) is None


async def test_acceptance_recovery_keeps_prompt_set_and_definitive_input_block_stays_paused(delivery_service,delivery_db):
    service,user,project,run,plan=delivery_service
    with psycopg.connect(delivery_db) as db:
        db.execute("UPDATE ecom_image_plans SET status='ready',items='[{\"item_id\":\"kept\",\"request_text\":\"exact\"}]',stage_outputs='{\"1\":{\"facts\":\"saved\"}}',recovery_state=%s WHERE id=%s",
            (Jsonb({'acceptance_error':{'code':'DETAIL_GENERATION_FAILED','attempt_id':str(uuid4())}}),plan))
    def row():return service.db.table('ecom_image_plans').select('*').eq('id',plan).single().execute().data
    recovery=DetailDeliveryRecovery(service)
    await recovery.advance(row(),service.projects.get_by_id(project),[])
    due(delivery_db,plan,'acceptance')
    await recovery.advance(row(),service.projects.get_by_id(project),[])
    assert row()['items']==[{'item_id':'kept','request_text':'exact'}]
    assert row()['stage_outputs']=={'1':{'facts':'saved'}}
    assert not row()['recovery_state'].get('acceptance_error')
    with psycopg.connect(delivery_db) as db:
        db.execute("UPDATE ecom_image_plans SET recovery_state=jsonb_set(recovery_state,'{acceptance_error}',%s) WHERE id=%s",
            (Jsonb({'code':'IMAGE_REFERENCE_CHANGED','attempt_id':str(uuid4())}),plan))
    await recovery.advance(row(),service.projects.get_by_id(project),[])
    blocked=row()['delivery_recovery']['acceptance']
    assert blocked['status']=='blocked'
    await recovery.advance(row(),service.projects.get_by_id(project),[])
    assert row()['delivery_recovery']['acceptance']['attempts']==blocked['attempts']
    assert recovery_projection(row(),{},[])['status']=='blocked'


async def test_retry_discovered_input_error_remains_blocked_without_more_attempts(delivery_db,monkeypatch):
    user,project,run,plan,items=ready_item(delivery_db)
    opt_in(delivery_db,plan)
    raw=LocalDBClient(psycopg.conninfo.make_conninfo(delivery_db,user='everydayai'),min_size=1,max_size=3)
    service=DetailPageGeneration(raw,user,None)
    monkeypatch.setattr('services.handlers.chat_image_controls.chat_image_acceptance_allowed',lambda *_:True)
    def changed(_):raise ValueError('IMAGE_REFERENCE_CHANGED')
    monkeypatch.setattr('services.detail_page_generation.image_resolver',lambda *_:SimpleNamespace(verify=changed))
    try:
        task=service.db.rpc('accept_detail_page_image',{'p_project_id':project,'p_plan_id':plan,'p_item_id':items[0]['item_id'],
            'p_snapshot':items[0]['snapshot'],'p_org_id':None}).execute().data['task_id']
        settle(delivery_db,user,task)
        async def advance():
            row=service.db.table('ecom_image_plans').select('*').eq('id',plan).single().execute().data
            tasks=service.db.table('tasks').select('*').eq('user_id',user).execute().data
            await DetailDeliveryRecovery(service).advance(row,service.projects.get_by_id(project),tasks)
        await advance();due(delivery_db,plan,'image:'+items[0]['item_id'])
        await advance();await advance()
        group=service.read(project)['groups'][0]
        assert group['auto_recovery']['status']=='blocked'
        state=group['auto_recovery']['items'][items[0]['item_id']]
        assert state['attempts']==1 and state['message']=='原始图片已变化，请重新上传后开始。'
        assert len(group['tasks'])==1
    finally:raw.pool.close()


async def test_bounded_scanner_rotates_delayed_projects_without_starvation(delivery_db):
    projects=[]
    for _ in range(21):
        user,project,run,plan=seed(delivery_db)
        opt_in(delivery_db,plan);projects.append((project,plan))
        with psycopg.connect(delivery_db) as db:
            db.execute("UPDATE detail_projects SET updated_at='2000-01-01' WHERE id=%s",(project,))
            db.execute("UPDATE ecom_image_plans SET status='failed' WHERE id=%s",(plan,))
    raw=LocalDBClient(psycopg.conninfo.make_conninfo(delivery_db,user='everydayai'),min_size=1,max_size=4)
    worker=DetailPageWorker(raw)
    try:
        await worker.scan();await worker.scan()
        with psycopg.connect(delivery_db) as db:
            for project,plan in projects:
                assert db.execute("SELECT delivery_recovery ? 'plan' FROM ecom_image_plans WHERE id=%s",(plan,)).fetchone()[0] is True
                assert db.execute("SELECT status FROM detail_projects WHERE id=%s",(project,)).fetchone()[0]=='analyzing'
    finally:
        await worker.close();raw.pool.close()


@pytest.mark.parametrize('key',['plan','acceptance'])
def test_stop_fences_claimed_planning_and_acceptance_retry(delivery_db,key):
    user,project,run,plan=seed(delivery_db)
    opt_in(delivery_db,plan)
    source='initial:0:1' if key=='plan' else str(uuid4())
    with psycopg.connect(delivery_db) as db:
        db.execute('UPDATE ecom_image_plans SET status=%s,recovery_state=%s WHERE id=%s',
            ('failed' if key=='plan' else 'ready',Jsonb({} if key=='plan' else {'acceptance_error':{'code':'TEMPORARY','attempt_id':source}}),plan))
    job_claim(delivery_db,user,plan,key,source);due(delivery_db,plan,key)
    job=job_claim(delivery_db,user,plan,key,source)
    with scoped(delivery_db,user) as db:
        db.execute('SELECT stop_detail_delivery_recovery(%s)',(project,))
    function='resume_detail_delivery_plan' if key=='plan' else 'resume_detail_delivery_acceptance'
    with scoped(delivery_db,user) as db:
        with pytest.raises(psycopg.Error,match='DETAIL_SCOPE_DENIED'):
            db.execute(f'SELECT {function}(%s,%s)',(plan,job['claim_token']))


def test_used_migration_cannot_drop_delivery_or_billing_history(delivery_db):
    user,project,run,plan=seed(delivery_db);opt_in(delivery_db,plan)
    with psycopg.connect(delivery_db) as db:
        with pytest.raises(psycopg.Error,match='ROLLBACK_REQUIRES_COMPATIBLE_APPLICATION'):
            db.execute((Path(__file__).parents[1]/'migrations/rollback/285_detail_page_delivery_recovery_rollback.sql').read_text())


def test_new_migration_preserves_chat_acceptance_and_legacy_charges(delivery_db):
    from tests.test_detail_page_generation_postgres import test_page_migration_preserves_real_chat_accept_claim_publish_and_required_conversation
    from tests.test_ecommerce_recovery_postgres import test_duplicate_finish_commits_exactly_one_charge_and_conflict_is_rejected
    test_page_migration_preserves_real_chat_accept_claim_publish_and_required_conversation(delivery_db)
    test_duplicate_finish_commits_exactly_one_charge_and_conflict_is_rejected(delivery_db)


@pytest.mark.parametrize('kind,failed_stage,interruption',[
    ('main_images',2,'timeout'),('detail_page',3,'timeout'),
    ('main_images',3,'cancel'),('detail_page',2,'cancel'),
])
async def test_real_three_stage_planner_recovers_interruption_without_rewriting_saved_stages(
        delivery_service,delivery_db,monkeypatch,kind,failed_stage,interruption):
    import json
    from services.adapters.base import StreamChunk
    from services.agent.image.ecommerce_planner.inputs import source_bindings
    from services.agent.image.ecommerce_planner.prompt_resources import resources
    from services.agent.image.ecommerce_planner.service import EcommerceImagePlanner,PlannerStreamBudget
    from services.detail_page_generation import page_owner
    from services.model_gateway import ModelGatewayTimeoutError
    from tests.ecommerce_design_fixtures import design_fixture
    service,user,project,run,plan=delivery_service
    draft,evidence,refs=design_fixture(15,kind,3)
    snap=evidence['input_snapshot'];snap['resolved_references']=refs
    snap['source_bindings']=source_bindings(snap)
    with psycopg.connect(delivery_db) as db:
        db.execute('UPDATE ecom_image_plans SET image_count=15,input_snapshot=%s,target_size=%s WHERE id=%s',
            (Jsonb(snap),Jsonb(snap['target_size']),plan))
    profile=SimpleNamespace(ecom_image_planning_model='kimi-k3',ecom_image_planning_reasoning='medium',
        ecom_image_planning_stage_timeout=300,ecom_image_planning_input_credits_per_million=1,
        ecom_image_planning_output_credits_per_million=1,wall_seconds=1200)
    owner=page_owner(service.db,user,None,project);owner.task_id=plan
    seen=[];fail=True
    def open_chat(request):
        async def stream(sent,**kwargs):
            stage=json.loads(sent[1]['content'][0]['text'])['stage'];seen.append(stage)
            if fail and stage==failed_stage:
                if interruption=='cancel':raise asyncio.CancelledError()
                raise ModelGatewayTimeoutError('kimi-k3',300,'stream')
            reply={1:evidence['product_selling_points'],2:evidence['visual_direction'],3:draft}[stage]
            yield StreamChunk(content=reply if isinstance(reply,str) else json.dumps(reply),prompt_tokens=100,completion_tokens=10)
        return SimpleNamespace(stream_chat=stream,last_result=SimpleNamespace(status='completed',usage={}),close=AsyncMock())
    monkeypatch.setattr('services.agent.image.ecommerce_planner.service.get_model_gateway',lambda:SimpleNamespace(open_chat=open_chat))
    def row():return service.db.table('ecom_image_plans').select('*').eq('id',plan).single().execute().data
    async def execute():
        owner.execution_budget=PlannerStreamBudget(None,1205);owner.cancellation_event=asyncio.Event()
        bodies,schema=resources(kind)
        return await EcommerceImagePlanner(owner,execution_profile=profile).execute(row(),refs,
            ['https://example.invalid/reference']*3,snap['messages'],schema,bodies)
    if interruption=='cancel':
        with pytest.raises(asyncio.CancelledError):await execute()
    else:assert (await execute()).status=='error'
    first=row()
    assert first['status']=='failed' and set(first['stage_outputs'])=={str(i) for i in range(1,failed_stage)}
    with psycopg.connect(delivery_db) as db:
        assert db.execute('SELECT credits FROM users WHERE id=%s',(user,)).fetchone()[0]==1000
    await DetailDeliveryRecovery(service).advance(first,service.projects.get_by_id(project),[])
    due(delivery_db,plan,'plan')
    await DetailDeliveryRecovery(service).advance(row(),service.projects.get_by_id(project),[])
    fail=False
    assert (await execute()).status=='success'
    final=row()
    assert len(final['items'])==15 and final['status']=='ready'
    assert seen==list(range(1,failed_stage+1))+list(range(failed_stage,4))
    for stage,output in first['stage_outputs'].items():assert final['stage_outputs'][stage]==output
    with psycopg.connect(delivery_db) as db:
        assert db.execute('SELECT credits FROM users WHERE id=%s',(user,)).fetchone()[0]==997
