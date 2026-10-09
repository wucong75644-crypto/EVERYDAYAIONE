"""Page planning and image settlement against a disposable, role-accurate database."""
from copy import deepcopy
from uuid import uuid4
from pathlib import Path
import psycopg
from psycopg.types.json import Jsonb
import pytest
from tests.test_ecommerce_recovery_postgres import ecom_db
from tests.test_chat_image_lifecycle_postgres import isolated_db
from services.handlers.chat_image_request import freeze_image_request

ROOT=Path(__file__).parents[1]
@pytest.fixture(scope='module')
def page_db(ecom_db):
    with psycopg.connect(ecom_db) as db:
        db.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
        db.execute((ROOT/'migrations/118_detail_projects.sql').read_text())
        db.execute('ALTER TABLE detail_projects OWNER TO everydayai')
        db.execute('ALTER TABLE detail_project_images OWNER TO everydayai')
        db.execute((ROOT/'migrations/283_detail_page_generation.sql').read_text())
    return ecom_db


def scoped(dsn,user,worker=False):
    db=psycopg.connect(dsn)
    db.execute('SET SESSION AUTHORIZATION everydayai')
    db.execute("SELECT set_config('app.actor_user_id',%s,true),set_config('app.org_id','',true),set_config('app.access_kind',%s,true)",(user,'worker' if worker else 'runtime'))
    return db


def seed(dsn,count=7):
    user,project,run,plan=[str(uuid4()) for _ in range(4)]
    with psycopg.connect(dsn) as db:
        db.execute('INSERT INTO users(id,credits) VALUES(%s,1000)',(user,))
        db.execute("INSERT INTO detail_projects(id,user_id,content_type,image_count) VALUES(%s,%s,'main_image',%s)",(project,user,count))
    rows=[{'id':plan,'project_id':project,'generation_run_id':run,'user_id':user,'org_id':None,
      'invocation_key':'main_images','input_digest':'a'*64,'image_count':count,
      'input_snapshot':{'resolved_references':[]},'target_size':{'aspect_ratio':'1:1','resolution':'1K'},
      'prompt_versions':{},'model_settings':{'model':'kimi-k3'}}]
    with scoped(dsn,user) as db:
        result=db.execute('SELECT start_detail_page_run(%s,1,%s,%s)',(project,run,Jsonb(rows))).fetchone()[0]
        assert result['status']=='analyzing'
        assert db.execute('SELECT start_detail_page_run(%s,1,%s,%s)',(project,run,Jsonb(rows))).fetchone()[0]['id']==project
    return user,project,run,plan


def snapshot(user,project,run,plan,item):
    return freeze_image_request({'mode':'text_to_image','prompt':'exact','aspect_ratio':'1:1','resolution':'1K'},[],origin={
      'destination':'detail_project','project_id':project,'generation_run_id':run,'actor_user_id':user,'org_id':None,
      'workspace_owner_id':user,'context_scope':'user','plan_source':{'plan_id':plan,'revision':1,'item_id':item,
      'request_text_sha256':__import__('hashlib').sha256(b'exact').hexdigest()}},max_requests=15,max_credits=300)


def test_page_claim_and_reservation_have_no_chat_identity(page_db):
    user,project,run,plan=seed(page_db)
    lease,attempt=str(uuid4()),str(uuid4())
    with scoped(page_db,user) as db:
        assert db.execute('SELECT claim_ecom_image_plan(%s,NULL,%s,600)',(plan,lease)).fetchone()[0]['claimed']
        receipt=db.execute('SELECT reserve_ecom_plan_attempt(%s,%s,%s,1,1200)',(plan,lease,attempt)).fetchone()[0]
        assert receipt['outcome']=='execute'
        with pytest.raises(psycopg.Error,match='UNCERTAIN'):
            db.execute('SELECT reserve_ecom_plan_attempt(%s,%s,%s,1,1200)',(plan,lease,str(uuid4())))


def test_page_images_reuse_claim_publish_and_do_not_create_messages(page_db):
    user,project,run,plan=seed(page_db,1);item=str(uuid4());s=snapshot(user,project,run,plan,item)
    with psycopg.connect(page_db) as db:
        before=db.execute('SELECT COUNT(*) FROM messages').fetchone()[0]
        db.execute("UPDATE ecom_image_plans SET status='ready',items=%s WHERE id=%s",(Jsonb([{
          'item_id':item,'position':1,'request_text':'exact','request_text_sha256':s['prompt_sha256'],'aspect_ratio':'1:1'}]),plan))
    with scoped(page_db,user) as db:
        receipt=db.execute('SELECT accept_detail_page_image(%s,%s,%s,%s)',(project,plan,item,Jsonb(s))).fetchone()[0]
        assert db.execute('SELECT accept_detail_page_image(%s,%s,%s,%s)',(project,plan,item,Jsonb(s))).fetchone()[0]['task_id']==receipt['task_id']
    task=receipt['task_id']
    with scoped(page_db,user,True) as db:
        assert db.execute('SELECT claim_chat_image_submission(%s,%s,60)',(task,str(uuid4()))).fetchone()[0]['outcome']=='claimed'
        db.execute('SELECT record_chat_image_provider_result(%s,%s)',(task,Jsonb({'status':'success'})))
        result=db.execute('SELECT publish_chat_image_result(%s,%s,\'completed\')',(task,Jsonb([{'type':'image','url':'result'}]))).fetchone()[0]
        assert result['outcome']=='published'
        assert db.execute('SELECT publish_chat_image_result(%s,%s,\'completed\')',(task,Jsonb([{'type':'image','url':'result'}]))).fetchone()[0]['outcome']=='replay'
    with psycopg.connect(page_db) as db:
        assert db.execute('SELECT COUNT(*) FROM messages').fetchone()[0]==before
        assert db.execute('SELECT COUNT(*) FROM credit_transactions WHERE task_id=%s',(task,)).fetchone()[0]==1
    with scoped(page_db,str(uuid4())) as db:
        with pytest.raises(psycopg.Error,match='DENIED'):
            db.execute('SELECT accept_detail_page_image(%s,%s,%s,%s)',(project,plan,item,Jsonb(s)))


def test_page_resume_preserves_completed_stages_and_rejects_uncertain_calls(page_db):
    user,project,run,plan=seed(page_db)
    outputs={'1':{'status':'ready','facts':['saved']},'2':'saved visual direction'}
    with psycopg.connect(page_db) as db:
        db.execute("UPDATE detail_projects SET status='failed' WHERE id=%s",(project,))
        db.execute("UPDATE ecom_image_plans SET status='failed',current_stage=3,stage_outputs=%s,stage_drafts=%s,recovery_state=%s WHERE id=%s",(
            Jsonb(outputs),Jsonb({'3':{'output':'saved partial'}}),Jsonb({'version':1,'counts':{'3':3},'attempts':{}}),plan))
    request=str(uuid4())
    with scoped(page_db,user) as db:
        assert db.execute('SELECT resume_detail_page_plan(%s,%s,%s)',(project,plan,request)).fetchone()[0]['status']=='resumed'
        replay=db.execute('SELECT resume_detail_page_plan(%s,%s,%s)',(project,plan,request)).fetchone()[0]
        assert replay['stage_outputs']==outputs and replay['stage_drafts']['3']['output']=='saved partial'
        assert replay['recovery_state']['counts']=={}
        assert len(replay['recovery_state']['previous_windows'])==1
    with psycopg.connect(page_db) as db:
        db.execute("UPDATE ecom_image_plans SET status='failed',recovery_state=%s WHERE id=%s",(
            Jsonb({'attempts':{'paid-call':{'outcome':'uncertain'}}}),plan))
    with scoped(page_db,user) as db:
        with pytest.raises(psycopg.Error,match='ECOM_PLAN_EXECUTION_UNCERTAIN'):
            db.execute('SELECT resume_detail_page_plan(%s,%s,%s)',(project,plan,str(uuid4())))

@pytest.mark.parametrize('model',['kimi-k3','gemini-3.8-flash'])
async def test_page_three_stages_keep_selected_model_and_exact_image_prompts(page_db,monkeypatch,model):
    import asyncio,json
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from core.local_db import LocalDBClient
    from services.adapters.base import StreamChunk
    from services.agent.file_id import compute_fid
    from services.agent.image.ecommerce_planner.inputs import source_bindings
    from services.agent.image.ecommerce_planner.prompt_resources import resources
    from services.agent.image.ecommerce_planner.service import EcommerceImagePlanner,PlannerStreamBudget
    from services.detail_page_generation import page_owner
    from tests.ecommerce_design_fixtures import design_fixture
    user,project,run,plan=seed(page_db)
    db=LocalDBClient(psycopg.conninfo.make_conninfo(page_db,user='everydayai'),min_size=1,max_size=3)
    seen=[]
    for mode in ('main_images','detail_page'):
        if mode=='detail_page':
            new_plan=str(uuid4())
            with psycopg.connect(page_db) as c:
                c.execute("INSERT INTO ecom_image_plans SELECT (jsonb_populate_record(NULL::ecom_image_plans,to_jsonb(p)||%s)).* FROM ecom_image_plans p WHERE id=%s",(Jsonb({'id':new_plan,'invocation_key':mode,'status':'planning','current_stage':1,'stage_outputs':{},'stage_attempts':[],'recovery_state':{},'items':[],'lease_token':None,'lease_expires_at':None}),plan))
            plan=new_plan
        draft,evidence,_=design_fixture(7,mode,3)
        refs=[{'file_id':compute_fid(None,f'product/{i}.png'),'role':'product' if i<2 else 'reference'} for i in range(3)]
        refs=[{**ref,'source_id':ref['file_id']} for ref in refs]
        resolved=[{**ref,'workspace_path':f'product/{i}.png','content_sha256':str(i+1)*64,'file_version':[1,2,3]} for i,ref in enumerate(refs)]
        snap=evidence['input_snapshot'];snap.update(references=refs,resolved_references=resolved,
            messages=[{'source_id':f'project:{project}:v1','parts':[{'content_index':0,'text':'  发财的感觉\n保留图片顺序  '}]}])
        snap['source_bindings']=source_bindings(snap)
        with psycopg.connect(page_db) as c:
            c.execute('UPDATE ecom_image_plans SET input_snapshot=%s,target_size=%s WHERE id=%s',(Jsonb(snap),Jsonb(snap['target_size']),plan))
        frozen=SimpleNamespace(ecom_image_planning_model=model,ecom_image_planning_reasoning='medium',ecom_image_planning_stage_timeout=300,
            ecom_image_planning_input_credits_per_million=1,ecom_image_planning_output_credits_per_million=1,wall_seconds=1200)
        owner=page_owner(db,user,None,project);owner.task_id=plan;owner.execution_budget=PlannerStreamBudget(None,1205);owner.cancellation_event=asyncio.Event()
        planner=EcommerceImagePlanner(owner,execution_profile=frozen)
        def open_chat(request):
            assert request.model_id==model and request.org_id is None
            async def stream(sent,**kwargs):
                body=json.loads(sent[1]['content'][0]['text']);seen.append((mode,body['stage']))
                assert body['raw_user_texts']==[{'source_ref':'text_1','text':'  发财的感觉\n保留图片顺序  '}]
                assert [r['source_ref'] for r in body['reference_inventory']]==['image_1','image_2','image_3']
                reply={1:evidence['product_selling_points'],2:evidence['visual_direction'],3:draft}[body['stage']]
                yield StreamChunk(content=reply if isinstance(reply,str) else json.dumps(reply),prompt_tokens=100,completion_tokens=10)
            return SimpleNamespace(stream_chat=stream,last_result=SimpleNamespace(status='completed',usage={}),close=AsyncMock())
        monkeypatch.setattr('services.agent.image.ecommerce_planner.service.get_model_gateway',lambda:SimpleNamespace(open_chat=open_chat))
        row=planner.scope.table('ecom_image_plans').select('*').eq('id',plan).single().execute().data
        bodies,schema=resources(mode)
        result=await planner.execute(row,resolved,['https://example.invalid/1']*3,snap['messages'],schema,bodies)
        assert result.status=='success',result.summary
        saved=planner.scope.table('ecom_image_plans').select('*').eq('id',plan).single().execute().data
        assert saved['status']=='ready' and len(saved['items'])==7
        for item in saved['items']:
            origin={'destination':'detail_project','project_id':project,'generation_run_id':run,'actor_user_id':user,'org_id':None,
                'workspace_owner_id':user,'context_scope':'user','plan_source':{'plan_id':plan,'revision':1,'item_id':item['item_id'],'request_text_sha256':item['request_text_sha256']}}
            frozen_image=freeze_image_request({'mode':'image_to_image','prompt':item['request_text'],'aspect_ratio':'3:4','resolution':'2K'},resolved,
                origin=origin,max_requests=15,max_credits=300)
            accepted=planner.scope.rpc('accept_detail_page_image',{'p_project_id':project,'p_plan_id':plan,'p_item_id':item['item_id'],'p_snapshot':frozen_image,'p_org_id':None}).execute().data
            task=planner.scope.table('tasks').select('request_params,conversation_id,assistant_message_id').eq('id',accepted['task_id']).single().execute().data
            assert task['request_params']['_media_request_v1']['prompt']==item['request_text']
            assert task['request_params']['_media_request_v1']['references']==resolved
            assert task['conversation_id'] is None and task['assistant_message_id'] is None
    assert seen==[(mode,stage) for mode in ('main_images','detail_page') for stage in (1,2,3)]
    db.pool.close()
