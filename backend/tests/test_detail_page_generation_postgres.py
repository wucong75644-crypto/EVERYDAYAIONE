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
        db.execute((ROOT/'migrations/279_detail_default_generation_mode.sql').read_text())
        # The production schema still has migration 018's mandatory conversation.
        db.execute('''ALTER TABLE tasks ALTER COLUMN conversation_id SET NOT NULL,
            ALTER COLUMN delivery_context SET NOT NULL,
            ADD COLUMN execution_mode TEXT NOT NULL DEFAULT 'serial',
            ADD COLUMN queue_sequence BIGSERIAL NOT NULL,
            ADD COLUMN execution_attempt INTEGER NOT NULL DEFAULT 0''')
        db.execute((ROOT/'migrations/283_detail_page_generation.sql').read_text())
    # Reproduce the real legacy INSERT before installing the forward migration.
    user,project,run,plan=seed(ecom_db,1)
    item=str(uuid4()); frozen=snapshot(user,project,run,plan,item)
    with psycopg.connect(ecom_db) as db:
        db.execute("UPDATE ecom_image_plans SET status='ready',items=%s WHERE id=%s",(Jsonb([{
            'item_id':item,'position':1,'request_text':'exact','request_text_sha256':frozen['prompt_sha256'],'aspect_ratio':'1:1'}]),plan))
    with scoped(ecom_db,user) as db:
        with pytest.raises(psycopg.errors.NotNullViolation) as failure:
            db.execute('SELECT accept_detail_page_image(%s,%s,%s,%s)',(project,plan,item,Jsonb(frozen)))
        assert failure.value.diag.column_name=='conversation_id'
    with psycopg.connect(ecom_db) as db:
        db.execute((ROOT/'migrations/284_detail_page_reliability.sql').read_text())
    return ecom_db

@pytest.mark.parametrize('kind,count,valid', [('default',7,False),('main_image',16,False),('detail_page',15,True)])
def test_project_count_constraint_after_main_migration(page_db,kind,count,valid):
    user=str(uuid4())
    with psycopg.connect(page_db) as db:
        db.execute('INSERT INTO users(id,credits) VALUES(%s,100)',(user,))
        if valid:
            db.execute('INSERT INTO detail_projects(user_id,content_type,image_count) VALUES(%s,%s,%s)',(user,kind,count))
        else:
            with pytest.raises(psycopg.errors.CheckViolation):
                db.execute('INSERT INTO detail_projects(user_id,content_type,image_count) VALUES(%s,%s,%s)',(user,kind,count))


def scoped(dsn,user,worker=False):
    db=psycopg.connect(dsn)
    db.execute('SET SESSION AUTHORIZATION everydayai')
    db.execute("SELECT set_config('app.actor_user_id',%s,true),set_config('app.org_id','',true),set_config('app.access_kind',%s,true)",(user,'worker' if worker else 'runtime'))
    return db


def seed(dsn,count=7,combined=False,model='kimi-k3'):
    user,project,run,plan=[str(uuid4()) for _ in range(4)]
    with psycopg.connect(dsn) as db:
        db.execute('INSERT INTO users(id,credits) VALUES(%s,1000)',(user,))
        db.execute("INSERT INTO detail_projects(id,user_id,content_type,image_count,prompt_model) VALUES(%s,%s,%s,%s,%s)",(project,user,'default' if combined else 'main_image',14 if combined else count,model))
    rows=[{'id':plan,'project_id':project,'generation_run_id':run,'user_id':user,'org_id':None,
      'invocation_key':'main_images','input_digest':'a'*64,'image_count':count,
      'input_snapshot':{'resolved_references':[]},'target_size':{'aspect_ratio':'1:1','resolution':'1K'},
      'prompt_versions':{},'model_settings':{'model':model}}]
    if combined:
        rows.append({**deepcopy(rows[0]),'id':str(uuid4()),'invocation_key':'detail_page'})
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
    user,project,run,plan=seed(page_db,combined=True,model=model)
    db=LocalDBClient(psycopg.conninfo.make_conninfo(page_db,user='everydayai'),min_size=1,max_size=3)
    seen=[]
    for mode in ('main_images','detail_page'):
        if mode=='detail_page':
            with psycopg.connect(page_db) as c:
                plan=str(c.execute("SELECT id FROM ecom_image_plans WHERE project_id=%s AND invocation_key='detail_page'",(project,)).fetchone()[0])
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


@pytest.mark.parametrize('failed_stage', [2,3])
@pytest.mark.parametrize('model,kind,count', [
    ('kimi-k3','main_images',15), ('gemini-3.8-flash','detail_page',15),
    ('kimi-k3','detail_page',7), ('gemini-3.8-flash','main_images',7),
])
async def test_page_worker_resumes_failed_stage_and_accepts_images_with_nine_ordered_originals(
        page_db, monkeypatch, tmp_path, model, kind, count, failed_stage):
    import base64, json
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from PIL import Image
    from core.config import get_settings
    from core.local_db import LocalDBClient
    from services.adapters.base import StreamChunk
    from services.adapters.dashscope.chat_adapter import DashScopeAPIError
    from services.adapters.kie.client import KieClient, KieAuthenticationError
    from services.agent.image.ecommerce_planner.inputs import source_bindings
    from services.agent.image.ecommerce_planner.page_profile import profile
    from services.detail_page_generation import DetailPageWorker, PageImageInputResolver, page_owner, plan_error
    from tests.ecommerce_design_fixtures import design_fixture
    user, project, run, plan = seed(page_db, count, model=model)
    db = LocalDBClient(psycopg.conninfo.make_conninfo(page_db,user='everydayai'),min_size=1,max_size=3)
    config = get_settings()
    monkeypatch.setattr(config,'file_workspace_root',str(tmp_path))
    monkeypatch.setattr(config,'kie_api_key','test-key')
    monkeypatch.setattr(config,'dashscope_api_key','test-key')
    for prefix in ('kimi','gemini'):
        for direction in ('input','output'):
            monkeypatch.setattr(config,f'detail_{prefix}_{direction}_credits_per_million',1)
    monkeypatch.setenv(KieClient.SHADOW_OVERSEAS_PROXY_ENV,'http://127.0.0.1:7891')
    resolver = PageImageInputResolver(page_owner(db,user,None,project))
    root = Path(resolver.files.files.workspace_root)
    root.mkdir(parents=True,exist_ok=True)
    images = []
    originals = []
    for position in range(9):
        path = root/f'{position}.png'
        Image.new('RGB',(32,32),(position*20,50,100)).save(path)
        originals.append(path.read_bytes())
        images.append({'category':'reference' if position%2 else 'product','workspace_path':path.name})
    refs = resolver.bind(images)
    draft, evidence, _ = design_fixture(count,kind,9)
    snap = evidence['input_snapshot']
    snap.update(references=[{k:r[k] for k in ('file_id','source_id','role')} for r in refs],
        resolved_references=refs,messages=[{'source_id':f'project:{project}:v1',
            'parts':[{'content_index':0,'text':'  保留原文和图片顺序\n粉色风格  '}]}])
    snap['source_bindings'] = source_bindings(snap)
    frozen = vars(profile(config,model))
    with psycopg.connect(page_db) as c:
        c.execute('UPDATE ecom_image_plans SET input_snapshot=%s,target_size=%s,model_settings=%s WHERE id=%s',
            (Jsonb(snap),Jsonb(snap['target_size']),Jsonb({'model':model,'profile':frozen,'image_budget':{'max_requests':15,'max_credits':300}}),plan))
    # The real worker and SQL lease/resume/save paths run; only external HTTP is substituted.
    from services.detail_page_generation import page_scope
    scoped_db = page_scope(db,user,None)
    service = SimpleNamespace(db=scoped_db,user_id=user,org_id=None)
    uploads, seen = [], []
    async def upload(client, content, content_type, file_name):
        assert content == originals[len(uploads)%9] and content_type == 'image/png'
        uploads.append(file_name)
        return f'https://kie.invalid/temporary/{len(uploads)}.png'
    monkeypatch.setattr(KieClient,'upload_image_bytes',upload)
    fail_third = True
    def open_chat(request):
        assert request.model_id == model
        async def stream(sent,**kwargs):
            body = json.loads(sent[1]['content'][0]['text'])
            stage = body['stage']
            seen.append(stage)
            assert body['raw_user_texts'][0]['text'] == '  保留原文和图片顺序\n粉色风格  '
            media = [part['image_url'] for part in sent[1]['content'] if part['type']=='input_image']
            if model == 'kimi-k3':
                assert [base64.b64decode(url.split(',',1)[1]) for url in media] == originals
            else:
                start = len(uploads)-8
                assert media == [f'https://kie.invalid/temporary/{i}.png' for i in range(start,start+9)]
            assert ('response_format' in kwargs) == (model=='kimi-k3' and stage in (1,3))
            if stage==failed_stage and fail_third:
                if failed_stage==2:
                    from services.model_gateway import ModelGatewayTimeoutError
                    raise ModelGatewayTimeoutError(model,300,'stream')
                if model=='kimi-k3':
                    raise DashScopeAPIError.from_http(b'{"error":{"code":"InvalidApiKey","message":"rejected"}}',401)
                raise KieAuthenticationError('rejected',status_code=401,error_code='401')
            reply = {1:evidence['product_selling_points'],2:evidence['visual_direction'],3:draft}[stage]
            yield StreamChunk(content=reply if isinstance(reply,str) else json.dumps(reply),prompt_tokens=100,completion_tokens=10)
        return SimpleNamespace(stream_chat=stream,last_result=SimpleNamespace(status='completed',usage={}),close=AsyncMock())
    monkeypatch.setattr('services.agent.image.ecommerce_planner.service.get_model_gateway',
        lambda:SimpleNamespace(open_chat=open_chat))
    def row():return scoped_db.table('ecom_image_plans').select('*').eq('id',plan).single().execute().data
    try:
        worker = DetailPageWorker(db)
        await worker.run(service,row())
        failed = row()
        assert failed['status']=='failed' and set(failed['stage_outputs'])=={str(i) for i in range(1,failed_stage)}
        assert seen == list(range(1,failed_stage+1))
        if failed_stage==2:
            old=next(a for a in failed['recovery_state']['attempts'].values() if a['stage']==2)
            assert old['outcome']=='uncertain' and old['usage']['local_request_closed'] is True
        assert len(uploads) == (9 if model=='gemini-3.8-flash' else 0)
        assert plan_error(failed)['message']
        previous = deepcopy(failed['stage_outputs'])
        scoped_db.rpc('resume_detail_page_plan',{'p_project_id':project,'p_plan_id':plan,
            'p_request_id':str(uuid4())}).execute()
        fail_third = False
        await worker.run(service,row())
        saved = row()
        assert saved['status']=='ready' and len(saved['items'])==count
        assert seen == list(range(1,failed_stage+1))+list(range(failed_stage,4))
        assert all(saved['stage_outputs'][key]==value for key,value in previous.items())
        assert len(uploads) == (18 if model=='gemini-3.8-flash' else 0)
        assert saved['input_snapshot']['resolved_references'] == refs
        # Analysis representation never enters the durable final image request.
        for item in saved['items']:
            frozen_image = freeze_image_request({'mode':'image_to_image','prompt':item['request_text'],
                'aspect_ratio':item['aspect_ratio'],'resolution':'2K'},refs,
                origin={'destination':'detail_project','project_id':project,'generation_run_id':run,
                    'actor_user_id':user,'org_id':None,'workspace_owner_id':user,'context_scope':'user',
                    'plan_source':{'plan_id':plan,'revision':1,'item_id':item['item_id'],
                        'request_text_sha256':item['request_text_sha256']}},max_requests=15,max_credits=300)
            assert frozen_image['references']==refs
            assert 'data:image/' not in item['request_text'] and 'kie.invalid/temporary' not in item['request_text']
        assert [a['stage'] for a in saved['stage_attempts']].count(1)==1
        assert [a['stage'] for a in saved['stage_attempts'] if a.get('attempt_id')].count(2)==(2 if failed_stage==2 else 1)
        # Real page acceptance -> shared claim -> result -> publication -> one settlement.
        from services.detail_page_generation import DetailPageGeneration
        generation=DetailPageGeneration(db,user,None)
        receipts=await generation.accept(saved)
        assert len(receipts)==count
        assert (await generation.accept(saved))['outcome']=='replay'
        for receipt in receipts:
            with scoped(page_db,user,True) as c:
                claimed=c.execute('SELECT claim_chat_image_submission(%s,%s,60)',(receipt['task_id'],str(uuid4()))).fetchone()[0]
                assert claimed['outcome']=='claimed'
                c.execute('SELECT record_chat_image_provider_result(%s,%s)',(receipt['task_id'],Jsonb({'status':'success'})))
                assert c.execute("SELECT publish_chat_image_result(%s,%s,'completed')",(receipt['task_id'],Jsonb([{'type':'image','url':'result'}]))).fetchone()[0]['outcome']=='published'
        with psycopg.connect(page_db) as c:
            assert c.execute('SELECT count(*) FROM credit_transactions WHERE user_id=%s',(user,)).fetchone()[0]==count
            assert c.execute("SELECT count(*) FROM tasks WHERE user_id=%s AND conversation_id IS NULL AND status='completed'",(user,)).fetchone()[0]==count
    finally:
        db.pool.close()


def ready_item(dsn, count=1):
    user,project,run,plan=seed(dsn,count)
    items=[]; snapshots=[]
    for position in range(1,count+1):
        item=str(uuid4()); frozen=snapshot(user,project,run,plan,item)
        items.append({'item_id':item,'position':position,'request_text':'exact',
            'request_text_sha256':frozen['prompt_sha256'],'aspect_ratio':'1:1'})
        snapshots.append({'item_id':item,'snapshot':frozen})
    with psycopg.connect(dsn) as db:
        db.execute("UPDATE ecom_image_plans SET status='ready',items=%s WHERE id=%s",(Jsonb(items),plan))
    return user,project,run,plan,snapshots


@pytest.mark.parametrize('change', ['missing_origin','null_origin','null_destination','wrong_type','wrong_user',
    'wrong_org','wrong_project','wrong_run','wrong_plan','wrong_item','wrong_revision','missing_hash',
    'null_hash','prompt','refs','null_refs','ratio','resolution','fake_conversation','chat_delivery','chat_message'])
def test_direct_task_insert_cannot_forge_page_exception(page_db,change):
    user,project,run,plan,rows=ready_item(page_db)
    frozen=rows[0]['snapshot']; o=frozen['origin']; conversation=None; kind='image'; task_user=user
    org=None; delivery={}; message=None
    if change=='missing_origin':del frozen['origin']
    elif change=='null_origin':frozen['origin']=None
    elif change=='null_destination':o['destination']=None
    elif change=='wrong_type':kind='chat'
    elif change=='wrong_user':task_user=str(uuid4())
    elif change=='wrong_org':org=str(uuid4())
    elif change in ('wrong_project','wrong_run'):o['project_id' if change=='wrong_project' else 'generation_run_id']=str(uuid4())
    elif change in ('wrong_plan','wrong_item'):o['plan_source']['plan_id' if change=='wrong_plan' else 'item_id']=str(uuid4())
    elif change=='wrong_revision':o['plan_source']['revision']=2
    elif change=='missing_hash':del frozen['request_hash']
    elif change=='null_hash':frozen['request_hash']=None
    elif change=='prompt':frozen['prompt']='forged'
    elif change=='refs':frozen['references']=[{'file_id':'forged'}]
    elif change=='null_refs':frozen['references']=None
    elif change=='ratio':frozen['aspect_ratio']='4:3'
    elif change=='resolution':frozen['resolution']='4K'
    elif change=='fake_conversation':conversation=str(uuid4())
    elif change=='chat_delivery':delivery={'actor':True}
    elif change=='chat_message':message=str(uuid4())
    with scoped(page_db,user) as db:
        with pytest.raises(psycopg.Error):
            db.execute('INSERT INTO tasks(id,user_id,org_id,conversation_id,type,model_id,request_params,delivery_context,assistant_message_id) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                (str(uuid4()),task_user,org,conversation,kind,frozen['model'],Jsonb({'_media_request_v1':frozen,'_media_lifecycle_v1':{'phase':'queued'}}),Jsonb(delivery),message))


def test_atomic_group_rollback_then_saved_ready_acceptance_retry(page_db):
    user,project,run,plan,rows=ready_item(page_db,7)
    bad=deepcopy(rows);bad[-1]['snapshot']['request_hash']=None
    with scoped(page_db,user) as db:
        with pytest.raises(psycopg.Error):
            db.execute('SELECT accept_detail_page_group(%s,%s,%s)',(project,plan,Jsonb(bad)))
    with psycopg.connect(page_db) as db:
        assert db.execute("SELECT count(*) FROM tasks WHERE request_params->'_media_request_v1'->'origin'->>'project_id'=%s",(project,)).fetchone()[0]==0
        db.execute("UPDATE ecom_image_plans SET recovery_state=%s,stage_outputs=%s WHERE id=%s",(
            Jsonb({'acceptance_error':{'code':'DETAIL_GENERATION_FAILED'}}),Jsonb({'1':{'saved':True},'2':'saved','3':{'saved':True}}),plan))
    request=str(uuid4())
    with scoped(page_db,user) as db:
        db.execute('SELECT resume_detail_page_plan(%s,%s,%s)',(project,plan,request))
        replay=db.execute('SELECT resume_detail_page_plan(%s,%s,%s)',(project,plan,request)).fetchone()[0]
        assert replay['status']=='ready' and replay['stage_outputs']['2']=='saved'
        assert 'acceptance_error' not in replay['recovery_state']
        receipt=db.execute('SELECT accept_detail_page_group(%s,%s,%s)',(project,plan,Jsonb(rows))).fetchone()[0]
        second=db.execute('SELECT accept_detail_page_group(%s,%s,%s)',(project,plan,Jsonb(rows))).fetchone()[0]
        assert [r['task_id'] for r in receipt]==[r['task_id'] for r in second]
        assert all(r['outcome']=='replay' for r in second)
    with psycopg.connect(page_db) as db:
        assert db.execute('SELECT count(*) FROM tasks WHERE user_id=%s',(user,)).fetchone()[0]==7
        assert db.execute('SELECT count(*) FROM credit_transactions WHERE user_id=%s',(user,)).fetchone()[0]==0


@pytest.mark.parametrize('closed,legacy,live,allowed',[
    (True,False,False,True),(False,False,False,False),(True,True,False,True),(True,False,True,False),
])
def test_explicit_timeout_resume_preserves_unknown_history_and_respects_live_lease(page_db,closed,legacy,live,allowed):
    from services.detail_page_generation import can_resume_plan
    user,project,run,plan=seed(page_db)
    attempt={'outcome':'uncertain','completed_at':'2026-10-10T04:12:42Z','usage':{
        'error_type':'ModelGatewayTimeoutError','error_code':'MODEL_TIMEOUT','output_characters':2380}}
    if not legacy:attempt['usage']['local_request_closed']=closed
    state={'attempts':{'old-call':attempt},'counts':{'1':1,'2':1},'deadline':'2026-10-10T04:26:12Z',
        'last_error':{'code':'MODEL_TIMEOUT','category':'uncertain'}}
    with psycopg.connect(page_db) as db:
        db.execute("UPDATE ecom_image_plans SET status='failed',current_stage=2,stage_outputs=%s,recovery_state=%s,lease_expires_at=CASE WHEN %s THEN now()+interval '10 minutes' ELSE NULL END WHERE id=%s",
            (Jsonb({'1':{'saved':'facts'}}),Jsonb(state),live,plan))
        from psycopg.rows import dict_row
        row=db.cursor(row_factory=dict_row).execute('SELECT * FROM ecom_image_plans WHERE id=%s',(plan,)).fetchone()
        assert can_resume_plan(row) is allowed
        from services.detail_page_generation import plan_error
        if allowed:assert '已超时结束' in plan_error(row)['message']
    request=str(uuid4())
    with scoped(page_db,user) as db:
        if not allowed:
            with pytest.raises(psycopg.Error):db.execute('SELECT resume_detail_page_plan(%s,%s,%s)',(project,plan,request))
        else:
            db.execute('SELECT resume_detail_page_plan(%s,%s,%s)',(project,plan,request))
            replay=db.execute('SELECT resume_detail_page_plan(%s,%s,%s)',(project,plan,request)).fetchone()[0]
            assert replay['stage_outputs']=={'1':{'saved':'facts'}}
            assert replay['recovery_state']['attempts']=={}
            assert replay['recovery_state']['previous_windows']==[state]


def test_source_remains_immutable_and_lifecycle_publication_is_allowed(page_db):
    user,project,run,plan,rows=ready_item(page_db)
    with scoped(page_db,user) as db:
        task=db.execute('SELECT accept_detail_page_image(%s,%s,%s,%s)',(project,plan,rows[0]['item_id'],Jsonb(rows[0]['snapshot']))).fetchone()[0]['task_id']
    with scoped(page_db,user) as db:
        with pytest.raises(psycopg.Error,match='IMMUTABLE'):
            db.execute("UPDATE tasks SET request_params=jsonb_set(request_params,'{_media_request_v1,prompt}','\"changed\"') WHERE id=%s",(task,))
    with scoped(page_db,user,True) as db:
        db.execute('SELECT claim_chat_image_submission(%s,%s,60)',(task,str(uuid4())))
        db.execute('SELECT record_chat_image_provider_result(%s,%s)',(task,Jsonb({'status':'success'})))
    with scoped(page_db,user,True) as db:
        result=db.execute("SELECT publish_chat_image_result(%s,%s,'completed')",(task,Jsonb([{'type':'image','url':'result'}]))).fetchone()[0]
        assert result['outcome']=='published'


def test_page_migration_preserves_real_chat_accept_claim_publish_and_required_conversation(page_db):
    from tests.test_chat_image_lifecycle_postgres import facts,accept,claim,worker_rpc,publish
    f=facts.__wrapped__(page_db)
    task=accept(f)['task_id']
    assert claim(f,task)['outcome']=='claimed'
    worker_rpc(f,'record_chat_image_provider_result',task,Jsonb({'status':'success'}))
    assert publish(f,task,status='completed')['outcome']=='published'
    with psycopg.connect(page_db) as db:
        assert db.execute('SELECT conversation_id FROM tasks WHERE id=%s',(task,)).fetchone()[0] is not None
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute("INSERT INTO tasks(id,user_id,type) VALUES(%s,%s,'chat')",(str(uuid4()),f['user']))


def test_page_exception_checks_organization_membership_and_scope(page_db):
    user,project,run,plan,rows=ready_item(page_db)
    org=str(uuid4());wrong_org=str(uuid4());frozen=rows[0]['snapshot'];frozen['origin']['org_id']=org
    with psycopg.connect(page_db) as db:
        db.execute('INSERT INTO organizations(id) VALUES(%s)',(org,))
        db.execute('INSERT INTO org_members(org_id,user_id) VALUES(%s,%s)',(org,user))
        db.execute('UPDATE detail_projects SET org_id=%s WHERE id=%s',(org,project))
        db.execute('UPDATE ecom_image_plans SET org_id=%s WHERE id=%s',(org,plan))
    for active in (wrong_org,org):
        with scoped(page_db,user) as db:
            db.execute("SELECT set_config('app.org_id',%s,true)",(active,))
            if active==wrong_org:
                with pytest.raises(psycopg.Error):db.execute('SELECT accept_detail_page_image(%s,%s,%s,%s,%s)',(project,plan,rows[0]['item_id'],Jsonb(frozen),active))
            else:
                assert db.execute('SELECT accept_detail_page_image(%s,%s,%s,%s,%s)',(project,plan,rows[0]['item_id'],Jsonb(frozen),active)).fetchone()[0]['outcome']=='accepted'


def test_direct_insert_cannot_bypass_group_deduplication_or_budget(page_db):
    user,project,run,plan,rows=ready_item(page_db,2)
    with psycopg.connect(page_db) as db:
        db.execute("UPDATE ecom_image_plans SET model_settings=model_settings||%s WHERE id=%s",(
            Jsonb({'image_budget':{'max_requests':1,'max_credits':300}}),plan))
    with scoped(page_db,user) as db:
        db.execute('SELECT accept_detail_page_image(%s,%s,%s,%s)',(project,plan,rows[0]['item_id'],Jsonb(rows[0]['snapshot'])))
    for i,error in ((0,'DETAIL_IMAGE_DUPLICATE'),(1,'DETAIL_IMAGE_BUDGET_EXCEEDED')):
        frozen=rows[i]['snapshot']
        with scoped(page_db,user) as db:
            with pytest.raises(psycopg.Error,match=error):
                db.execute("INSERT INTO tasks(id,user_id,type,model_id,request_params) VALUES(%s,%s,'image',%s,%s)",
                    (str(uuid4()),user,frozen['model'],Jsonb({'_media_request_v1':frozen,'_media_lifecycle_v1':{'phase':'queued'}})))


def test_page_image_retry_reuses_the_frozen_source_and_request_id(page_db):
    user,project,run,plan,rows=ready_item(page_db)
    frozen=rows[0]['snapshot']
    with scoped(page_db,user) as db:
        task=db.execute('SELECT accept_detail_page_image(%s,%s,%s,%s)',(project,plan,rows[0]['item_id'],Jsonb(frozen))).fetchone()[0]['task_id']
    with scoped(page_db,user,True) as db:
        db.execute('SELECT claim_chat_image_submission(%s,%s,60)',(task,str(uuid4())))
        db.execute('SELECT record_chat_image_provider_result(%s,%s)',(task,Jsonb({'status':'success'})))
        db.execute("SELECT publish_chat_image_result(%s,%s,'completed')",(task,Jsonb([{'type':'image','url':'result'}])))
    request=str(uuid4());retry=deepcopy(frozen)
    retry['origin'].update(retry_request_id=request,retry_of_task_id=task)
    with scoped(page_db,user) as db:
        receipt=db.execute('SELECT replay_chat_image_snapshot(%s,%s,%s,NULL,true)',(task,request,Jsonb(retry))).fetchone()[0]
        replay=db.execute('SELECT replay_chat_image_snapshot(%s,%s,%s,NULL,true)',(task,request,Jsonb(retry))).fetchone()[0]
        assert receipt['task_id']==replay['task_id'] and replay['outcome']=='replay'


def test_old_heartbeat_cannot_reclaim_a_failed_or_resumed_plan(page_db):
    from types import SimpleNamespace
    from core.local_db import LocalDBClient
    from services.detail_page_generation import DetailPageWorker,page_scope
    user,project,run,plan=seed(page_db)
    raw=LocalDBClient(psycopg.conninfo.make_conninfo(page_db,user='everydayai'),min_size=1,max_size=2)
    service=SimpleNamespace(db=page_scope(raw,user,None),user_id=user)
    old,new=str(uuid4()),str(uuid4())
    try:
        with scoped(page_db,user) as db:
            assert db.execute('SELECT claim_ecom_image_plan(%s,NULL,%s,600)',(plan,old)).fetchone()[0]['claimed']
        assert DetailPageWorker.renew(service,plan,old)
        with psycopg.connect(page_db) as db:
            db.execute("UPDATE ecom_image_plans SET status='failed',lease_token=NULL,lease_expires_at=NULL WHERE id=%s",(plan,))
        assert not DetailPageWorker.renew(service,plan,old)
        with scoped(page_db,user) as db:
            db.execute('SELECT resume_detail_page_plan(%s,%s,%s)',(project,plan,str(uuid4())))
        assert not DetailPageWorker.renew(service,plan,old)
        with scoped(page_db,user) as db:
            assert db.execute('SELECT claim_ecom_image_plan(%s,NULL,%s,600)',(plan,new)).fetchone()[0]['claimed']
        assert not DetailPageWorker.renew(service,plan,old)
        assert DetailPageWorker.renew(service,plan,new)
        with psycopg.connect(page_db) as db:
            db.execute("UPDATE detail_projects SET status='archived' WHERE id=%s",(project,))
        assert not DetailPageWorker.renew(service,plan,new)
    finally:raw.pool.close()
