"""Native video boundaries on isolated PostgreSQL; no model/provider/OSS IO."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
import pytest

from services.handlers.chat_image_request import canonical_hash
from services.task_execution.contracts import TaskState
from services.tool_invocation_store import hash_tool_arguments
from tests.test_task_execution_receipt_continuation_postgres import (
    future_db,scheduled_db,controls_db,continuation_db,children_db,native_result_db,
    operation_db,task_db,composition_db,ecom_db,isolated_db,seed)
from tests.test_task_execution_operations_postgres import begin,complete
from tests.test_task_execution_postgres import task_payload,save

ROOT=Path(__file__).parents[1]

@pytest.fixture(autouse=True)
def owned_host(monkeypatch):
    from core.config import settings
    monkeypatch.setattr(settings,'oss_cdn_domain','https://owned.invalid')


@pytest.fixture(scope='module')
def video_db(future_db):
    with psycopg.connect(future_db) as c:
        # Match the checked-in bootstrap catalog: original asset tables are
        # everydayai-owned, RLS enabled, not forced. No extra runtime grants.
        c.execute('ALTER TABLE user_assets OWNER TO everydayai')
        c.execute('ALTER TABLE user_asset_refs OWNER TO everydayai')
        for name in ('354_agent_task_native_writes.sql','357_agent_task_video_queue.sql','358_agent_task_video_continuations.sql'):
            try:c.execute((ROOT/'migrations'/name).read_text())
            except Exception as error:raise RuntimeError(name) from error
    yield future_db
    with psycopg.connect(future_db) as c:
        c.execute("DELETE FROM agent_task_events WHERE payload->>'source_kind'='video'")
        c.execute((ROOT/'migrations/rollback/358_agent_task_video_continuations_rollback.sql').read_text())
        c.execute("DELETE FROM tasks WHERE type='video'")
        c.execute("DELETE FROM agent_task_native_writes")
        c.execute((ROOT/'migrations/rollback/357_agent_task_video_queue_rollback.sql').read_text())
        c.execute((ROOT/'migrations/rollback/354_agent_task_native_writes_rollback.sql').read_text())


def connection(f,worker=False):
    c=psycopg.connect(psycopg.conninfo.make_conninfo(f['dsn'],user='everydayai'))
    c.execute("SELECT set_config('app.actor_user_id',%s,true),set_config('app.org_id',%s,true),set_config('app.access_kind',%s,true)",
        (f['user'],f.get('org') or '', 'worker' if worker else 'runtime'))
    return c


def rpc(f,name,worker=True,**params):
    if worker:params.setdefault('p_org_id',f.get('org'))
    query=sql.SQL('SELECT {}({})').format(sql.Identifier(name),sql.SQL(',').join(sql.SQL('{}=>%s').format(sql.Identifier(k)) for k in params))
    with connection(f,worker) as c:return c.execute(query,tuple(Jsonb(v) if isinstance(v,(dict,list)) else v for v in params.values())).fetchone()[0]


def setup(dsn,org=False):
    f=seed(dsn);f['org']=str(uuid4()) if org else None
    if org:
        with psycopg.connect(dsn) as c:
            c.execute('INSERT INTO organizations(id) VALUES(%s)',(f['org'],))
            c.execute('INSERT INTO org_members(org_id,user_id) VALUES(%s,%s)',(f['org'],f['user']))
            c.execute('UPDATE conversations SET org_id=%s WHERE id=%s',(f['org'],f['conv']))
            c.execute('UPDATE tasks SET org_id=%s WHERE id=%s',(f['org'],f['task']))
            c.execute('UPDATE messages SET org_id=%s WHERE id=%s',(f['org'],f['message']))
    raw=task_payload(f)['_task_execution'];raw['image_binding_version']=3;raw['org_id']=f['org']
    raw['requirements'][0]['artifact_kind']='file'
    raw['steps']=[{'id':'video','title':'真实视频','capability':'platform.generate_video','requirement_ids':['report']}]
    state=TaskState.model_validate(raw)
    with connection(f) as c:
        save(c,f,{'messages':[{'role':'system','content':'原记忆'}],'opaque_context':{'keep':True},'_task_execution':state.model_dump(mode='json')})
        started=begin(c,f,state.revision,call='video-1',tool='generate_video',capability='platform.generate_video',step='video',args_hash=hash_tool_arguments({'prompt':'桌面文具视频'}))
    state=TaskState.model_validate(started['state'])
    origin={'parent_task_id':f['task'],'logical_task_id':f['task'],'actor_user_id':f['user'],'org_id':f['org'],
        'workspace_owner_id':f['user'],'context_scope':'user','conversation_id':f['conv'],'turn_id':f['turn'],
        'input_message_id':f['message'],'base_context_revision':0,'tool_call_id':'video-1','args_hash':state.operations[0].arguments_sha256}
    snap={'schema_version':1,'mode':'text_to_video','model':'sora-2-text-to-video','prompt':'桌面文具视频','duration_seconds':10,
        'aspect_ratio':'landscape','remove_watermark':True,'references':[],'estimated_credits':10,'estimated_provider_credits':1,
        'budget':{'max_requests':2,'max_credits':20},'origin':origin}
    snap['request_hash']=canonical_hash(snap)
    accepted=rpc(f,'accept_agent_video_request',False,p_parent_task_id=f['task'],p_execution_token=f['token'],p_snapshot=snap,p_org_id=f['org'])
    f.update(child=accepted['task_id'],assistant=accepted['message_id'],snapshot=snap,state=state)
    return f


def claim(f,token=None):
    token=token or str(uuid4())
    return token,rpc(f,'claim_agent_video_submission',p_task_id=f['child'],p_claim_token=token,p_lease_seconds=60)


def dispatch(f):
    token,result=claim(f);assert result['outcome']=='claimed'
    assert rpc(f,'mark_agent_video_dispatch',p_task_id=f['child'],p_claim_token=token)['outcome']=='dispatch'
    return token


def test_migration_invoker_no_public_and_bound_snapshot(video_db):
    f=setup(video_db)
    replay=rpc(f,'accept_agent_video_request',False,p_parent_task_id=f['task'],p_execution_token=f['token'],p_snapshot=f['snapshot'])
    assert replay['task_id']==f['child'] and replay['outcome']=='replay'
    with connection(f) as c:
        row=c.execute('SELECT delivery_context FROM tasks WHERE id=%s',(f['child'],)).fetchone()[0]
        assert row['agent_video'] is True
        with pytest.raises(psycopg.Error,match='SNAPSHOT_IMMUTABLE'):
            with c.transaction():c.execute("UPDATE tasks SET model_id='another' WHERE id=%s",(f['child'],))
        funcs=c.execute("SELECT prosecdef,has_function_privilege('image_untrusted',oid,'EXECUTE') FROM pg_proc WHERE proname LIKE '%agent_video%' AND pronamespace='public'::regnamespace").fetchall()
        assert funcs and all(row==(False,False) for row in funcs)


def test_credits_reclaim_once_and_dispatch_nonce_never_reissues(video_db):
    f=setup(video_db);first,result=claim(f);assert result['outcome']=='claimed'
    assert claim(f)[1]['outcome']=='not_queued'
    with psycopg.connect(video_db) as c:
        c.execute("UPDATE tasks SET request_params=jsonb_set(request_params,'{_agent_video_lifecycle_v1,lease_expires_at}',to_jsonb(now()-interval '1 second')) WHERE id=%s",(f['child'],))
    token,result=claim(f);assert result['outcome']=='claimed'
    assert rpc(f,'mark_agent_video_dispatch',p_task_id=f['child'],p_claim_token=first)['outcome']=='not_owned'
    assert rpc(f,'mark_agent_video_dispatch',p_task_id=f['child'],p_claim_token=token)['outcome']=='dispatch'
    assert rpc(f,'mark_agent_video_dispatch',p_task_id=f['child'],p_claim_token=token)['outcome']=='not_owned'
    assert rpc(f,'mark_agent_video_uncertain',p_task_id=f['child'],p_claim_token=token)['outcome']=='uncertain'
    assert claim(f)[1]['outcome']=='not_queued'
    with connection(f) as c:
        assert c.execute('SELECT credits FROM users WHERE id=%s',(f['user'],)).fetchone()[0]==90
        assert c.execute('SELECT count(*) FROM credit_transactions WHERE task_id=%s',(f['child'],)).fetchone()[0]==1


def test_lost_acceptance_callback_binds_only_original_claim_and_result(video_db):
    f=setup(video_db);token=dispatch(f)
    assert rpc(f,'bind_agent_video_submission',p_task_id=f['child'],p_claim_token=str(uuid4()),p_external_task_id='external-video')['outcome']=='not_owned'
    rpc(f,'mark_agent_video_uncertain',p_task_id=f['child'],p_claim_token=token)
    assert rpc(f,'bind_agent_video_submission',p_task_id=f['child'],p_claim_token=token,p_external_task_id='external-video')['outcome']=='accepted'
    result={'task_id':'external-video','status':'failed','error':'provider failed'}
    rpc(f,'record_agent_video_result',p_task_id=f['child'],p_external_task_id='external-video',p_result=result)
    with pytest.raises(psycopg.Error):rpc(f,'record_agent_video_result',p_task_id=f['child'],p_external_task_id='wrong',p_result=result)
    for _ in range(2):
        rpc(f,'publish_agent_video',p_task_id=f['child'],p_content=[{'type':'text','text':'生成失败'}],p_status='failed',p_error='provider failed')
    with connection(f) as c:
        assert c.execute('SELECT credits FROM users WHERE id=%s',(f['user'],)).fetchone()[0]==100
        assert c.execute('SELECT status FROM credit_transactions WHERE task_id=%s',(f['child'],)).fetchone()[0]=='refunded'
        original=c.execute('SELECT input_message_id,turn_id,base_context_revision FROM tasks WHERE id=%s',(f['task'],)).fetchone()
        assert tuple(map(str,original[:2]))==(f['message'],f['turn']) and original[2]==0


def test_uncertain_deadline_refunds_once_without_authorizing_another_create(video_db):
    f=setup(video_db);token=dispatch(f)
    rpc(f,'mark_agent_video_uncertain',p_task_id=f['child'],p_claim_token=token)
    params=dict(p_task_id=f['child'],p_content=[{'type':'text','text':'无法核实，已退款'}],p_status='failed',p_platform_pays=True)
    with pytest.raises(psycopg.Error,match='NOT_DUE'):rpc(f,'publish_agent_video',**params)
    with psycopg.connect(video_db) as c:c.execute("UPDATE tasks SET request_params=jsonb_set(request_params,'{_agent_video_lifecycle_v1,uncertain_deadline}',to_jsonb(now()-interval '1 second')) WHERE id=%s",(f['child'],))
    for _ in range(2):rpc(f,'publish_agent_video',**params)
    assert claim(f)[1]['outcome']=='not_queued'
    with connection(f) as c:
        assert c.execute('SELECT credits FROM users WHERE id=%s',(f['user'],)).fetchone()[0]==100
        assert c.execute("SELECT request_params#>>'{_media_platform_cost_v1,evidence}' FROM tasks WHERE id=%s",(f['child'],)).fetchone()[0]=='unconfirmed'


def test_success_without_owned_asset_is_rejected(video_db):
    f=setup(video_db);token=dispatch(f)
    rpc(f,'bind_agent_video_submission',p_task_id=f['child'],p_claim_token=token,p_external_task_id='ok-video')
    rpc(f,'record_agent_video_result',p_task_id=f['child'],p_external_task_id='ok-video',p_result={'task_id':'ok-video','status':'success','video_url':'https://temporary.invalid/out.mp4'})
    with pytest.raises(psycopg.Error,match='PERSISTENCE_UNCONFIRMED'):
        rpc(f,'publish_agent_video',p_task_id=f['child'],p_content=[{'type':'video','url':'https://temporary.invalid/out.mp4','asset_id':str(uuid4())}],p_status='completed')


def test_rollback_refuses_other_tenant_facts_and_keeps_force_rls(video_db):
    setup(video_db)
    with psycopg.connect(video_db) as c:
        with pytest.raises(psycopg.Error,match='NOT_DRAINED'):
            with c.transaction():c.execute((ROOT/'migrations/rollback/357_agent_task_video_queue_rollback.sql').read_text())
        assert c.execute("SELECT relforcerowsecurity FROM pg_class WHERE oid='tasks'::regclass").fetchone()[0]


def register_output(f):
    from services.assets.asset_registry import AssetRegistryService,ReadyAssetDraft,AssetRefDraft
    def call(name,params):return SimpleNamespace(execute=lambda:SimpleNamespace(data=rpc(f,name,**params)))
    output={'url':'https://owned.invalid/'+f['child']+'.mp4','content_sha256':'a'*64,'etag':'own-etag','size':42,
        'object_key':'personal/test/'+f['child']+'.mp4','name':f['child']+'.mp4','mime_type':'video/mp4'}
    asset=AssetRegistryService(SimpleNamespace(rpc=call)).register_ready_asset(
        ReadyAssetDraft(org_id=None,storage_scope='user',storage_owner_key=f['user'],media_type='video',original_url=output['url'],
            download_url=output['url'],name=output['name'],size=42,content_sha256=output['content_sha256']),
        AssetRefDraft(ref_key='task:'+f['child']+':0',actor_user_id=f['user'],source_type='generated',source_kind='video_task',ref_kind='task',
            conversation_id=f['conv'],source_task_id=f['child'],source_message_id=f['assistant'],content_index=0))
    output['asset_id']=asset['asset']['id']
    rpc(f,'record_agent_video_output',p_task_id=f['child'],p_output=output)
    return output


def publish_success(f):
    token=dispatch(f)
    rpc(f,'bind_agent_video_submission',p_task_id=f['child'],p_claim_token=token,p_external_task_id='success-'+f['child'])
    rpc(f,'record_agent_video_result',p_task_id=f['child'],p_external_task_id='success-'+f['child'],
        p_result={'task_id':'success-'+f['child'],'status':'success','video_url':'https://provider.invalid/temp.mp4'})
    output=register_output(f)
    content=[{'type':'video','url':output['url'],'asset_id':output['asset_id'],'content_sha256':output['content_sha256']}]
    rpc(f,'publish_agent_video',p_task_id=f['child'],p_content=content,p_status='completed')
    return output


def test_real_asset_publication_confirms_original_ledger_and_bound_read_proof(video_db):
    from services.task_execution.video_operations import validate_proof
    f=setup(video_db);output=publish_success(f)
    proof=rpc(f,'read_agent_task_video',False,p_task_id=f['task'],p_execution_token=f['token'],p_logical_task_id=f['task'],
        p_expected_revision=f['state'].revision,p_operation_id=f['state'].operations[0].id)['proof']
    assert validate_proof(f['state'],f['state'].operations[0],proof)=='completed'
    assert proof['video']['output']==output
    with connection(f) as c:
        assert c.execute('SELECT status FROM credit_transactions WHERE task_id=%s',(f['child'],)).fetchone()[0]=='confirmed'
        assert c.execute('SELECT credits FROM users WHERE id=%s',(f['user'],)).fetchone()[0]==90
        assert c.execute('SELECT input_message_id,turn_id,base_context_revision FROM tasks WHERE id=%s',(f['task'],)).fetchone()[2]==0


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['normal','paused','cancelled','new_input'])
async def test_video_receipt_uses_original_actor_queue_and_preserves_frozen_context(video_db,mode):
    from tests.test_task_execution_receipt_continuation_postgres import claim_internal
    from services.task_execution.receipt_continuation import continuation_payload
    f=setup(video_db);publish_success(f)
    with connection(f) as c:
        result=complete(c,f,f['state'].model_dump(mode='json'),status='accepted',receipt='native-video:'+f['child'])
        state=TaskState.model_validate(result['state'])
        waiting=state.model_copy(update={'status':'waiting_dependencies','revision':state.revision+1})
        history=[{'role':'system','content':'原摘要和长期记忆'},{'role':'user','content':state.original_goal},
            {'role':'assistant','tool_calls':[{'id':'video-1','type':'function','function':{'name':'generate_video','arguments':'{}'}}]},
            {'role':'tool','tool_call_id':'video-1','content':'已受理，不是成果'}]
        payload={'messages':history,'opaque_context':{'keep':True},'next_model_round':4,'content_blocks':[],
            '_task_execution':waiting.model_dump(mode='json')}
        save(c,f,payload,expected=state.revision)
    assistant=str(uuid4())
    with psycopg.connect(video_db) as c:
        c.execute("UPDATE messages SET status='completed',context_revision=1,turn_id=%s WHERE id=%s",(f['turn'],f['message']))
        c.execute("INSERT INTO messages(id,conversation_id,role,status,content,context_revision,turn_id,reply_to_message_id) VALUES(%s,%s,'assistant','completed','[]',2,%s,%s)",(assistant,f['conv'],f['turn'],f['message']))
        c.execute("UPDATE tasks SET status='completed',assistant_message_id=%s WHERE id=%s",(assistant,f['task']))
    if mode in {'paused','cancelled'}:rpc(f,'control_agent_task',False,p_root_task_id=f['task'],p_user_id=f['user'],p_org_id=None,p_action='pause' if mode=='paused' else 'cancel')
    if mode=='new_input':
        with psycopg.connect(video_db) as c:c.execute("INSERT INTO messages(id,conversation_id,role,content) VALUES(%s,%s,'user','新的独立任务')",(str(uuid4()),f['conv']))
    if mode!='normal':
        queued=rpc(f,'refresh_agent_task_video_events',False,p_source_task_id=f['task'])
        assert queued['outcome'] in {'waiting','obsolete'}
        return
    from core.local_db import AsyncLocalDBClient
    from core.db_scope import AsyncScopedDatabaseClient,DatabaseScope,DatabaseAccessKind
    from services.task_execution.continuation_consumer import TaskContinuationConsumer
    client=AsyncLocalDBClient(psycopg.conninfo.make_conninfo(f['dsn'],user='everydayai'),min_size=1,max_size=1)
    await client.open()
    try:
        worker=AsyncScopedDatabaseClient(client,DatabaseScope(None,None,DatabaseAccessKind.WORKER))
        assert await TaskContinuationConsumer(worker).scan_once()==1
        assert await TaskContinuationConsumer(worker).scan_once()==0
        async with client._pool.connection() as conn:
            cursor=await conn.execute("SELECT current_setting('app.actor_user_id',true) actor,current_setting('app.access_kind',true) access")
            row=await cursor.fetchone();assert not row['actor'] and not row['access']
    finally:await client.close()
    with connection(f) as c:
        queued_id=str(c.execute("SELECT id FROM tasks WHERE conversation_id=%s AND delivery_context->>'source_kind'='video'",(f['conv'],)).fetchone()[0])
    task,claim_=claim_internal(f,queued_id)
    bundle=rpc(f,'load_agent_receipt_continuation',False,p_task_id=claim_.task_id,p_execution_token=claim_.execution_token)
    assert bundle['outcome']=='found',bundle
    scope=SimpleNamespace(actor_user_id=f['user'],**state.execution_identity.model_dump())
    candidate,current=continuation_payload(bundle,task=task,claim=claim_,execution_scope=scope)
    assert candidate['messages'][:-1]==history and candidate['opaque_context']=={'keep':True}
    assert current.requirements==state.requirements and current.original_goal==state.original_goal
    applied=rpc(f,'apply_agent_receipt_continuation',False,p_task_id=claim_.task_id,p_execution_token=claim_.execution_token,
        p_expected_revision=waiting.revision,p_payload=candidate)
    assert applied['outcome']=='saved',applied
    with connection(f) as c:
        assert c.execute("SELECT count(*) FROM tasks WHERE conversation_id=%s AND delivery_context->>'source_kind'='video'",(f['conv'],)).fetchone()[0]==1


def test_organization_disabled_after_claim_cannot_dispatch(video_db):
    f=setup(video_db,org=True);token,result=claim(f);assert result['outcome']=='claimed'
    with psycopg.connect(video_db) as c:c.execute("UPDATE organizations SET status='inactive' WHERE id=%s",(f['org'],))
    assert rpc(f,'mark_agent_video_dispatch',p_task_id=f['child'],p_claim_token=token)['outcome']=='identity_denied'
    with connection(f,True) as c:
        assert not c.execute("SELECT request_params->'_agent_video_lifecycle_v1' ? 'dispatch_started_at' FROM tasks WHERE id=%s",(f['child'],)).fetchone()[0]
    rpc(f,'fail_agent_video_before_dispatch',p_task_id=f['child'],p_claim_token=token,p_error='组织已停用，未发送')
    rpc(f,'publish_agent_video',p_task_id=f['child'],p_content=[{'type':'text','text':'未发送，已退款'}],p_status='failed')
    with connection(f,True) as c:assert c.execute('SELECT credits FROM users WHERE id=%s',(f['user'],)).fetchone()[0]==100


@pytest.mark.asyncio
async def test_real_media_tool_lifecycle_admits_once_and_replays_receipt_without_second_child(video_db):
    from dataclasses import replace
    from unittest.mock import AsyncMock
    from services.media_tool_executor import MediaToolMixin
    from services.task_execution.operation_store import TaskOperationStore
    from services.tools import ToolCall,ToolDispatcher,ToolExecutionService
    from services.tools.catalog import build_capability_catalog
    from tests.test_task_execution_reach_operations import case,execute
    f=seed(video_db);f['org']=None
    runtime,handler,ctx,_,_,_,_=case()
    raw=task_payload(f)['_task_execution'];raw.update(image_binding_version=3,steps=[{'id':'video','title':'生成视频','capability':'platform.generate_video','requirement_ids':['report']}])
    raw['requirements'][0]['artifact_kind']='file';state=TaskState.model_validate(raw)
    runtime.task_execution_state=state;runtime.task_id=f['task'];runtime.replay_save_sequence=0
    handler._actor_turn_id=f['turn'];handler._actor_execution_token=f['token']
    ctx=replace(ctx,org_id=None,actor_user_id=f['user'],workspace_owner_id=f['user'],conversation_id=f['conv'],task_id=f['task'],permission_mode='auto',feature_flags={})
    with connection(f) as c:save(c,f,{'messages':[{'role':'system','content':'原上下文'}],'opaque_context':{'keep':True},'_task_execution':state.model_dump(mode='json')})
    def sync_call(name,params):return SimpleNamespace(execute=lambda:SimpleNamespace(data=rpc(f,name,False,**params)))
    async def async_rpc(name,params):return sync_call(name,params).execute()
    registry=build_capability_catalog()
    runtime.task_operation_store=TaskOperationStore(SimpleNamespace(rpc=lambda n,p:SimpleNamespace(execute=lambda:async_rpc(n,p))),task_id=f['task'],execution_token=f['token'],turn_id=f['turn'],registry=registry)
    class Parent:
        def select(self,*_):return self
        def eq(self,*_):return self
        def single(self):return self
        def execute(self):
            with connection(f) as c:return SimpleNamespace(data=c.execute('SELECT to_jsonb(t) FROM tasks t WHERE id=%s',(f['task'],)).fetchone()[0])
    async def native(args):
        owner=SimpleNamespace(_task_execution_state=runtime.task_execution_state,_task_id=f['task'],image_execution_token=f['token'],org_id=None,
            db=SimpleNamespace(rpc=sync_call,table=lambda _:Parent()))
        return await MediaToolMixin._generate_video(owner,args)
    io=AsyncMock(side_effect=native);service=ToolExecutionService(registry,ToolDispatcher({('legacy','generate_video'):io}))
    call=ToolCall('actual-video','generate_video',{'prompt':'原始文具桌面视频提示词'})
    planning=await execute(runtime,handler,replace(ctx,permission_mode='plan'),call,service)
    assert planning.status!='success' and not runtime.task_execution_state.operations
    io.assert_not_awaited()
    result=await execute(runtime,handler,ctx,call,service)
    assert result.status=='success',repr(result.exception)
    assert runtime.task_execution_state.operations[-1].status=='accepted'
    second=await execute(runtime,handler,replace(ctx,call_id='another-video'),ToolCall('another-video',call.name,dict(call.arguments)),service)
    assert second.execution.replayed;io.assert_awaited_once()
    with connection(f) as c:
        assert c.execute("SELECT count(*) FROM tasks WHERE conversation_id=%s AND type='video'",(f['conv'],)).fetchone()[0]==1
        assert c.execute('SELECT credits FROM users WHERE id=%s',(f['user'],)).fetchone()[0]==100
        saved=c.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s',(f['task'],)).fetchone()[0]
        assert saved['opaque_context']=={'keep':True} and saved['messages']==[{'role':'system','content':'原上下文'}]
