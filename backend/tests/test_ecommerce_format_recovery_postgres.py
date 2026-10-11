"""Actual SQL fencing, restart recovery, settlement and Image acceptance for v4."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
import pytest

from core.local_db import LocalDBClient
from services.detail_page_generation import page_owner
from services.detail_page_recovery import POLICY
from services.agent.image.ecommerce_planner.service import EcommerceImagePlanner
from services.agent.image.ecommerce_planner.prompt_resources import resources,resource_versions
from services.agent.image.ecommerce_planner.format_delivery import PROMPTS_VERSION
from services.handlers.chat_image_request import freeze_image_request
from tests.test_chat_image_lifecycle_postgres import isolated_db
from tests.test_ecommerce_recovery_postgres import ecom_db
from tests.test_detail_page_generation_postgres import page_db,seed,scoped
from tests.test_detail_page_delivery_recovery import delivery_db,opt_in
from tests.test_ecommerce_minimal_delivery import fixture

ROOT=Path(__file__).parents[1]
@pytest.fixture(scope='module')
def format_db(delivery_db):
    with psycopg.connect(delivery_db) as db:
        db.execute((ROOT/'migrations/287_ecommerce_format_recovery.sql').read_text())
    return delivery_db


def seed_draft(dsn,stage=3):
    user,project,run,plan=seed(dsn,1);lease=str(uuid4());opt_in(dsn,plan)
    draft={'output':'saved raw','format_version':'ecom-format.v1','format_attempted':False}
    with psycopg.connect(dsn) as db:
        db.execute('UPDATE ecom_image_plans SET current_stage=%s,stage_drafts=%s WHERE id=%s',
            (stage,Jsonb({str(stage):draft}),plan))
    with scoped(dsn,user) as db:
        assert db.execute('SELECT claim_ecom_image_plan(%s,NULL,%s,600)',(plan,lease)).fetchone()[0]['claimed']
    return user,project,run,plan,lease,draft


def complete(db,plan,lease,draft,stage=3,output=None,status=None):
    output=output or ({'status':'ready','images':[{'item_id':'program-id'}],'review_records':[]} if stage==3 else {'facts':'validated'})
    return db.execute('SELECT complete_ecom_plan_local_draft(%s,%s,%s,%s,%s,%s,%s,%s,%s)',
        (plan,lease,stage,Jsonb(draft),'ecom-format.v1',Jsonb(output),status or ('ready' if stage==3 else 'planning'),
        Jsonb(output['images']) if stage==3 and 'images' in output else None,
        Jsonb(output.get('review_records',[])) if stage==3 and 'images' in output else None)).fetchone()[0]


def test_format_attempt_state_compare_and_swap_has_one_winner(format_db):
    user,_,_,plan,lease,draft=seed_draft(format_db)
    changed={**draft,'format_attempted':True}
    def save(_):
        try:
            with scoped(format_db,user) as db:
                return db.execute('SELECT persist_ecom_plan_format_state(%s,%s,3,%s,%s)',
                    (plan,lease,Jsonb(draft),Jsonb(changed))).fetchone()[0]['outcome']
        except psycopg.Error as e:return str(e).splitlines()[0]
    with ThreadPoolExecutor(4) as pool:results=list(pool.map(save,range(4)))
    assert results.count('saved')==1
    assert all(r=='saved' or 'DRAFT_CONFLICT' in r for r in results)


@pytest.mark.parametrize('stage',[1,2,3])
def test_local_completion_does_not_forge_provider_attempt_or_charge_twice(format_db,stage):
    user,_,_,plan,lease,draft=seed_draft(format_db,stage)
    with psycopg.connect(format_db) as db:
        attempts=[{'stage':i,'status':'completed','usage':{'user_credits':i+2}} for i in range(1,stage)]
        db.execute('UPDATE ecom_image_plans SET stage_attempts=%s WHERE id=%s',(Jsonb(attempts),plan))
    with scoped(format_db,user) as db:
        assert complete(db,plan,lease,draft,stage)['outcome']=='saved'
        assert complete(db,plan,lease,draft,stage)['outcome']=='replay'
    with psycopg.connect(format_db) as db:
        row=db.execute('SELECT stage_attempts,stage_drafts,status,current_stage FROM ecom_image_plans WHERE id=%s',(plan,)).fetchone()
        assert row[0]==attempts
        assert row[1][str(stage)]['local_completion']['provider_calls']==0
        assert row[2]==('ready' if stage==3 else 'planning')
        charged=sum(i+2 for i in range(1,stage)) if stage==3 else 0
        assert db.execute('SELECT credits FROM users WHERE id=%s',(user,)).fetchone()[0]==1000-charged
        assert db.execute('SELECT count(*) FROM credits_history WHERE user_id=%s',(user,)).fetchone()[0]==(1 if charged else 0)


@pytest.mark.parametrize('stage',[1,2,3])
def test_local_question_output_is_saved_at_actual_stage(format_db,stage):
    user,_,_,plan,lease,draft=seed_draft(format_db,stage)
    with scoped(format_db,user) as db:
        assert complete(db,plan,lease,draft,stage,{'status':'needs_input','questions':['明确商品身份？']},'needs_input')['outcome']=='saved'
        assert db.execute('SELECT current_stage,status FROM ecom_image_plans WHERE id=%s',(plan,)).fetchone()==(stage,'needs_input')


@pytest.mark.parametrize('fault',['actor','lease','draft','run','uncertain','started','validator','stage'])
def test_local_completion_rejects_untrusted_stale_or_unknown_execution(format_db,fault):
    user,project,_,plan,lease,draft=seed_draft(format_db)
    if fault in {'uncertain','started','run'}:
        with psycopg.connect(format_db) as db:
            if fault=='run':db.execute('UPDATE detail_projects SET run_state=run_state||%s WHERE id=%s',(Jsonb({'run_id':str(uuid4())}),project))
            else:db.execute('UPDATE ecom_image_plans SET recovery_state=%s WHERE id=%s',
                (Jsonb({'attempts':{'old':{'plan_id':plan,'outcome':fault}}}),plan))
    if fault=='lease':lease=str(uuid4())
    if fault=='draft':draft={**draft,'output':'tampered'}
    with scoped(format_db,str(uuid4()) if fault=='actor' else user) as db:
        with pytest.raises(psycopg.Error):
            if fault=='validator':
                db.execute("SELECT complete_ecom_plan_local_draft(%s,%s,3,%s,'unknown','{}','ready','[]','[]')",(plan,lease,Jsonb(draft)))
            else:complete(db,plan,lease,draft,2 if fault=='stage' else 3)


@pytest.mark.parametrize('kind,count',[('main_images',7),('detail_page',7),('main_images',15),('detail_page',15)])
async def test_v4_pipeline_repairs_only_missing_item_and_accepts_exact_image_requests(format_db,kind,count):
    user,project,run,plan=seed(format_db,count);opt_in(format_db,plan)
    _,evidence,bodies,prompts,_=fixture(count,kind)
    snapshot=evidence['input_snapshot']
    # Acceptance uses the same complete immutable resolved reference list.
    snapshot['resolved_references']=[{**ref,'workspace_path':f'product/{i}.png',
        'content_sha256':str(i+1)*64,'file_version':[1,2,3,4]} for i,ref in enumerate(snapshot['references'])]
    with psycopg.connect(format_db) as db:
        db.execute('UPDATE ecom_image_plans SET invocation_key=%s,input_snapshot=%s,target_size=%s,prompt_versions=%s,model_settings=%s WHERE id=%s',
            (kind,Jsonb(snapshot),Jsonb(snapshot['target_size']),Jsonb(resource_versions(PROMPTS_VERSION)),
             Jsonb({'model':'kimi-k3','delivery_policy':POLICY,'image_budget':{'max_requests':15,'max_credits':300}}),plan))
    raw=LocalDBClient(psycopg.conninfo.make_conninfo(format_db,user='everydayai'),min_size=1,max_size=3)
    owner=page_owner(raw,user,None,project);owner.task_id=plan
    owner.cancellation_event=asyncio.Event();owner.execution_budget=None
    planner=EcommerceImagePlanner(owner,execution_profile=SimpleNamespace())
    calls=[]
    async def model_call(row,lease,stage,prompt,messages):
        reservation=await planner._reserve(row,lease,stage,attempt:=str(uuid4()),600)
        calls.append((stage,messages))
        if stage==1:
            output=deepcopy(evidence['product_selling_points']);output.pop('schema_version')
            output['facts'][0]['nonbusiness_extra']=None
        elif stage==2:output=evidence['visual_direction'].replace('## 8.','### 8、')
        elif reservation['ordinal']==1:output={'prompts':prompts['prompts'][:-1]}
        else:output={'prompts':[prompts['prompts'][-1]]}
        return output if isinstance(output,str) else json.dumps(output,ensure_ascii=False),{
            'attempt_id':attempt,'user_credits':1,'input_tokens':10,'output_tokens':20}
    planner._call=model_call
    try:
        row=planner.scope.table('ecom_image_plans').select('*').eq('id',plan).single().execute().data
        result=await planner.execute(row,snapshot['resolved_references'],['https://example.invalid/image']*3,
            snapshot['messages'],resources(kind,PROMPTS_VERSION)[1],bodies)
        assert result.status=='success',result.summary
        assert [stage for stage,_ in calls]==[1,2,3,3]
        body=json.loads(calls[-1][1][1]['content'][0]['text'])
        assert body['targets_in_order']==[['prompts',count-1]]
        saved=planner.scope.table('ecom_image_plans').select('*').eq('id',plan).single().execute().data
        assert len(saved['items'])==count and saved['status']=='ready'
        for i,item in enumerate(saved['items']):
            assert item['design']['prompt']==prompts['prompts'][i]
            origin={'destination':'detail_project','project_id':project,'generation_run_id':run,'actor_user_id':user,'org_id':None,
                'workspace_owner_id':user,'context_scope':'user','plan_source':{'plan_id':plan,'revision':1,
                'item_id':item['item_id'],'request_text_sha256':item['request_text_sha256']}}
            image=freeze_image_request({'mode':'image_to_image','prompt':item['request_text'],'aspect_ratio':'3:4','resolution':'2K'},
                snapshot['resolved_references'],origin=origin,max_requests=15,max_credits=300)
            accepted=planner.scope.rpc('accept_detail_page_image',{'p_project_id':project,'p_plan_id':plan,
                'p_item_id':item['item_id'],'p_snapshot':image,'p_org_id':None}).execute().data
            actual=planner.scope.table('tasks').select('request_params').eq('id',accepted['task_id']).single().execute().data['request_params']['_media_request_v1']
            assert actual['prompt']==item['request_text'] and actual['references']==snapshot['resolved_references']
            assert actual['resolution']=='2K' and actual['aspect_ratio']=='3:4'
        with psycopg.connect(format_db) as db:
            # Image tasks reserve a source here; the Image worker settles their own ledger later.
            assert db.execute('SELECT credits FROM users WHERE id=%s',(user,)).fetchone()[0]==997
            assert [a['status'] for a in saved['stage_attempts']]==['completed','completed','validation_failed','completed']
    finally:raw.pool.close()


def test_migration_roundtrip_is_invoker_only_and_not_public(format_db):
    with psycopg.connect(format_db) as db:
        db.execute((ROOT/'migrations/rollback/287_ecommerce_format_recovery_rollback.sql').read_text())
        db.execute((ROOT/'migrations/287_ecommerce_format_recovery.sql').read_text())
        rows=db.execute("SELECT prosecdef,has_function_privilege('image_untrusted',oid,'EXECUTE'),has_function_privilege('everydayai',oid,'EXECUTE') FROM pg_proc WHERE proname IN ('lock_ecom_format_plan','persist_ecom_plan_format_state','complete_ecom_plan_local_draft')").fetchall()
        assert len(rows)==3 and all(row==(False,False,True) for row in rows)


def test_insufficient_balance_rolls_back_local_output_audit_and_stage_advance(format_db):
    user,_,_,plan,lease,draft=seed_draft(format_db)
    with psycopg.connect(format_db) as db:
        db.execute('UPDATE users SET credits=1 WHERE id=%s',(user,))
        db.execute('UPDATE ecom_image_plans SET stage_attempts=%s WHERE id=%s',
            (Jsonb([{'stage':1,'status':'completed','usage':{'user_credits':10}}]),plan))
    with scoped(format_db,user) as db:
        with pytest.raises(psycopg.Error,match='INSUFFICIENT_CREDITS'):complete(db,plan,lease,draft)
    with psycopg.connect(format_db) as db:
        assert db.execute('SELECT status,current_stage,stage_outputs,stage_drafts FROM ecom_image_plans WHERE id=%s',(plan,)).fetchone()==(
            'planning',3,{}, {'3':draft})
        assert db.execute('SELECT credits FROM users WHERE id=%s',(user,)).fetchone()[0]==1


async def test_page_claim_reads_latest_format_flag_instead_of_stale_worker_row(format_db):
    from services.detail_page_recovery import plan_blocked
    user,project,run,plan=seed(format_db,1);opt_in(format_db,plan)
    _,evidence,bodies,_,_=fixture(1)
    with psycopg.connect(format_db) as db:
        db.execute('UPDATE ecom_image_plans SET current_stage=3,input_snapshot=%s,target_size=%s,prompt_versions=%s,stage_outputs=%s WHERE id=%s',
            (Jsonb(evidence['input_snapshot']),Jsonb(evidence['input_snapshot']['target_size']),Jsonb(resource_versions(PROMPTS_VERSION)),
             Jsonb({'1':evidence['product_selling_points'],'2':evidence['visual_direction']}),plan))
    raw=LocalDBClient(psycopg.conninfo.make_conninfo(format_db,user='everydayai'),min_size=1,max_size=2)
    owner=page_owner(raw,user,None,project);owner.task_id=plan;owner.cancellation_event=asyncio.Event();owner.execution_budget=None
    planner=EcommerceImagePlanner(owner,execution_profile=SimpleNamespace())
    planner._call=AsyncMock(side_effect=AssertionError('Do not repeat a spent formatting call'))
    try:
        stale=planner.scope.table('ecom_image_plans').select('*').eq('id',plan).single().execute().data
        with psycopg.connect(format_db) as db:
            db.execute('UPDATE ecom_image_plans SET stage_drafts=%s WHERE id=%s',
                (Jsonb({'3':{'output':'{"prompts":["incomplete','format_attempted':True,'format_version':'ecom-format.v1'}}),plan))
        result=await planner.execute(stale,evidence['input_snapshot']['references'],['test']*3,
            evidence['input_snapshot']['messages'],resources('main_images',PROMPTS_VERSION)[1],bodies)
        assert result.status=='error' and result.error_message=='PLANNER_DELIVERY_REPAIR_EXHAUSTED'
        planner._call.assert_not_awaited()
        latest=planner.scope.table('ecom_image_plans').select('*').eq('id',plan).single().execute().data
        assert plan_blocked(latest)
    finally:raw.pool.close()


@pytest.mark.parametrize('stage',[1,2,3])
async def test_sealed_legacy_draft_completes_via_real_planner_without_model_io(format_db,stage):
    from services.agent.image.ecommerce_planner.assembly import assemble_designs
    from services.agent.image.ecommerce_planner.contracts import validate_product, VISUAL_SECTIONS
    from services.agent.image.ecommerce_planner.prompt_resources import wrapper
    from tests.ecommerce_design_fixtures import design_fixture
    user,project,_,plan,lease,_=seed_draft(format_db,stage)
    designs,evidence,refs=design_fixture(1,'detail_page',3)
    bodies,schema=resources('detail_page')
    evidence['product_schema']=schema
    if stage==1:
        candidate=deepcopy(evidence['product_selling_points'])
        candidate['facts'][0]['unknown_null']=None
        validator=lambda v:validate_product(v,schema,evidence['input_snapshot'])
    elif stage==2:
        candidate=evidence['visual_direction'].replace('## 8.','### 8、')
        validator=lambda v:planner._validate_visual(v,VISUAL_SECTIONS,evidence['input_snapshot'])
    else:
        candidate=deepcopy(designs)
        candidate['images'][0]['/name']=candidate['images'][0].pop('name')
        validator=lambda v:assemble_designs(v,evidence['input_snapshot'],
            evidence['product_selling_points'],evidence['visual_direction'])
    draft={'output':candidate,'validation_error':'old-format-error'}
    attempts=[{'stage':stage,'status':'validation_failed','usage':{'user_credits':7},'attempt_id':str(uuid4())}]
    evidence['stage_drafts']={str(stage):draft}
    with psycopg.connect(format_db) as db:
        db.execute('UPDATE ecom_image_plans SET input_snapshot=%s,prompt_versions=%s,stage_drafts=%s,stage_attempts=%s WHERE id=%s',
            (Jsonb(evidence['input_snapshot']),Jsonb(resource_versions()),Jsonb(evidence['stage_drafts']),Jsonb(attempts),plan))
    raw=LocalDBClient(psycopg.conninfo.make_conninfo(format_db,user='everydayai'),min_size=1,max_size=2)
    owner=page_owner(raw,user,None,project);owner.task_id=plan
    owner.cancellation_event=asyncio.Event();owner.execution_budget=None
    planner=EcommerceImagePlanner(owner,execution_profile=SimpleNamespace())
    planner._call=AsyncMock(side_effect=AssertionError('Saved draft must complete locally'))
    try:
        row=planner.scope.table('ecom_image_plans').select('*').eq('id',plan).single().execute().data
        result,usage=await planner._page_stage(row,lease,stage,bodies[stage-1],wrapper(stage,'detail_page'),
            evidence,evidence['input_snapshot']['messages'],refs,['test']*3,validator)
        await planner._save(row,lease,stage,result,'ready' if stage==3 else 'planning',result if stage==3 else None,usage)
        planner._call.assert_not_awaited()
        saved=planner.scope.table('ecom_image_plans').select('*').eq('id',plan).single().execute().data
        assert saved['stage_outputs'][str(stage)]==result
        assert saved['stage_attempts']==attempts
        assert saved['stage_drafts'][str(stage)]['local_completion']['provider_calls']==0
        assert saved['stage_drafts'][str(stage)]['original_output']==candidate
        with psycopg.connect(format_db) as db:
            assert db.execute('SELECT credits FROM users WHERE id=%s',(user,)).fetchone()[0]==1000
    finally:raw.pool.close()
