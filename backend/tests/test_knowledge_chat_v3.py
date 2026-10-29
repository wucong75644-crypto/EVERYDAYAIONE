"""Real runtime-role RLS in an isolated loopback cluster, no app credentials."""
from pathlib import Path
from uuid import uuid4
import json
import pytest
import psycopg
from pydantic import ValidationError
from tests.test_knowledge_workspace_v2 import local_db, command, document
from services.knowledge_workspace.chat import KnowledgeRun, roots
from core.exceptions import AppException
from schemas.message import GenerateRequest
from services.message_idempotency_service import MessageIdempotencyService

@pytest.fixture(scope='module')
def v3_db(local_db):
    make,actors=local_db
    with psycopg.connect(**make.admin_args) as c:
        c.execute('SET LOCAL ROLE everydayai')
        c.execute("ALTER TABLE messages ADD COLUMN conversation_id uuid, ADD COLUMN role text DEFAULT 'user', ADD COLUMN actor_id uuid, ADD COLUMN org_id uuid")
        c.execute("GRANT SELECT ON messages TO everydayai_runtime; ALTER TABLE messages ENABLE ROW LEVEL SECURITY; CREATE POLICY test_message_scope ON messages USING(actor_id=NULLIF(current_setting('app.actor_user_id',true),'')::uuid AND org_id IS NOT DISTINCT FROM NULLIF(current_setting('app.org_id',true),'')::uuid)")
        c.execute('RESET ROLE')
        c.execute((Path(__file__).resolve().parents[1]/'migrations/305_knowledge_chat_v3.sql').read_text())
        c.execute((Path(__file__).resolve().parents[1]/'migrations/306_knowledge_chat_application_role.sql').read_text())
    return make,actors

def start(make,repo,refs,input_id=None,conversation_id=None):
    input_id=input_id or str(uuid4());conversation_id=conversation_id or str(uuid4())
    with psycopg.connect(**make.admin_args) as c:
        c.execute('INSERT INTO conversations(id) VALUES(%s) ON CONFLICT DO NOTHING',(conversation_id,))
        c.execute("INSERT INTO messages(id,conversation_id,role,knowledge_refs,actor_id,org_id) VALUES(%s,%s,'user',%s,%s,%s) ON CONFLICT DO NOTHING",(input_id,conversation_id,json.dumps(refs),repo.scope.actor_user_id,repo.scope.org_id))
    return KnowledgeRun.start(repo,conversation_id=conversation_id,input_message_id=input_id),input_id,conversation_id

def ref(obj):return {'root':{'kind':'container','id':str(obj['container_id'])},'policy':'latest_saved'}

def test_strict_protocol_fingerprint():
    root={'root':{'kind':'object','id':str(uuid4())},'policy':'latest_saved'}
    kwargs={'content':[{'type':'text','text':'使用售后资料'}],'knowledge_refs':[root,root]}
    body=GenerateRequest(**kwargs);assert len(body.knowledge_refs)==1
    original=MessageIdempotencyService.build_fingerprint('c',body)
    other={'root':{'kind':'container','id':str(uuid4())},'policy':'latest_saved'}
    assert original!=MessageIdempotencyService.build_fingerprint('c',GenerateRequest(**{**kwargs,'knowledge_refs':[other]}))
    for reserved in ('actor','org','snapshot_id','authorized'):
        with pytest.raises(ValidationError):GenerateRequest(**{**kwargs,'knowledge_refs':[{**root,reserved:True}]})
    with pytest.raises(ValidationError):GenerateRequest(**{**kwargs,'knowledge_refs':[root]*9})

def test_snapshot_replay_new_turn_and_scope(v3_db):
    make,_=v3_db;r=make();obj=document(r);reference=ref(obj)
    run,mid,cid=start(make,r,[reference]);root=run.introduction()['roots'][0]['handle']
    outline=run.execute('outline',{'root_handle':root});handle=next(x['handle'] for x in outline['items'] if x['source']['url'].startswith('/knowledge?object='+str(obj['id'])))
    assert '验收关键词' in run.execute('read',{'root_handle':root,'item_handles':[handle]})['items'][0]['body']
    r.command(command('save_document',{'object_id':obj['id'],'expected_revision':2,'body':'新一轮资料'}))
    replay=KnowledgeRun.start(r,conversation_id=cid,input_message_id=mid);assert replay.id==run.id
    assert '验收关键词' in replay.execute('read',{'root_handle':root,'item_handles':[handle]})['items'][0]['body']
    fresh,_,_=start(make,r,[reference]);newroot=fresh.introduction()['roots'][0]['handle']
    assert fresh.execute('search',{'root_handle':newroot,'query':'新一轮资料'})['items'][0]['source']['revision']==3
    with pytest.raises(AppException):fresh.execute('read',{'root_handle':newroot,'item_handles':[handle]})
    assert all(str(x['id'])!=str(obj['container_id']) for x in roots(make(1))['items'])
    with pytest.raises(AppException):start(make,make(1),[reference])
    with pytest.raises(AppException):start(make,make(0,1),[reference])

def test_revoke(v3_db):
    make,actors=v3_db;owner=make();obj=document(owner)
    owner.command(command('set_member',{'container_id':obj['container_id'],'user_id':actors[1],'role':'viewer','status':'active'}))
    run,_,_=start(make,make(1),[ref(obj)]);root=run.introduction()['roots'][0]['handle']
    handle=run.execute('outline',{'root_handle':root})['items'][0]['handle']
    run.execute('read',{'root_handle':root,'item_handles':[handle]})
    owner.command(command('set_member',{'container_id':obj['container_id'],'user_id':actors[1],'role':'viewer','status':'removed'}))
    with pytest.raises(AppException):run.execute('read',{'root_handle':root,'item_handles':[handle]})
    with pytest.raises(AppException):run.revalidate()

def test_pagination_partial_read_and_cursor_tampering(v3_db):
    make,_=v3_db;r=make();obj=document(r)
    r.command(command('save_document',{'object_id':obj['id'],'expected_revision':2,'body':'规则数据'*7000}))
    run,_,_=start(make,r,[ref(obj)]);root=run.introduction()['roots'][0]['handle'];h=run.execute('outline',{'root_handle':root,'limit':1})['items'][0]['handle']
    one=run.execute('read',{'root_handle':root,'item_handles':[h]})['items'][0]
    assert one['status']=='partial' and one['next_cursor'] and len(one['body'])==8000
    two=run.execute('read',{'root_handle':root,'item_handles':[h],'cursor':one['next_cursor']})['items'][0];assert len(two['body'])==8000
    with pytest.raises(AppException):run.execute('outline',{'root_handle':root,'cursor':one['next_cursor']})
    with pytest.raises(AppException):run.execute('read',{'root_handle':root,'item_handles':[h],'cursor':one['next_cursor']+'x'})
    with r.cursor() as c:
        c.execute('SELECT state FROM kb_read_receipts WHERE snapshot_id=%s',(run.id,));assert 'body_complete' not in {row['state'] for row in c.fetchall()}

def test_child_not_a_root(v3_db):
    make,_=v3_db;r=make();obj=document(r)
    child=r.command(command('create_object',{'container_id':obj['container_id'],'parent_id':obj['id'],'kind':'document','title':'子资料','body':'不能作为总分支','position':{'x':0,'y':0}}))['object_id']
    with pytest.raises(AppException):start(make,r,[{'root':{'kind':'object','id':str(child)},'policy':'latest_saved'}])

from tests.test_knowledge_files import file_client, uploaded

def test_file_versions_fixed_reference_materialize_and_removal(v3_db,file_client,monkeypatch):
    from services.knowledge_workspace import materialize as staging
    client,ctx,settings,r,make=file_client
    monkeypatch.setattr(staging,'get_settings',lambda:settings)
    source=document(r);oid=source['id'];bid=uploaded(client,oid,'固定资料第一版')['binding_id']
    run,_,_=start(make,r,[ref(source)]);root=run.introduction()['roots'][0]['handle']
    fh=next(i['handle'] for i in run.snapshot['items'] if i['type']=='file')
    uploaded(client,oid,'新资料第二版',binding_id=bid,expected_revision='1')
    value=run.execute('read',{'root_handle':root,'item_handles':[fh],'materialize':True})['items'][0]
    assert value['body']=='固定资料第一版'
    from services.file_executor import FileExecutor
    fs=FileExecutor(settings.file_workspace_root,ctx.user_id,ctx.org_id)
    assert fs.resolve_safe_path(value['workspace_path']).read_text()=='固定资料第一版'
    assert run.materialized_assets()[0].asset_id.startswith('knowledge:')
    assert value['canonical_asset_id']!=value['attachment_ref_id']
    staging.cleanup(run)
    assert not fs.resolve_safe_path(value['workspace_path']).exists()
    assert run.materialized_assets() and fs.resolve_safe_path(value['workspace_path']).exists()
    target=document(r);tid=target['id']
    result=client.post(f'/api/knowledge/objects/{tid}/files/reference',json={'command_id':str(uuid4()),'source_object_id':str(oid),'source_binding_id':bid,'source_revision':1})
    assert result.status_code==200
    fixed,_,_=start(make,r,[ref(target)]);fr=fixed.snapshot['roots'][0]['handle'];fixed_h=next(i['handle'] for i in fixed.snapshot['items'] if i['type']=='file')
    removed=client.request('DELETE',f'/api/knowledge/objects/{oid}/files/{bid}',json={'command_id':str(uuid4()),'expected_revision':2});assert removed.status_code==200
    with pytest.raises(AppException):run.materialized_assets()
    assert fixed.execute('read',{'root_handle':fr,'item_handles':[fixed_h]})['items'][0]['body']=='固定资料第一版'


def test_reference_scope_rules_and_real_tool_handler(v3_db):
    make,_=v3_db;r=make();source=document(r)
    target_id=r.command(command('create_object',{'container_id':source['container_id'],'kind':'topic','title':'另一总分支','body':'被引用资料','position':{'x':0,'y':0}}))['object_id']
    target=r.object(target_id)
    rule=r.command(command('create_object',{'container_id':source['container_id'],'parent_id':source['id'],'kind':'rule','title':'退货规则','body':'仅支持签收后七天内退货','position':{'x':0,'y':0}}))['object_id']
    r.command(command('connect',{'source_object_id':source['id'],'target_object_id':target['id'],'relation_type':'reference'}))
    run,_,_=start(make,r,[{'root':{'kind':'object','id':str(source['id'])},'policy':'latest_saved'}]);root=run.snapshot['roots'][0]['handle']
    first=run.execute('outline',{'root_handle':root})
    source_h=next(i['handle'] for i in run.snapshot['items'] if i['id']==str(source['id']) and i['type']=='object')
    link=next(l for l in run.snapshot['links'] if l['source_object_id']==str(source['id']))
    followed=run.execute('follow_reference',{'root_handle':root,'link_handle':link['handle']})
    assert followed['item_handle'] and 'knowledge?' in followed['source']['url']
    assert len([i for i in run.snapshot['items'] if i['id']==str(target['id'])])==1
    result=run.execute('read',{'root_handle':root,'item_handles':[source_h]})
    assert result['items'][0]['rules'][0]['body']=='仅支持签收后七天内退货'
    assert not result['coverage']['full_framework_ready']
    with pytest.raises(AppException):run.execute('follow_reference',{'root_handle':root,'link_handle':str(uuid4())})


@pytest.mark.asyncio
async def test_tool_runtime_handler_and_catalog_scope(v3_db):
    from types import SimpleNamespace
    from services.agent.tool_executor import ToolExecutor
    from services.tools.catalog import build_tool_catalog
    from services.tools.runtime_context import executor_context
    from services.tools.policy import ToolPolicy
    from services.tools.legacy import LegacyAdvertisement
    make,actors=v3_db;r=make();obj=document(r);run,_,_=start(make,r,[ref(obj)])
    executor=ToolExecutor(SimpleNamespace(pool=r.pool),actors[0],str(uuid4()),org_id=r.scope.org_id,knowledge_run=run,tool_entrypoint='model')
    context=executor_context(executor);registry=build_tool_catalog();policy=ToolPolicy(registry)
    advertised=registry.resolve(context,policy=policy,advertisement=LegacyAdvertisement()).advertised
    assert 'knowledge_read' in advertised
    executor.knowledge_run=None
    denied=registry.resolve(executor_context(executor),policy=policy,advertisement=LegacyAdvertisement()).allowed
    assert 'knowledge_read' not in denied
    executor.knowledge_run=run
    result=await executor._knowledge_handler('outline')({'root_handle':run.snapshot['roots'][0]['handle']})
    assert json.loads(result.summary)['items']


def test_message_binding_cannot_be_taken_from_another_actor(v3_db):
    make,_=v3_db;r=make();obj=document(r)
    run,mid,cid=start(make,r,[ref(obj)])
    with pytest.raises(AppException):KnowledgeRun.start(make(1),conversation_id=cid,input_message_id=mid)
    with pytest.raises(AppException):KnowledgeRun.start(r,conversation_id=str(uuid4()),input_message_id=mid)
    with make(1).cursor() as c:
        c.execute('SELECT id FROM kb_read_snapshots WHERE id=%s',(run.id,));assert c.fetchone() is None


def test_actual_source_footer_and_no_false_full_read(v3_db):
    make,_=v3_db;r=make();obj=document(r)
    run,_,_=start(make,r,[ref(obj)]);root=run.snapshot['roots'][0]['handle']
    assert '尚未读取' in run.evidence_footer()
    h=run.execute('outline',{'root_handle':root})['items'][0]['handle']
    assert '尚未读取' in run.evidence_footer()
    read=run.execute('read',{'root_handle':root,'item_handles':[h]})
    footer=run.evidence_footer();assert read['items'][0]['source']['url'] in footer
    assert '本次实际读取' in footer and '版本 2' in footer


@pytest.mark.parametrize('args',[{'root_handle':{}},{'root_handle':'x','actor':'forged'},{'root_handle':'x','materialize':'false'},{'root_handle':'x','item_handles':'bad'}])
def test_model_cannot_inject_authority_or_coerce_arguments(v3_db,args):
    make,_=v3_db;r=make();obj=document(r);run,_,_=start(make,r,[ref(obj)])
    with pytest.raises(AppException):run.execute('read',args)


@pytest.mark.asyncio
async def test_plain_chat_does_not_acquire_knowledge_scope():
    from types import SimpleNamespace
    from services.knowledge_workspace.runtime import prepare_knowledge_run
    handler=SimpleNamespace(org_id=None)
    await prepare_knowledge_run(handler,object(),'legacy-user','legacy-conversation')
    assert handler._knowledge_run is None
    with pytest.raises(AppException):
        await prepare_knowledge_run(handler,object(),'legacy-user','legacy-conversation',required=True)


@pytest.mark.asyncio
async def test_plain_model_answer_and_evidence_survive_finalization(v3_db,monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from tests.test_chat_execution_engine import _request
    from services.handlers.chat.execution_engine import execute_chat
    from services.handlers.chat.execution_sink import CollectingExecutionSink
    from services.tools.runtime_context import catalog_context
    from services.conversation_turn_runtime import ConversationTurnRuntime
    make,_=v3_db;r=make();obj=document(r);run,_,_=start(make,r,[ref(obj)])
    root=run.snapshot['roots'][0]['handle'];h=run.execute('outline',{'root_handle':root})['items'][0]['handle']
    source=run.execute('read',{'root_handle':root,'item_handles':[h]})['items'][0]['source']['url']
    async def stream(**kwargs):
        yield SimpleNamespace(content='中文正文中的验收关键词',thinking_content=None,tool_calls=None,prompt_tokens=3,completion_tokens=2,credits_consumed=None,finish_reason='stop')
    prepared=SimpleNamespace(execution_context=catalog_context('org-1'),adapter=SimpleNamespace(stream_chat=stream,close=AsyncMock()),
        permission=SimpleNamespace(mode=SimpleNamespace(value='auto'),need_exit_attachment=False,get_reminder=lambda _:''),
        core_tools=[],stream_kwargs={},tool_context=SimpleNamespace(discovered_tools=set(),build_context_prompt=lambda:''),messages=[],
        budget=SimpleNamespace(stop_reason=None,turns_used=0))
    prepared.budget.use_turn=lambda:setattr(prepared.budget,'turns_used',prepared.budget.turns_used+1)
    monkeypatch.setattr('services.handlers.chat.execution_engine.prepare_chat_stream',AsyncMock(return_value=prepared))
    # Cleanup settings are separate from the real database fixture.
    monkeypatch.setattr('services.knowledge_workspace.materialize.cleanup',lambda value:None)
    audited=[]
    async def audit(candidate,facts,*args,**kwargs):
        assert candidate==['中文正文中的验收关键词']
        assert any('中文正文中的验收关键词' in fact['text'] for fact in facts)
        audited.append(candidate)
    monkeypatch.setattr('services.knowledge_workspace.grounding.audit',audit)
    handler=SimpleNamespace(org_id='org-1',_adapter=None,_knowledge_run=run,_calculate_credits=lambda u:u['completion_tokens'])
    sink=CollectingExecutionSink();sink.flush=AsyncMock(side_effect=lambda:None)
    runtime=ConversationTurnRuntime(conversation_id='conv-1',task_id='task-1',turn_id='turn-1',cancellation_event=asyncio.Event())
    result=await execute_chat(handler=handler,request=_request(),runtime=runtime,sink=sink)
    assert result.parts[0].text=='中文正文中的验收关键词'
    assert len(audited)==1
    assert source in result.parts[-1].text
    assert source in sink.text+json.dumps(sink.blocks,ensure_ascii=False)
    sink.flush.assert_awaited_once()


def test_followed_target_inherits_only_its_explicit_rules(v3_db):
    make,_=v3_db;r=make();source=document(r);cid=source['container_id']
    def create(kind,title,parent=None):
        payload={'container_id':cid,'kind':kind,'title':title,'body':title,'position':{'x':0,'y':0}}
        if parent:payload['parent_id']=parent
        return str(r.command(command('create_object',payload))['object_id'])
    target=create('topic','引用目标')
    rule=create('rule','目标专用规则',target)
    unrelated=create('document','不得越界的兄弟资料',target)
    global_rule=create('rule','空间通用规则')
    r.command(command('connect',{'source_object_id':source['id'],'target_object_id':target,'relation_type':'reference'}))
    run,_,_=start(make,r,[{'root':{'kind':'object','id':str(source['id'])},'policy':'latest_saved'}]);root=run.snapshot['roots'][0]['handle']
    assert unrelated not in {i['id'] for i in run.snapshot['items']}
    run.execute('outline',{'root_handle':root})
    sh=next(i['handle'] for i in run.snapshot['items'] if i['id']==str(source['id']))
    own=run.execute('read',{'root_handle':root,'item_handles':[sh]})['items'][0]
    assert {x['body'] for x in own['rules']}=={'空间通用规则'}
    rh=next(i['handle'] for i in run.snapshot['items'] if i['id']==rule)
    with pytest.raises(AppException):run.execute('read',{'root_handle':root,'item_handles':[rh]})
    followed=run.execute('follow_reference',{'root_handle':root,'link_handle':run.snapshot['links'][0]['handle']})
    read=run.execute('read',{'root_handle':root,'item_handles':[followed['item_handle']]})['items'][0]
    assert {x['body'] for x in read['rules']}=={'空间通用规则','目标专用规则'}


@pytest.mark.asyncio
async def test_runtime_uses_persisted_refs_and_reuses_snapshot(v3_db,monkeypatch):
    from types import SimpleNamespace
    from services.handlers.context_snapshot import ContextAnchor
    from services.knowledge_workspace.runtime import prepare_knowledge_run
    make,_=v3_db;r=make();obj=document(r);original,mid,cid=start(make,r,[ref(obj)])
    monkeypatch.setattr('api.routes.knowledge.enabled',lambda ctx:True)
    handler=SimpleNamespace(db=SimpleNamespace(pool=r.pool),org_id=r.scope.org_id,_personal_context_allowed=True)
    anchor=ContextAnchor(task_id=str(uuid4()),conversation_id=cid,turn_id=str(uuid4()),input_message_id=mid,base_revision=0,through_message_id=None,org_id=r.scope.org_id)
    await prepare_knowledge_run(handler,anchor,r.scope.actor_user_id,cid,required=True)
    assert handler._knowledge_run.id==original.id
    handler._personal_context_allowed=False
    with pytest.raises(AppException):await prepare_knowledge_run(handler,anchor,r.scope.actor_user_id,cid,required=True)
    assert handler._knowledge_run is None


@pytest.mark.asyncio
async def test_retry_restores_saved_reference_and_rejects_nonpersonal_context(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import MagicMock,Mock
    from fastapi import HTTPException
    from schemas.message import GenerationType
    from services.knowledge_workspace.message_intent import prepare_knowledge_intent
    reference={'root':{'kind':'object','id':str(uuid4())},'policy':'latest_saved'}
    body=GenerateRequest(content=[{'type':'text','text':'重试'}],operation='retry',original_message_id=str(uuid4()),params={'_knowledge_refs_required':False,'knowledge_refs':['forged']})
    db=MagicMock();q=db.table.return_value
    q.select.return_value=q;q.eq.return_value=q;q.maybe_single.return_value=q
    q.execute.side_effect=[SimpleNamespace(data={'role':'assistant','reply_to_message_id':'saved'}),SimpleNamespace(data={'knowledge_refs':[reference]}),SimpleNamespace(data={'scope_type':'user','user_id':'actor'})]
    monkeypatch.setattr('api.routes.knowledge.enabled',lambda _:True)
    validator=Mock();monkeypatch.setattr('services.knowledge_workspace.message_intent.validate',validator)
    monkeypatch.setattr('services.knowledge_workspace.message_intent.repository',lambda *args:'scoped-repository')
    await prepare_knowledge_intent(db,body,'actor','org','conversation',GenerationType.CHAT)
    assert body.params=={'_knowledge_refs_required':True}
    validator.assert_called_once_with('scoped-repository',[reference])
    q.execute.side_effect=[SimpleNamespace(data={'role':'user','knowledge_refs':[reference]}),SimpleNamespace(data={'scope_type':'channel','user_id':'actor'})]
    with pytest.raises(HTTPException) as exc:await prepare_knowledge_intent(db,body,'actor','org','conversation',GenerationType.CHAT)
    assert exc.value.status_code==403


@pytest.mark.parametrize('role',['everydayai','everydayai_runtime'])
def test_application_and_runtime_roles_read_with_same_rls(v3_db,file_client,role):
    from psycopg_pool import ConnectionPool
    from services.knowledge_workspace.repository import Repository
    client,ctx,settings,owner,make=file_client
    source=document(owner);uploaded(client,source['id'],'应用连接角色附件验证')
    with ConnectionPool(kwargs={**make.admin_args,'options':'-c role='+role},min_size=1,max_size=2) as pool:
        repo=Repository(pool,owner.scope)
        run,mid,cid=start(make,repo,[ref(source)])
        root=run.snapshot['roots'][0]['handle']
        assert run.execute('outline',{'root_handle':root})['items']
        fh=next(i['handle'] for i in run.snapshot['items'] if i['type']=='file')
        result=run.execute('read',{'root_handle':root,'item_handles':[fh]})
        assert result['items'][0]['body']=='应用连接角色附件验证'
        assert '本次实际读取' in run.evidence_footer()
        assert KnowledgeRun.start(repo,conversation_id=cid,input_message_id=mid).id==run.id
        with repo.cursor() as c:
            c.execute("SELECT current_user,has_table_privilege(current_user,'public.kb_read_snapshots','UPDATE') AS can_update,has_table_privilege(current_user,'public.kb_read_receipts','DELETE') AS can_delete")
            identity=c.fetchone();assert identity['current_user']==role
            assert not identity['can_update'] and not identity['can_delete']
            c.execute("SELECT has_table_privilege(current_user,'public.kb_read_snapshots','UPDATE') AS allowed")
            assert not c.fetchone()['allowed']
        for scope in (make(1).scope,make(0,1).scope):
            foreign=Repository(pool,scope)
            with foreign.cursor() as c:
                c.execute('SELECT id FROM kb_read_snapshots WHERE id=%s',(run.id,))
                assert c.fetchone() is None
            with pytest.raises(AppException):KnowledgeRun.start(foreign,conversation_id=cid,input_message_id=mid)
