"""Role-accurate positional receipts, budgets, restart, cost and old-plan isolation."""
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
import pytest
import asyncio
from types import SimpleNamespace

from tests.test_chat_image_lifecycle_postgres import isolated_db
from tests.test_ecommerce_recovery_postgres import ecom_db
from tests.test_detail_page_generation_postgres import page_db, seed, scoped
from tests.test_detail_page_delivery_recovery import delivery_db, opt_in
from tests.test_ecommerce_format_recovery_postgres import format_db, complete

ROOT = Path(__file__).parents[1] / 'migrations'


@pytest.fixture(scope='module')
def positions_db(format_db):
    with psycopg.connect(format_db) as db:
        db.execute((ROOT / '375_ecommerce_position_delivery.sql').read_text())
    return format_db


def start(dsn, count=6, version='ecom-prompts.v5'):
    user, project, run, plan = seed(dsn, count)
    opt_in(dsn, plan)
    lease = str(uuid4())
    draft = {'format_version':'ecom-format.v1', 'delivery_version':version,
        'current_position':1, 'positions': {str(i):{} for i in range(1,count+1)}}
    with psycopg.connect(dsn) as db:
        db.execute('UPDATE ecom_image_plans SET current_stage=3, prompt_versions=%s,stage_drafts=%s WHERE id=%s',
            (Jsonb({'stage_three_delivery_version':version}),Jsonb({'3':draft}),plan))
    with scoped(dsn,user) as db:
        assert db.execute('SELECT claim_ecom_image_plan(%s,NULL,%s,600)',(plan,lease)).fetchone()[0]['claimed']
    return user,project,plan,lease,draft


def save_position(db, plan, lease, before, attempt, credits=3):
    pos = before['current_position']
    draft = deepcopy(before)
    draft['positions'][str(pos)] = {'prompt':f'image_1，位置{pos}完整正文', 'attempt_id':attempt}
    usage={'position':pos,'user_credits':credits,'attempt_id':attempt}
    args=(plan,lease,attempt,Jsonb(usage),Jsonb(before),Jsonb(draft))
    result=db.execute('SELECT finish_ecom_position_attempt(%s,%s,%s,%s,%s,%s)',args).fetchone()[0]
    return draft,args,result


def reserve(db,plan,lease,stage=3):
    attempt=str(uuid4())
    result=db.execute('SELECT reserve_ecom_plan_attempt(%s,%s,%s,%s,600)',(plan,lease,attempt,stage)).fetchone()[0]
    return attempt,result


def test_six_separate_positions_complete_once_bill_once_and_exact_replay(positions_db):
    user,_,plan,lease,draft=start(positions_db)
    for pos in range(1,7):
        with scoped(positions_db,user) as db:
            after=deepcopy(draft);after['current_position']=pos
            db.execute('SELECT persist_ecom_plan_format_state(%s,%s,3,%s,%s)',
                (plan,lease,Jsonb(draft),Jsonb(after)))
            draft=after
            attempt,reservation=reserve(db,plan,lease)
            assert reservation['ordinal']==1
            draft,args,result=save_position(db,plan,lease,draft,attempt)
            assert result['outcome']=='saved'
            assert db.execute('SELECT finish_ecom_position_attempt(%s,%s,%s,%s,%s,%s)',args).fetchone()[0]['outcome']=='replay'
    with scoped(positions_db,user) as db:
        assert db.execute('SELECT credits FROM users WHERE id=%s',(user,)).fetchone()[0]==1000
        output={'status':'ready','images':[{'item_id':str(uuid4())} for _ in range(6)],'review_records':[]}
        assert complete(db,plan,lease,draft,output=output)['outcome']=='saved'
        assert complete(db,plan,lease,draft,output=output)['outcome']=='replay'
        assert db.execute('SELECT credits FROM users WHERE id=%s',(user,)).fetchone()[0]==982
        assert db.execute('SELECT count(*) FROM credits_history WHERE user_id=%s',(user,)).fetchone()[0]==1
        row=db.execute('SELECT stage_attempts,stage_drafts FROM ecom_image_plans WHERE id=%s',(plan,)).fetchone()
        assert len(row[0])==6
        assert row[1]['3']['local_completion']['provider_calls']==0


def test_concurrent_reservations_are_fenced_by_position(positions_db):
    user, _, plan, lease, _ = start(positions_db, 2)
    with scoped(positions_db, user) as db:
        for position in (2, 1):
            result = db.execute('SELECT reserve_ecom_plan_attempt(%s,%s,%s,3,600,%s)',
                (plan, lease, str(uuid4()), position)).fetchone()[0]
            assert result['ordinal'] == 1
        with pytest.raises(psycopg.Error, match='EXECUTION_UNCERTAIN'):
            db.execute('SELECT reserve_ecom_plan_attempt(%s,%s,%s,3,600,2)', (plan, lease, str(uuid4())))


def test_review_reservation_requires_complete_drafts_and_a_signal(positions_db):
    user, _, plan, lease, draft = start(positions_db, 2)
    def reserve_review(db, attempt):
        return db.execute('SELECT reserve_ecom_plan_attempt(%s,%s,%s,3,600,0)',
            (plan, lease, attempt)).fetchone()[0]
    with scoped(positions_db, user) as db:
        with pytest.raises(psycopg.Error, match='POSITION_INVALID'):
            reserve_review(db, str(uuid4()))
    with scoped(positions_db, user) as db:
        attempt, _ = reserve(db, plan, lease)
        draft, _, _ = save_position(db, plan, lease, draft, attempt)
        after = deepcopy(draft)
        after['current_position'] = 2
        after['positions']['1']['review_signal'] = {'positions': [1, 2], 'reason': '具体冲突'}
        db.execute('SELECT persist_ecom_plan_format_state(%s,%s,3,%s,%s)', (plan, lease, Jsonb(draft), Jsonb(after)))
        attempt, _ = reserve(db, plan, lease)
        draft, _, _ = save_position(db, plan, lease, after, attempt)
        review = str(uuid4())
        assert reserve_review(db, review)['ordinal'] == 1
        after = deepcopy(draft)
        after['positions']['1'].pop('review_signal')
        db.execute('SELECT finish_ecom_plan_draft_attempt(%s,%s,3,%s,%s,%s,10)',
            (plan, lease, review, Jsonb({'position': 0, 'user_credits': 10}), Jsonb(after)))
        output = {'status': 'ready', 'images': [{'item_id': str(uuid4())} for _ in range(2)], 'review_records': []}
        assert complete(db, plan, lease, after, output=output)['outcome'] == 'saved'
        assert db.execute('SELECT credits FROM users WHERE id=%s', (user,)).fetchone()[0] == 994


def test_three_attempt_budget_per_position_and_legacy_budget_is_unchanged(positions_db):
    for version in ['ecom-prompts.v5','ecom-prompts.v4']:
        user,_,plan,lease,draft=start(positions_db,2,version)
        for _ in range(3):
            with scoped(positions_db,user) as db:
                attempt,_=reserve(db,plan,lease)
                db.execute("SELECT finish_ecom_plan_attempt(%s,%s,3,%s,'{}','rejected')",(plan,lease,attempt))
        with scoped(positions_db,user) as db:
            with pytest.raises(psycopg.Error,match='RETRY_EXHAUSTED'):
                reserve(db,plan,lease)
        draft['current_position']=2
        with psycopg.connect(positions_db) as db:
            db.execute('UPDATE ecom_image_plans SET stage_drafts=%s WHERE id=%s',(Jsonb({'3':draft}),plan))
        with scoped(positions_db,user) as db:
            if version.endswith('v5'):
                assert reserve(db,plan,lease)[1]['ordinal']==1
            else:
                with pytest.raises(psycopg.Error,match='RETRY_EXHAUSTED'):reserve(db,plan,lease)


def test_repaired_position_charges_only_final_accepted_draft(positions_db):
    user,_,plan,lease,draft=start(positions_db,2)
    with scoped(positions_db,user) as db:
        attempt,_=reserve(db,plan,lease)
        draft,_,_=save_position(db,plan,lease,draft,attempt,3)
        pending=deepcopy(draft)
        pending['positions']['1']={'superseded':draft['positions']['1'],'error':'明确设计错误'}
        db.execute('SELECT persist_ecom_plan_format_state(%s,%s,3,%s,%s)',(plan,lease,Jsonb(draft),Jsonb(pending)))
        draft=pending
        attempt,_=reserve(db,plan,lease)
        draft,_,_=save_position(db,plan,lease,draft,attempt,5)
        pending=deepcopy(draft);pending['current_position']=2
        db.execute('SELECT persist_ecom_plan_format_state(%s,%s,3,%s,%s)',(plan,lease,Jsonb(draft),Jsonb(pending)))
        draft=pending
        attempt,_=reserve(db,plan,lease)
        draft,_,_=save_position(db,plan,lease,draft,attempt,7)
        output={'status':'ready','images':[{'item_id':str(uuid4())} for _ in range(2)],'review_records':[]}
        assert complete(db,plan,lease,draft,output=output)['outcome']=='saved'
        assert db.execute('SELECT credits FROM users WHERE id=%s',(user,)).fetchone()[0]==988


@pytest.mark.parametrize('fault',['lease','position','other_position','draft','actor'])
def test_position_commit_rejects_stale_or_cross_position_mutation(positions_db,fault):
    user,_,plan,lease,draft=start(positions_db,2)
    with scoped(positions_db,user) as db:
        attempt,_=reserve(db,plan,lease)
    before=deepcopy(draft);after=deepcopy(draft)
    after['positions']['1']={'prompt':'image_1完整正文'}
    usage={'position':1,'user_credits':3}
    if fault=='lease':lease=str(uuid4())
    if fault=='position':usage['position']=2
    if fault=='other_position':after['positions']['2']={'prompt':'恶意改写有效稿'}
    if fault=='draft':before['extra']='stale'
    if fault=='actor':user=str(uuid4())
    with scoped(positions_db,user) as db:
        with pytest.raises(psycopg.Error):
            db.execute('SELECT finish_ecom_position_attempt(%s,%s,%s,%s,%s,%s)',
                (plan,lease,attempt,Jsonb(usage),Jsonb(before),Jsonb(after)))


def test_migration_rollback_reapply_retains_drafts_and_invoker_permissions(positions_db):
    _,_,plan,_,draft=start(positions_db,1)
    with psycopg.connect(positions_db) as db:
        db.execute((ROOT/'rollback/375_ecommerce_position_delivery_rollback.sql').read_text())
        db.execute((ROOT/'375_ecommerce_position_delivery.sql').read_text())
        assert db.execute('SELECT stage_drafts FROM ecom_image_plans WHERE id=%s',(plan,)).fetchone()[0]['3']==draft
        assert db.execute("SELECT prosecdef,pg_get_userbyid(proowner),has_function_privilege('image_untrusted',oid,'EXECUTE') FROM pg_proc WHERE oid='finish_ecom_position_attempt(uuid,uuid,uuid,jsonb,jsonb,jsonb)'::regprocedure").fetchone()==(False,'everydayai_owner',False)


async def test_actual_executor_resumes_only_failed_position_and_settles_final_set(positions_db):
    from core.local_db import LocalDBClient
    from services.detail_page_generation import page_owner
    from services.agent.image.ecommerce_planner.service import EcommerceImagePlanner
    from services.agent.image.ecommerce_planner.prompt_resources import plan_resources, resource_versions
    from services.agent.image.ecommerce_planner.position_delivery import POSITION_VERSION
    from tests.test_ecommerce_position_delivery import setup
    _,evidence,_,_,_=setup(3)
    user,project,plan,_,_=start(positions_db,3)
    with psycopg.connect(positions_db) as db:
        db.execute("UPDATE ecom_image_plans SET current_stage=1,lease_token=NULL,lease_expires_at=NULL,stage_drafts='{}',input_snapshot=%s,prompt_versions=%s WHERE id=%s",
            (Jsonb(evidence['input_snapshot']),Jsonb(resource_versions(POSITION_VERSION)),plan))
    raw=LocalDBClient(psycopg.conninfo.make_conninfo(positions_db,user='everydayai'),min_size=1,max_size=3)
    owner=page_owner(raw,user,None,project);owner.task_id=plan
    owner.execution_budget=None;owner.cancellation_event=asyncio.Event()
    service=EcommerceImagePlanner(owner,execution_profile=SimpleNamespace())
    calls=[];reject=True
    async def provider(row,lease,stage,prompt,messages,*,position=None):
        nonlocal reject
        import json
        calls.append((stage,position))
        aid=str(uuid4());await service._reserve(row,lease,stage,aid,600,position=position)
        if stage==3 and position==2 and reject:
            reject=False
            await service._finish(row,lease,stage,aid,{},'rejected')
            raise ConnectionError('definite local failure before response')
        value=(evidence['product_selling_points'] if stage==1 else evidence['visual_direction'] if stage==2
            else {'prompt': f'image_1第{position}张完整设计正文，保持真实比例和原印刷。', 'copy': []})
        return value if isinstance(value,str) else json.dumps(value,ensure_ascii=False), {'attempt_id':aid,'user_credits':1}
    service._call=provider
    try:
        row=service.scope.table('ecom_image_plans').select('*').eq('id',plan).single().execute().data
        bodies,schema=plan_resources(row)
        result=await service.execute(row,evidence['input_snapshot']['references'],['test']*3,
            evidence['input_snapshot']['messages'],schema,bodies)
        assert result.status=='error'
        saved=service.scope.table('ecom_image_plans').select('*').eq('id',plan).single().execute().data
        assert saved['status']=='failed' and saved['current_stage']==3
        first=deepcopy(saved['stage_drafts']['3']['positions']['1'])
        assert first.get('prompt') and not saved['stage_drafts']['3']['positions']['2'], (
            result.error_message, saved['recovery_state'], saved['stage_drafts']['3'])
        assert saved['stage_drafts']['3']['positions']['3'].get('prompt')
        with scoped(positions_db,user) as db:
            db.execute('SELECT resume_detail_page_plan(%s,%s,%s)',(project,plan,str(uuid4())))
        row=service.scope.table('ecom_image_plans').select('*').eq('id',plan).single().execute().data
        result=await service.execute(row,evidence['input_snapshot']['references'],['test']*3,
            evidence['input_snapshot']['messages'],schema,bodies)
        assert result.status=='success'
        saved=service.scope.table('ecom_image_plans').select('*').eq('id',plan).single().execute().data
        assert saved['status']=='ready' and len(saved['items'])==3
        assert saved['stage_drafts']['3']['positions']['1']==first
        assert calls==[(1,None),(2,None),(3,1),(3,2),(3,3),(3,2)]
        with scoped(positions_db,user) as db:
            assert db.execute('SELECT credits FROM users WHERE id=%s',(user,)).fetchone()[0]==995
    finally:raw.pool.close()
