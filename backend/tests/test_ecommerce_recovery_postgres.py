"""Real transactions/RLS in the explicit Unix-socket temporary test cluster."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
import pytest

from tests.test_chat_image_lifecycle_postgres import isolated_db

ROOT = Path(__file__).parents[1]


@pytest.fixture(scope="module")
def ecom_db(isolated_db):
    with psycopg.connect(isolated_db) as c:
        c.execute("ALTER TYPE credits_change_type ADD VALUE 'conversation_cost'")
        c.execute("GRANT REFERENCES ON users,organizations,conversations,tasks,messages TO everydayai WITH GRANT OPTION")
        c.execute("SET LOCAL ROLE everydayai_owner")
        c.execute((ROOT / "migrations/279_ecommerce_image_plans.sql").read_text())
        c.execute((ROOT / "migrations/281_ecommerce_recovery.sql").read_text())
    return isolated_db


def scope(c, user):
    c.execute("SET SESSION AUTHORIZATION everydayai")
    c.execute("SELECT set_config('app.actor_user_id',%s,true),set_config('app.org_id','',true),set_config('app.access_kind','runtime',true)", (user,))


def seed(dsn, *, stage=1):
    user, conv, task, message, plan, lease, token, run = [str(uuid4()) for _ in range(8)]
    with psycopg.connect(dsn) as c:
        c.execute("INSERT INTO users(id,credits) VALUES(%s,100)", (user,))
        c.execute("INSERT INTO conversations(id,user_id) VALUES(%s,%s)", (conv,user))
        c.execute("INSERT INTO messages(id,conversation_id,role,content) VALUES(%s,%s,'user','[]')", (message,conv))
        c.execute("INSERT INTO tasks(id,user_id,conversation_id,type,status,input_message_id,execution_token,request_params,turn_id,base_context_revision,delivery_context) VALUES(%s,%s,%s,'chat','running',%s,%s,%s,%s,0,'{\"actor\":true}')",
            (task,user,conv,message,token,Jsonb({'_ecom_workflow': {'version':1,'kind':'main_images','generation_run_id':run}}),str(uuid4())))
        scope(c,user)
        c.execute("INSERT INTO ecom_image_plans(id,user_id,conversation_id,parent_task_id,input_message_id,base_context_revision,invocation_key,input_digest,status,current_stage,image_count,input_snapshot,prompt_versions,model_settings,target_size,lease_token,lease_expires_at) VALUES(%s,%s,%s,%s,%s,0,'initial',%s,'planning',%s,1,'{}','{}','{}','{}',%s,now()+interval '10 minutes')",
            (plan,user,conv,task,message,'a'*64,stage,lease))
    return dict(user=user,conv=conv,task=task,message=message,plan=plan,lease=lease,token=token,run=run,stage=stage)


def reserve(c, f, attempt=None):
    attempt = attempt or str(uuid4())
    value = c.execute("SELECT reserve_ecom_plan_attempt(%s,%s,%s,%s,600)", (f['plan'],f['lease'],attempt,f['stage'])).fetchone()[0]
    return attempt,value


def finish(c,f,attempt, *, output=None, status='planning', credits=4, outcome='validation_failed'):
    return c.execute("SELECT finish_ecom_plan_attempt(%s,%s,%s,%s,%s,%s,%s,%s,NULL,NULL,%s)",
        (f['plan'],f['lease'],f['stage'],attempt,Jsonb({'input_tokens':100,'user_credits':credits}),outcome,Jsonb(output) if output is not None else None,status,credits)).fetchone()[0]


def test_duplicate_finish_commits_exactly_one_charge_and_conflict_is_rejected(ecom_db):
    f=seed(ecom_db)
    with psycopg.connect(ecom_db) as c:
        scope(c,f['user'])
        attempt,r=reserve(c,f)
        assert r['remaining_attempts']==2
        assert finish(c,f,attempt)['outcome']=='saved'
        assert finish(c,f,attempt)['outcome']=='replay'
        assert c.execute('SELECT credits FROM users WHERE id=%s',(f['user'],)).fetchone()[0]==96
        assert c.execute('SELECT count(*) FROM credits_history WHERE user_id=%s',(f['user'],)).fetchone()[0]==1
    with psycopg.connect(ecom_db) as c:
        scope(c,f['user'])
        with pytest.raises(psycopg.errors.InvalidParameterValue,match='ECOM_PLAN_ATTEMPT_CONFLICT'):
            finish(c,f,attempt,credits=5)


def test_schema_and_network_rejections_share_three_calls_and_restart_keeps_count(ecom_db):
    f=seed(ecom_db,stage=3)
    for index in range(3):
        with psycopg.connect(ecom_db) as c:
            scope(c,f['user'])
            attempt,r=reserve(c,f)
            assert r['ordinal']==index+1
            finish(c,f,attempt,outcome='rejected' if index==1 else 'validation_failed',credits=0 if index==1 else 4)
    with psycopg.connect(ecom_db) as c:
        scope(c,f['user'])
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState,match='ECOM_PLAN_RETRY_EXHAUSTED'):
            reserve(c,f)


def test_started_or_uncertain_attempt_never_authorizes_a_second_call(ecom_db):
    f=seed(ecom_db)
    with psycopg.connect(ecom_db) as c:
        scope(c,f['user']); attempt,_=reserve(c,f)
    with psycopg.connect(ecom_db) as c:
        scope(c,f['user'])
        assert reserve(c,f,attempt)[1]['outcome']=='replay'
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState,match='ECOM_PLAN_EXECUTION_UNCERTAIN'):
            reserve(c,f)
    with psycopg.connect(ecom_db) as c:
        scope(c,f['user']); finish(c,f,attempt,credits=0,outcome='uncertain')
    with psycopg.connect(ecom_db) as c:
        scope(c,f['user'])
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState,match='ECOM_PLAN_EXECUTION_UNCERTAIN'):
            reserve(c,f)


def test_stage_two_json_string_is_saved_atomically_with_usage(ecom_db):
    f=seed(ecom_db,stage=2)
    with psycopg.connect(ecom_db) as c:
        scope(c,f['user']); attempt,_=reserve(c,f)
        text='## 1. 视觉定位\n原文风格'
        finish(c,f,attempt,output=text,outcome='completed')
        row=c.execute('SELECT stage_outputs,current_stage FROM ecom_image_plans WHERE id=%s',(f['plan'],)).fetchone()
        assert row==({'2':text},3)
        assert finish(c,f,attempt,output=text,outcome='completed')['outcome']=='replay'


def test_fresh_parent_inherits_exhausted_root_until_trusted_user_retry(ecom_db):
    f=seed(ecom_db,stage=3)
    with psycopg.connect(ecom_db) as c:
        scope(c,f['user'])
        for _ in range(3):
            a,_=reserve(c,f);finish(c,f,a,credits=0)
    child,task,lease,token=[str(uuid4()) for _ in range(4)]
    with psycopg.connect(ecom_db) as c:
        c.execute("INSERT INTO tasks(id,user_id,conversation_id,type,status,input_message_id,execution_token,request_params) VALUES(%s,%s,%s,'chat','running',%s,%s,'{}')",(task,f['user'],f['conv'],f['message'],token))
        scope(c,f['user'])
        c.execute("INSERT INTO ecom_image_plans SELECT %s,user_id,org_id,conversation_id,%s,input_message_id,base_context_revision,'child',plan_revision+1,id,input_digest,'planning',3,image_count,input_snapshot,prompt_versions,model_settings,stage_outputs,'[]',items,review_records,target_size,%s,now()+interval '10 minutes',1,now(),now(),id,'{}' FROM ecom_image_plans WHERE id=%s",(child,task,lease,f['plan']))
    cf={**f,'plan':child,'task':task,'lease':lease}
    with psycopg.connect(ecom_db) as c:
        scope(c,f['user'])
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState,match='ECOM_PLAN_RETRY_EXHAUSTED'):
            reserve(c,cf)
    with psycopg.connect(ecom_db) as c:
        scope(c,f['user'])
        c.execute('SELECT bind_ecom_workflow(%s,%s,%s)',(task,token,Jsonb({'version':1,'kind':'main_images','plan_id':child,'window_task_id':task,'reset_window':True,'generation_run_id':f['run']})))
        a,r=reserve(c,cf); assert r['ordinal']==1
        finish(c,cf,a,credits=0)
        a,r=reserve(c,cf); assert r['ordinal']==2  # same task cannot reset twice


def test_wrong_actor_and_stale_lease_cannot_reserve(ecom_db):
    f=seed(ecom_db)
    with psycopg.connect(ecom_db) as c:
        scope(c,str(uuid4()))
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState,match='ECOM_PLAN_LEASE_LOST'):
            reserve(c,f)
    with psycopg.connect(ecom_db) as c:
        scope(c,f['user'])
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState,match='ECOM_PLAN_LEASE_LOST'):
            reserve(c,{**f,'lease':str(uuid4())})


def test_recovery_functions_preserve_invoker_and_revoke_public(ecom_db):
    with psycopg.connect(ecom_db) as c:
        rows=c.execute("SELECT proname,prosecdef,has_function_privilege('image_untrusted',oid,'EXECUTE') FROM pg_proc WHERE proname IN ('bind_ecom_workflow','reserve_ecom_plan_attempt','finish_ecom_plan_attempt','fail_ecom_plan')").fetchall()
        assert len(rows)==4 and all(r[1:]==(False,False) for r in rows)


def ready_plan(dsn):
    from services.handlers.chat_image_request import freeze_image_request
    import hashlib
    f = seed(dsn, stage=3)
    f['item'] = str(uuid4())
    text = 'exact server-owned prompt'
    f['hash'] = hashlib.sha256(text.encode()).hexdigest()
    with psycopg.connect(dsn) as c:
        turn = str(c.execute('SELECT turn_id FROM tasks WHERE id=%s', (f['task'],)).fetchone()[0])
    origin = dict(parent_task_id=f['task'], actor_user_id=f['user'], workspace_owner_id=f['user'],
        org_id=None, context_scope='user', conversation_id=f['conv'], turn_id=turn,
        input_message_id=f['message'], base_context_revision=0, tool_call_id='first',
        plan_source={'plan_id':f['plan'],'revision':1,'item_id':f['item'],'request_text_sha256':f['hash']})
    snapshot = freeze_image_request({'mode':'text_to_image','prompt':text}, [], origin=origin,
        max_requests=15,max_credits=100)
    f['snapshot'] = snapshot
    item = dict(item_id=f['item'], request_text=text, request_text_sha256=f['hash'],
        aspect_ratio=snapshot['aspect_ratio'], references=[],position=1,name='主图',purpose='展示')
    with psycopg.connect(dsn) as c:
        scope(c,f['user'])
        c.execute("UPDATE ecom_image_plans SET status='ready',items=%s,target_size=%s,stage_outputs=%s WHERE id=%s",
            (Jsonb([item]),Jsonb({k:snapshot[k] for k in ('aspect_ratio','resolution')}),
                Jsonb({'1':{'product':{'name':'存钱本'}},'2':'已保存风格'}),f['plan']))
        c.execute("UPDATE tasks SET request_params=jsonb_set(request_params,'{_ecom_workflow,plan_id}',to_jsonb(%s::text)) WHERE id=%s",
            (f['plan'],f['task']))
    return f


def another_parent(dsn, f, *, new_run=False):
    from services.handlers.chat_image_request import freeze_image_request
    parent, token, turn = [str(uuid4()) for _ in range(3)]
    run = str(uuid4()) if new_run else f['run']
    with psycopg.connect(dsn) as c:
        c.execute("INSERT INTO tasks(id,user_id,conversation_id,type,status,input_message_id,execution_token,turn_id,base_context_revision,delivery_context,request_params) VALUES(%s,%s,%s,'chat','running',%s,%s,%s,0,'{\"actor\":true}',%s)",
            (parent,f['user'],f['conv'],f['message'],token,turn,Jsonb({'_ecom_workflow':{
                'version':1,'kind':'main_images','generation_run_id':run,'plan_id':f['plan']}})))
    origin={**f['snapshot']['origin'],'parent_task_id':parent,'turn_id':turn,'tool_call_id':'next'}
    snapshot=freeze_image_request({'mode':'text_to_image','prompt':f['snapshot']['prompt']},[],
        origin=origin,max_requests=15,max_credits=100)
    return {**f,'task':parent,'token':token,'run':run,'snapshot':snapshot}


def accept_plan(dsn,f):
    with psycopg.connect(dsn) as c:
        scope(c,f['user'])
        return c.execute('SELECT accept_chat_ecom_plan_image(%s,%s,%s,NULL,%s,1,%s)',
            (f['task'],f['token'],Jsonb(f['snapshot']),f['plan'],f['item'])).fetchone()[0]


def test_concurrent_cross_parent_retry_replays_one_child_and_explicit_regenerate_is_new(ecom_db):
    f=ready_plan(ecom_db)
    other=another_parent(ecom_db,f)
    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts=list(pool.map(lambda facts:accept_plan(ecom_db,facts),[f,other]))
    assert receipts[0]['task_id']==receipts[1]['task_id']
    assert receipts[0]['message_id']==receipts[1]['message_id']
    assert sorted(r['outcome'] for r in receipts)==['accepted','replay']
    with psycopg.connect(ecom_db) as c:
        assert c.execute("SELECT count(*) FROM tasks WHERE user_id=%s AND type='image'",(f['user'],)).fetchone()[0]==1
        # Acceptance is free; the existing worker settles the one accepted
        # task later. Recovery must not add a debit at acceptance.
        assert c.execute('SELECT count(*) FROM credits_history WHERE user_id=%s',(f['user'],)).fetchone()[0]==0
    regenerated=another_parent(ecom_db,f,new_run=True)
    assert accept_plan(ecom_db,regenerated)['task_id']!=receipts[0]['task_id']


def test_replayed_image_receipt_still_requires_current_token_and_bound_plan(ecom_db):
    f=ready_plan(ecom_db);accept_plan(ecom_db,f)
    other=another_parent(ecom_db,f)
    with pytest.raises(psycopg.errors.InsufficientPrivilege,match='ECOM_PLAN_PARENT_DENIED'):
        accept_plan(ecom_db,{**other,'token':str(uuid4())})
    with psycopg.connect(ecom_db) as c:
        scope(c,f['user'])
        c.execute("UPDATE tasks SET request_params=jsonb_set(request_params,'{_ecom_workflow,plan_id}',to_jsonb(%s::text)) WHERE id=%s",(str(uuid4()),other['task']))
    with pytest.raises(psycopg.errors.InsufficientPrivilege,match='ECOM_PLAN_WORKFLOW_MISMATCH'):
        accept_plan(ecom_db,other)


async def test_original_task_buttons_restore_scope_and_ready_plan_reports_submitted_items(ecom_db):
    from core.local_db import LocalDBClient
    from services.agent.image.ecommerce_planner.workflow import WorkflowBinding,restore_retry_binding
    from services.agent.image.ecommerce_planner.service import EcommerceImagePlanner
    f=ready_plan(ecom_db)
    receipt=accept_plan(ecom_db,f)
    wf=WorkflowBinding(kind='main_images',root_task_id=f['task'],window_task_id=f['task'],
        generation_run_id=f['run'],plan_id=f['plan'],skill={'revision':'v3','package_id':str(uuid4())})
    placeholder=str(uuid4())
    with psycopg.connect(ecom_db) as c:
        c.execute('UPDATE tasks SET placeholder_message_id=%s,request_params=%s WHERE id=%s',
            (placeholder,Jsonb({'_ecom_workflow':wf.model_dump(mode='json')}),f['task']))
    db=LocalDBClient(psycopg.conninfo.make_conninfo(ecom_db,user='everydayai'),min_size=1,max_size=3)
    try:
        inputs=dict(db=db,user_id=f['user'],org_id=None,conversation_id=f['conv'],message_id=placeholder)
        retry=await restore_retry_binding(**inputs,operation='retry')
        assert retry['plan_id']==f['plan'] and retry['generation_run_id']==f['run']
        assert retry['mode']=='retry' and retry['reset_window'] and retry['skill']==wf.skill
        regenerate=await restore_retry_binding(**inputs,operation='regenerate')
        assert regenerate['generation_run_id']!=f['run'] and regenerate['mode']=='regenerate'
        assert await restore_retry_binding(**{**inputs,'org_id':str(uuid4())},operation='retry') is None
        owner=SimpleNamespace(db=db,user_id=f['user'],org_id=None,task_id=f['task'])
        service=EcommerceImagePlanner(owner);service.workflow=wf
        result=await service._result(f['plan'],1,'ready')
        assert result.metadata['images'][0]['submission_state']=='submitted'
        assert result.metadata['images'][0]['task_id']==receipt['task_id']
        assert result.metadata['images'][0]['message_id']==receipt['message_id']
    finally:
        db.close()


def test_expired_persisted_deadline_and_null_bindings_are_denied(ecom_db):
    f=seed(ecom_db)
    with psycopg.connect(ecom_db) as c:
        scope(c,f['user']);a,_=reserve(c,f);finish(c,f,a,credits=0)
        c.execute("UPDATE ecom_image_plans SET recovery_state=jsonb_set(recovery_state,'{deadline}',to_jsonb(now()-interval '1 second')) WHERE id=%s",(f['plan'],))
    with psycopg.connect(ecom_db) as c:
        scope(c,f['user'])
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState,match='ECOM_PLAN_PARENT_BUDGET_EXHAUSTED'):
            reserve(c,f)
    with psycopg.connect(ecom_db) as c:
        scope(c,f['user'])
        with pytest.raises(psycopg.errors.InsufficientPrivilege,match='ECOM_WORKFLOW_SCOPE_DENIED'):
            c.execute('SELECT bind_ecom_workflow(%s,%s,NULL)',(f['task'],f['token']))


def professional_outputs(ref):
    from services.agent.image.ecommerce_planner.contracts import VISUAL_SECTIONS, LABELS, SCHEME_SECTIONS
    product={'schema_version':'product-selling-points.v2','status':'ready',
        'product':{'name':'存钱本','category':'文具','use':'存钱','sale_scope':None,
            'identity_status':'clear','fact_ids':['f1']},
        'facts':[{'id':'f1','statement':'金色存钱本','status':'usable','variant_ids':[],
            'sources':[{'source_id':ref['source_id'],'kind':'image_observation','locator':'图片1'}]}],
        'selling_points':[{'id':'s1','title':'财富主题','priority':'primary','buyer_need':'记录存钱',
            'buyer_need_basis':'inferred','scenario':None,'feature':'金色外观','mechanism':None,
            'benefit':'便于存钱记录','benefit_basis':'inferred','fact_ids':['f1'],
            'variant_ids':[],'claim_boundary':[]}], 'questions':[],'gaps':[]}
    visual='\n'.join(f'## {i}. {s}\n原文发财风格，内容{i}' for i,s in enumerate(VISUAL_SECTIONS,1))
    positive='\n'.join(f'【{label}】内容' for label in LABELS)
    positive+=f"\n输入图片1—{ref['source_id']}\n商品按参考图真实比例等比例缩放，排版围绕实际商品形状安排，不拉伸、压扁、增厚或改变部件比例。"
    negative='排除拉伸、压扁、增厚、部件比例错误、透视失真'
    scheme='\n'.join(f'## {s}\n'+ '\n'.join(f'**{field}** 内容' for field in fields)
        for s,fields in SCHEME_SECTIONS.items())
    scheme+=f'\n## 完整生图提示词\n{positive}\n## 负面提示词\n{negative}'
    final={'status':'ready','questions':[], 'images':[{'position':1,'name':'主图','purpose':'展示',
        'scheme_markdown':scheme,'references':[ref],'positive_prompt':positive,
        'negative_prompt':negative,'aspect_ratio':'1:1'}], 'review_records':[{
            'object':'整套方案','method':'self_check','checks':['比例与引用'], 'conclusion':'pass',
            'evidence':'引用保留','impact':'无','attribution':'自检','repair':'无','recheck_scope':'整套'}]}
    return product,visual,final


@pytest.mark.parametrize('failed_stage,mode,reuse,expected', [
    (3,'retry',2,[3]), (2,'retry',2,[2,3]), (3,'revise',1,[2,3]), (3,'revise',0,[1,2,3]),
    (3,'automatic',0,[]),
])
async def test_real_planner_resume_reuses_only_valid_stages_and_preserves_raw_inputs(
        ecom_db,monkeypatch,failed_stage,mode,reuse,expected):
    import asyncio
    import json
    from core.local_db import LocalDBClient
    from services.adapters.base import StreamChunk
    from services.agent.image.ecommerce_planner.service import EcommerceImagePlanner
    from services.agent.image.ecommerce_planner.prompt_resources import HASHES, SCHEMA_SHA256, INTEGRATION_RULES_SHA256
    from services.agent.image.ecommerce_planner.workflow import WorkflowBinding
    from tests.test_ecommerce_workflow import planner
    f=seed(ecom_db,stage=failed_stage)
    new_task,new_message,new_token=[str(uuid4()) for _ in range(3)]
    automatic=mode=='automatic'
    if automatic:
        new_task,new_message,new_token=f['task'],f['message'],f['token']
        with psycopg.connect(ecom_db) as c:
            scope(c,f['user'])
            for _ in range(3):
                a,_=reserve(c,f);finish(c,f,a,credits=0)
    ref={'asset_id':str(uuid4()),'role':'product'}
    public={**ref,'source_id':ref['asset_id']}
    resolved={**public,'workspace_path':'products/book.png','content_sha256':'b'*64,
        'file_version':1,'width':1024,'height':1024,'aspect_ratio':'1:1'}
    raw='帮我生成1张主图，要求带那种发财风格的，这个是存钱本'
    messages=[{'message_id':f['message'],'context_revision':0,'parts':[{'content_index':0,'text':raw}]}]
    target={'mode':'inherit_reference','aspect_ratio':'1:1','resolution':'1K','source':'reference'}
    snapshot={'references':[public],'messages':messages,'image_count':1,'task_type':'main_images',
        'resolved_references':[{k:resolved[k] for k in ('source_id','content_sha256','file_version','workspace_path')}],
        'target_size':target}
    product,visual,final=professional_outputs(public)
    outputs={'1':product,**({'2':visual} if failed_stage==3 else {})}
    versions={'resources_sha256':list(HASHES),'schema_sha256':SCHEMA_SHA256,
        'integration_reference_sha256':INTEGRATION_RULES_SHA256}
    wf=WorkflowBinding(kind='main_images',mode='new' if automatic else mode,root_task_id=f['task'],window_task_id=new_task,
        generation_run_id=f['run'],plan_id=f['plan'],source_task_id=f['task'],reset_window=not automatic,
        reuse_through_stage=reuse)
    current_text='重试' if mode=='retry' else '改为极简风格，保留金色' if reuse else '补充商品事实原文'
    with psycopg.connect(ecom_db) as c:
        if not automatic:
            c.execute("INSERT INTO messages(id,conversation_id,role,content) VALUES(%s,%s,'user',%s)",
                (new_message,f['conv'],json.dumps([{'type':'text','text':current_text}],ensure_ascii=False)))
            c.execute("INSERT INTO tasks(id,user_id,conversation_id,type,status,input_message_id,execution_token,turn_id,base_context_revision,request_params) VALUES(%s,%s,%s,'chat','running',%s,%s,%s,0,%s)",
                (new_task,f['user'],f['conv'],new_message,new_token,str(uuid4()),Jsonb({'_ecom_workflow':wf.model_dump(mode='json')})))
        else:
            c.execute('UPDATE tasks SET request_params=%s WHERE id=%s',
                (Jsonb({'_ecom_workflow':wf.model_dump(mode='json')}),new_task))
        scope(c,f['user'])
        c.execute("UPDATE ecom_image_plans SET status='failed',input_snapshot=%s,target_size=%s,stage_outputs=%s,prompt_versions=%s,model_settings=%s,recovery_state=recovery_state || %s WHERE id=%s",
            (Jsonb(snapshot),Jsonb(target),Jsonb(outputs),Jsonb(versions),Jsonb({'provider':'kie','model':'gpt-5-6-luna','reasoning_effort':'medium'}),
                Jsonb({'last_error':{'code':'PLANNER_IMAGE_JSON_INVALID' if automatic else 'KIE_AUTHENTICATION_FAILED',
                    'category':'output_validation' if automatic else 'authentication'}}),f['plan']))
    db=LocalDBClient(psycopg.conninfo.make_conninfo(ecom_db,user='everydayai'),min_size=1,max_size=3)
    owner=SimpleNamespace(db=db,user_id=f['user'],workspace_user_id=f['user'],org_id=None,
        conversation_id=f['conv'],task_id=new_task,context_scope='user',execution_mode='interactive',
        image_execution_token=new_token,_current_tool_call_id='resume',cancellation_event=asyncio.Event(),
        execution_budget=None,image_skill_snapshot=({'skill_key':'ecommerce-main-images'},))
    service=EcommerceImagePlanner(owner)
    service.settings=SimpleNamespace(**vars(planner().settings),ecom_image_planning_enabled=True)
    class Resolver:
        def __init__(self,*a,**kw): pass
        def _message(self,_):return {'role':'user','context_revision':1,'content':[{'type':'text','text':current_text}]}
        def resolve(self,refs):
            assert refs==[ref]
            return [resolved.copy()]
        def verify(self,refs):pass
        def preview(self,ref):return 'https://example.invalid/original.png'
        def size_context(self):return {},{}
    monkeypatch.setattr('services.handlers.chat_image_request.ChatImageInputResolver',Resolver)
    seen=[]
    def open_chat(request):
        async def stream(sent,**kwargs):
            body=json.loads(sent[1]['content'][0]['text']);stage=body['stage'];seen.append(body)
            assert sent[1]['content'][2]['image_url']=='https://example.invalid/original.png'
            reply={1:json.dumps(product,ensure_ascii=False),2:visual,3:json.dumps(final,ensure_ascii=False)}[stage]
            yield StreamChunk(content=reply,prompt_tokens=100,completion_tokens=10)
        return SimpleNamespace(stream_chat=stream,last_result=SimpleNamespace(status='completed',usage={}),close=AsyncMock())
    monkeypatch.setattr('services.agent.image.ecommerce_planner.service.get_model_gateway',lambda:SimpleNamespace(open_chat=open_chat))
    try:
        result=await service.run({'continue_plan_id':f['plan']})
        if automatic:
            assert result.status=='error' and result.error_message=='ECOM_PLAN_RETRY_EXHAUSTED'
            assert not seen
            with psycopg.connect(ecom_db) as c:
                rows=c.execute('SELECT id,root_plan_id,recovery_state FROM ecom_image_plans WHERE parent_task_id=%s',(f['task'],)).fetchall()
                assert len(rows)==2 and any(str(row[1])==f['plan'] for row in rows)
                assert next(row[2] for row in rows if str(row[0])==f['plan'])['counts']['3']==3
            return
        assert result.status=='success', result.summary
        assert result.metadata['retry_context']['generation_allowed'] is True
        assert [body['stage'] for body in seen]==expected
        assert all(body['raw_user_messages'][0]==messages[0] for body in seen)
        assert all(body['references_in_generation_order']==[{'ordinal':1,'source_id':public['source_id'],'role':'product'}] for body in seen)
        if mode=='retry':
            assert all(body['raw_user_messages']==messages for body in seen)
        else:
            assert all(body['raw_user_messages'][-1]['parts']==[{'content_index':0,'text':current_text}] for body in seen)
        plan_id=result.metadata['plan_id']
        with psycopg.connect(ecom_db) as c:
            saved=c.execute('SELECT stage_outputs,items FROM ecom_image_plans WHERE id=%s',(plan_id,)).fetchone()
            assert saved[0]['1']==product
            assert saved[1][0]['references']==[public]
            assert c.execute('SELECT count(*) FROM credits_history WHERE user_id=%s',(f['user'],)).fetchone()[0]==len(expected)
    finally:
        db.close()


@pytest.mark.parametrize('kind,available,model_calls,skill_loads', [
    ('main_images',True,1,1),('ordinary_image',True,1,0),('needs_input',True,0,0),
    ('main_images',False,0,0),
])
async def test_real_first_image_entry_activates_before_model_and_blocks_diy_text(
        ecom_db,monkeypatch,kind,available,model_calls,skill_loads):
    import asyncio
    import json
    from dataclasses import replace
    from core.local_db import LocalDBClient
    from schemas.message import ImagePart,TextPart
    from services.conversation_turn_runtime import ConversationTurnRuntime
    from services.adapters.base import StreamChunk
    from services.handlers.chat.execution_engine import execute_chat,ChatExecutionRequest
    from services.handlers.chat.execution_sink import CollectingExecutionSink
    from services.skills.runtime import SkillBindingError
    from tests.test_skill_runtime import Source,item,state
    from tests.test_skill_runtime_actor import prepared,handler
    f=seed(ecom_db)
    content=[TextPart(text='帮我生成5张主图，要求发财风格'),ImagePart(name='存钱本',workspace_path='book.png')]
    with psycopg.connect(ecom_db) as c:
        turn=str(c.execute("UPDATE tasks SET request_params='{}' WHERE id=%s RETURNING turn_id",(f['task'],)).fetchone()[0])
        c.execute('UPDATE messages SET content=%s WHERE id=%s',
            (json.dumps([p.model_dump(mode='json') for p in content],ensure_ascii=False),f['message']))
    db=LocalDBClient(psycopg.conninfo.make_conninfo(ecom_db,user='everydayai'),min_size=1,max_size=3)
    h=handler();h.db=db;h.org_id=None;h._actor_execution_token=f['token']
    p=prepared();p.stream_kwargs={}
    p.execution_context=replace(p.execution_context,org_id=None,context_scope='user',execution_mode='interactive',
        feature_flags={'ecom_image_planning_enabled':True,'chat_image_async_enabled':True})
    tools=('get_conversation_context','plan_ecommerce_images','generate_image')
    source=Source([item('ecommerce-main-images',revision='v3',tools=tools)] if available else [],
        body='必须调用主图策划子 Agent，不能自己编写主图方案。')
    skills=state(source,platform_tool_names=tools)
    await skills.initialize()
    runtime=ConversationTurnRuntime(conversation_id=f['conv'],task_id=f['task'],turn_id=turn,
        execution_token=f['token'],cancellation_event=asyncio.Event())
    async def create(**kwargs):
        kwargs['runtime'].skill_runtime=skills
        return skills
    monkeypatch.setattr('services.skills.runtime.create_skill_runtime',create)
    monkeypatch.setattr('services.handlers.chat.execution_engine.prepare_chat_stream',AsyncMock(return_value=p))
    route=AsyncMock(return_value={'kind':kind,'mode':'new','reuse_through_stage':0})
    monkeypatch.setattr('services.agent.image.ecommerce_planner.workflow.classify_workflow',route)
    seen=[]
    async def stream_chat(**kwargs):
        seen.append(kwargs)
        if kind=='main_images':
            assert 'ecommerce-main-images' in skills.active
            assert source.load.await_count==1
            assert any(t['function']['name']=='plan_ecommerce_images' for t in kwargs['tools'])
        yield StreamChunk(content='我自行编写五张主图方案',thinking_content='我自己拆卖点',prompt_tokens=10,completion_tokens=10)
    p.adapter.stream_chat=stream_chat
    request=ChatExecutionRequest(content=content,user_id=f['user'],conversation_id=f['conv'],task_id=f['task'],
        message_id=str(uuid4()),model_id='offline-only',context_anchor=object())
    sink=CollectingExecutionSink()
    try:
        if kind=='main_images' and not available:
            with pytest.raises(SkillBindingError,match='主图入口 Skill 无法启用'):
                await execute_chat(handler=h,request=request,runtime=runtime,sink=sink)
        else:
            result=await execute_chat(handler=h,request=request,runtime=runtime,sink=sink)
            serialized=json.dumps([part.model_dump(mode='json') for part in result.parts],ensure_ascii=False)
            if kind=='main_images':
                assert '主模型没有调用策划子 Agent' in serialized
                assert '我自行编写' not in serialized and '我自己拆卖点' not in serialized
                assert sink.text==sink.thinking==''
                assert '我自行编写' not in json.dumps(sink.blocks,ensure_ascii=False)
                assert result.content_blocks[0]['type']=='skill_step'
                with psycopg.connect(ecom_db) as c:
                    saved=c.execute("SELECT request_params->'_ecom_workflow' FROM tasks WHERE id=%s",(f['task'],)).fetchone()[0]
                    assert saved['kind']=='main_images' and saved['skill']['revision']=='v3'
            elif kind=='ordinary_image':
                assert '我自行编写' in serialized and 'ecommerce-main-images' not in skills.active
                assert sink.text=='我自行编写五张主图方案'
            else:
                assert '图片用途尚未确认' in serialized and '我自行编写' not in serialized
        assert len(seen)==model_calls and source.load.await_count==skill_loads
        route.assert_awaited_once()
        h._execute_tool_calls.assert_not_awaited()
        p.adapter.close.assert_awaited_once()
    finally:
        db.close()
