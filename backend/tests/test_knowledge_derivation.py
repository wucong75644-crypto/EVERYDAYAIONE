"""Derived knowledge runs against a disposable loopback database, never production."""
from pathlib import Path
from uuid import uuid4
import asyncio
import psycopg
import pytest
from psycopg.types.json import Jsonb
from tests.test_knowledge_attachment_cleanup import cleanup_db,resize_db,manual_db,summary_db,geometry_db,groups_db,editing_db,entry_db,rev,directional_db,structure_db,current,visual_db,tools_db,payload,local_db,command,layout_db,before_db,create,toggle,theme_db,rich_db
from services.knowledge_workspace.derivation import process

@pytest.fixture(autouse=True)
def isolated_model_configuration(monkeypatch):
 """Unit tests never inherit a fake or real provider credential from the suite."""
 from core.config import get_settings
 monkeypatch.setattr(get_settings(),'dashscope_api_key',None)


@pytest.fixture(scope='module')
def derived_db(cleanup_db):
 make,actors=cleanup_db
 with psycopg.connect(**make.admin_args) as c:
  c.execute("SET LOCAL ROLE everydayai; ALTER TABLE messages ADD COLUMN conversation_id uuid, ADD COLUMN role text DEFAULT 'user', ADD COLUMN actor_id uuid, ADD COLUMN org_id uuid; GRANT SELECT ON messages TO everydayai_runtime; ALTER TABLE messages ENABLE ROW LEVEL SECURITY; CREATE POLICY test_message_scope ON messages USING(actor_id=NULLIF(current_setting('app.actor_user_id',true),'')::uuid AND org_id IS NOT DISTINCT FROM NULLIF(current_setting('app.org_id',true),'')::uuid); RESET ROLE")
  for name in ['305_knowledge_chat_v3.sql','306_knowledge_chat_application_role.sql','331_knowledge_derivations.sql']:c.execute((Path(__file__).resolve().parents[1]/'migrations'/name).read_text())
 return make,actors

async def fake(sources,title,path):
 return {'sections':[{'title':'适用与步骤','text':sources[0]['text'],'source_refs':[sources[0]['key']]}] if sources else [],'tags':['售后'] if sources else []}

def upload(r,n,text='缺件先核对订单，再补寄'):
 return r.file_command(uuid4(),'file_upload',{'object_id':str(n['id']),'version_id':str(uuid4()),'name':'处理.txt','mime_type':'text/plain','size':len(text),'sha256':'a'*64,'text_status':'ready','extracted_text':text})

def document(r,oid):
 with r.cursor() as c:c.execute('SELECT public.kb_derived_document(%s) AS value',(oid,));return c.fetchone()['value']

def test_derive_keeps_manual_versions_and_canvas_history(derived_db):
 r=derived_db[0]();n=create(r);r.command(command('save_document',{'object_id':n['id'],'expected_revision':1,'body':'人工说明'}));upload(r,n)
 before=r.object(n['id']);history=r.canvas_history();gid=asyncio.run(process(r,n['id'],fake));assert gid
 after=document(r,n['id']);assert after['sections'][0]['text']=='人工说明';assert after['tags']==['售后'];assert after['chunks'];assert r.object(n['id'])==before;assert r.canvas_history()==history

def test_source_change_hides_old_generation_and_lease_cannot_publish(derived_db):
 r=derived_db[0]();n=create(r);upload(r,n);asyncio.run(process(r,n['id'],fake));assert document(r,n['id'])['generation_id']
 with r.cursor() as c:
  c.execute('SELECT public.kb_enqueue_derivation(%s)',(n['id'],));c.execute('SELECT public.kb_claim_derivation(%s) AS v',(n['id'],));claim=c.fetchone()['v']
 upload(r,n,'新版不同内容');assert document(r,n['id'])['generation_id'] is None
 with r.cursor() as c:
  c.execute('SELECT public.kb_publish_derivation(%s,%s,%s,%s,%s) AS v',(n['id'],claim['token'],claim['dirty'],claim['fingerprint'],Jsonb({})));assert c.fetchone()['v'] is None

def test_permissions_and_manual_tags_remain_isolated(derived_db):
 make,_=derived_db;r=make();n=create(r);upload(r,n);asyncio.run(process(r,n['id'],fake));assert document(make(1),n['id']) is None
 with pytest.raises(Exception,match='KB_ACCESS_DENIED'):
  with make(1).cursor() as c:c.execute('SELECT public.kb_set_knowledge_override(%s,%s,%s,0)',(n['id'],'tag:售后',Jsonb({'hidden':True})))
 with r.cursor() as c:c.execute('SELECT public.kb_set_knowledge_override(%s,%s,%s,0)',(n['id'],'tag:售后',Jsonb({'hidden':True})))
 assert document(r,n['id'])['overrides']['tag:售后']['hidden'];assert not current(r,n['id'])['canvas_data'].get('labels')

def test_ancestor_jobs_and_rollup(derived_db):
 r=derived_db[0]();root=create(r);leaf=root;chain=[root]
 for i in range(5):leaf=create(r,root['container_id'],leaf['id'],f'层级{i}');chain.append(leaf)
 upload(r,leaf)
 for n in reversed(chain):assert asyncio.run(process(r,n['id'],fake))
 assert document(r,root['id'])['sections'];assert document(r,root['id'])['tags']==['售后'];assert document(r,root['id'])['coverage']=='complete'

def test_search_and_snapshot_scope_context_and_budget(derived_db,monkeypatch):
 from core.config import get_settings
 monkeypatch.setattr(get_settings(),'knowledge_derivation_enabled',True)
 from tests.test_knowledge_chat_v3 import start,ref
 r=derived_db[0]();root=create(r,title='拼多多售后');leaf=create(r,root['container_id'],root['id'],'漏发');upload(r,leaf);asyncio.run(process(r,leaf['id'],fake));asyncio.run(process(r,root['id'],fake))
 hits=r.search(root['container_id'],'缺件');assert any(h['id']==leaf['id'] for h in hits['items'])
 run,_,_=start(derived_db[0],r,[ref(root)]);handle=run.introduction()['roots'][0]['handle']
 outlined=run.execute('outline',{'root_handle':handle});scope=next(i['scope_handle'] for i in outlined['items'] if i['title']=='拼多多售后')
 found=run.execute('search',{'root_handle':handle,'scope_handle':scope,'query':'漏发','mode':'route'});assert found['items'];assert all('body' not in i for i in found['items'])
 evidence=run.execute('search',{'root_handle':handle,'scope_handle':scope,'query':'缺件'});generated=next(i for i in evidence['items'] if i['type']=='generated')
 read=run.execute('read',{'root_handle':handle,'item_handles':[generated['handle']],'context':True});assert '核对订单' in read['items'][0]['body']
 assert run.execute('search',{'root_handle':handle,'query':'缺件'})['items']
 assert run.execute('search',{'root_handle':handle,'query':'缺件'})['items']
 assert run.execute('search',{'root_handle':handle,'query':'缺件'})['stop_reason']=='no_new_evidence'
 with pytest.raises(Exception):run.execute('search',{'root_handle':handle,'scope_handle':str(uuid4()),'query':'缺件'})

def test_generation_history_and_removed_dependency(derived_db):
 r=derived_db[0]();n=create(r);binding=upload(r,n);old=asyncio.run(process(r,n['id'],fake));before=document(r,n['id'])
 r.command(command('save_document',{'object_id':n['id'],'expected_revision':1,'body':'新增人工内容'}));assert document(r,n['id'])['generation_id'] is None
 with r.cursor() as c:c.execute('SELECT public.kb_derived_document(%s,%s) AS v',(n['id'],old));assert c.fetchone()['v']['generation_id']==str(old)
 r.file_command(uuid4(),'file_remove',{'object_id':str(n['id']),'binding_id':binding['binding_id'],'expected_revision':1})
 with r.cursor() as c:c.execute('SELECT public.kb_derived_document(%s,%s) AS v',(n['id'],old));assert c.fetchone()['v']['generation_id'] is None
 assert before['sections']

def test_override_exact_retry_and_version_conflict(derived_db):
 r=derived_db[0]();n=create(r);upload(r,n);asyncio.run(process(r,n['id'],fake));section=document(r,n['id'])['sections'][0]['id'];key=uuid4();args=(n['id'],section,Jsonb({'text':'人工改正'}),0,key)
 with r.cursor() as c:c.execute('SELECT public.kb_set_knowledge_override(%s,%s,%s,%s,%s) AS v',args);assert c.fetchone()['v']==1
 with r.cursor() as c:c.execute('SELECT public.kb_set_knowledge_override(%s,%s,%s,%s,%s) AS v',args);assert c.fetchone()['v']==1
 with pytest.raises(Exception,match='KB_VERSION_CONFLICT'):
  with r.cursor() as c:c.execute('SELECT public.kb_set_knowledge_override(%s,%s,%s,0)',(n['id'],section,Jsonb({'text':'覆盖'})))
 assert document(r,n['id'])['overrides'][section]['text']=='人工改正'

def test_changed_source_during_model_cannot_publish(derived_db):
 r=derived_db[0]();n=create(r);upload(r,n)
 async def changing(sources,title,path):
  upload(r,n,'请求期间上传的新版')
  return await fake(sources,title,path)
 assert asyncio.run(process(r,n['id'],changing)) is None
 assert document(r,n['id'])['generation_id'] is None
 assert asyncio.run(process(r,n['id'],fake))


def test_only_one_live_claim_and_expired_lease_fenced(derived_db):
 r=derived_db[0]();n=create(r);upload(r,n)
 with r.cursor() as c:
  c.execute('SELECT public.kb_claim_derivation(%s) AS v',(n['id'],));first=c.fetchone()['v'];assert first
 with r.cursor() as c:
  c.execute('SELECT public.kb_claim_derivation(%s) AS v',(n['id'],));assert c.fetchone()['v'] is None
 # A new source invalidates the old lease and creates a separately fenced claim.
 upload(r,n,'新来源')
 with r.cursor() as c:
  c.execute('SELECT public.kb_claim_derivation(%s) AS v',(n['id'],));second=c.fetchone()['v'];assert second['token']!=first['token']
  c.execute('SELECT public.kb_fail_derivation(%s,%s,%s)',(n['id'],first['token'],'late failure'))
 with r.cursor() as c:
  c.execute('SELECT public.kb_claim_derivation(%s) AS v',(n['id'],));assert c.fetchone()['v'] is None


def test_correction_does_not_survive_removed_source_authorization(derived_db):
 r=derived_db[0]();n=create(r);binding=upload(r,n);asyncio.run(process(r,n['id'],fake));section=document(r,n['id'])['sections'][0]['id']
 with r.cursor() as c:c.execute('SELECT public.kb_set_knowledge_override(%s,%s,%s,0)',(n['id'],section,Jsonb({'text':'来源对应的人工更正'})))
 r.file_command(uuid4(),'file_remove',{'object_id':str(n['id']),'binding_id':binding['binding_id'],'expected_revision':1})
 assert section not in document(r,n['id'])['overrides']


def test_model_output_is_bounded_and_refs_are_allowlisted():
 from services.knowledge_workspace.chunking import validate_output
 with pytest.raises(ValueError):validate_output({'sections':[{'title':'跨权限','text':'秘密','source_refs':['unknown']}],'tags':[]},[])
 with pytest.raises(ValueError):validate_output({'sections':[],'tags':['x'*41]},[])


def test_read_budget_and_deadline_persist(derived_db,monkeypatch):
 from core.config import get_settings
 from tests.test_knowledge_chat_v3 import start,ref
 monkeypatch.setattr(get_settings(),'knowledge_derivation_enabled',True)
 r=derived_db[0]();n=create(r);run,_,_=start(derived_db[0],r,[ref(n)])
 monkeypatch.setattr(get_settings(),'knowledge_retrieval_timeout',0)
 result=run.execute('outline',{'root_handle':run.introduction()['roots'][0]['handle']})
 assert result['stop_reason']=='budget_exhausted'


def test_unsupported_media_reports_partial_instead_of_inventing_text(derived_db):
 r=derived_db[0]();n=create(r)
 r.file_command(uuid4(),'file_upload',{'object_id':str(n['id']),'version_id':str(uuid4()),'name':'资料.png','mime_type':'image/png','size':10,'sha256':'b'*64,'text_status':'unsupported','extracted_text':None})
 asyncio.run(process(r,n['id'],fake));data=document(r,n['id'])
 assert data['coverage']=='partial';assert data['sections']==[]


def test_fifty_levels_enqueue_to_actual_root(derived_db):
 r=derived_db[0]();root=create(r);leaf=root;ids=[root['id']]
 for i in range(49):
  leaf=create(r,root['container_id'],leaf['id'],f'第{i+2}层');ids.append(leaf['id'])
 upload(r,leaf)
 # Claims prove every real ancestor was queued without a fixed business depth.
 for oid in ids:
  with r.cursor() as c:
   c.execute('SELECT public.kb_claim_derivation(%s) AS v',(oid,));assert c.fetchone()['v'] is not None


def test_runtime_cannot_select_derived_payload_tables(derived_db):
 r=derived_db[0]()
 for table in ['kb_derived_generations','kb_knowledge_overrides','kb_derivation_jobs','kb_derived_heads']:
  with pytest.raises(psycopg.errors.InsufficientPrivilege):
   with r.cursor() as c:c.execute('SELECT * FROM public.'+table)


def test_routing_expands_one_parent_but_never_escapes_selected_root():
 from services.knowledge_workspace.retrieval_policy import scoped,rank
 items=[{'id':'a','handle':'ha','type':'object','parent_id':None}, {'id':'b','handle':'hb','type':'object','parent_id':'a'}, {'id':'c','handle':'hc','type':'object','parent_id':'b'}]
 local,_=scoped(items,'hc',False);assert [i['id'] for i in local]==['c']
 wider,_=scoped(items,'hc',True);assert {i['id'] for i in wider}=={'b','c'}
 root,_=scoped(items,'ha',True);assert {i['id'] for i in root}=={'a','b','c'}
 with pytest.raises(ValueError):scoped(items,'unselected-root',True)
 assert rank('拼多多缺件','淘宝售后','缺件补寄','淘宝')==0
 assert rank('拼多多缺件','拼多多售后','缺件补寄','拼多多')>0


def test_generated_rule_summary_is_not_an_explicit_rule(derived_db,monkeypatch):
 from core.config import get_settings
 from tests.test_knowledge_chat_v3 import start,ref
 monkeypatch.setattr(get_settings(),'knowledge_derivation_enabled',True)
 r=derived_db[0]();n=create(r)
 rule=r.command(command('create_object',{'container_id':n['container_id'],'parent_id':n['id'],'kind':'rule','title':'显式规则','body':'必须核对订单','position':{'x':0,'y':0}}))
 asyncio.run(process(r,rule['object_id'],fake))
 run,_,_=start(derived_db[0],r,[ref(n)])
 generated=[i for i in run.snapshot['items'] if i['type']=='generated']
 assert generated and all(i['kind']!='rule' for i in generated)


def test_default_scope_is_actor_org_scoped_and_stable_on_replay(derived_db):
 from tests.test_knowledge_chat_v3 import start
 from services.knowledge_workspace.chat import KnowledgeRun
 make,_=derived_db;r=make();n=create(r)
 _,message,cid=start(make,r,[])
 run=KnowledgeRun.start(r,conversation_id=cid,input_message_id=message,default_scope=True)
 assert any(root['root']['id']==str(n['container_id']) for root in run.snapshot['roots'])
 assert not any(root['root']['id']==str(n['container_id']) for root in __import__('services.knowledge_workspace.chat',fromlist=['roots']).roots(make(1))['items'])
 create(r,title='后来创建的内容')
 replay=KnowledgeRun.start(r,conversation_id=cid,input_message_id=message,default_scope=True)
 assert replay.id==run.id


def test_unchanged_complete_generation_reused_without_model(derived_db):
 r=derived_db[0]();n=create(r);upload(r,n);gid=asyncio.run(process(r,n['id'],fake))
 with r.cursor() as c:c.execute('SELECT public.kb_enqueue_derivation(%s)',(n['id'],))
 async def forbidden(*args):raise AssertionError('no-op must not call model')
 assert asyncio.run(process(r,n['id'],forbidden))==gid


def test_historical_backfill_and_retry_are_bounded_and_write_guarded(derived_db):
 make,_=derived_db;r=make();n=create(r)
 assert r.organize(n['container_id'])['queued']>=1
 assert make(1).organize(n['container_id']) is None


def test_media_cache_requires_live_binding(derived_db):
 r=derived_db[0]();n=create(r);b=upload(r,n)
 with r.cursor() as c:
  c.execute('SELECT public.kb_save_media_result(%s,%s,1,%s,%s)',(n['id'],b['binding_id'],'test',Jsonb({'text':'图片核对内容','coverage':'complete'})))
 with r.cursor() as c:
  c.execute('SELECT public.kb_media_result(%s,%s,1,%s) AS v',(n['id'],b['binding_id'],'test'));assert c.fetchone()['v']['text']=='图片核对内容'
 with derived_db[0](1).cursor() as c:
  c.execute('SELECT public.kb_media_result(%s,%s,1,%s) AS v',(n['id'],b['binding_id'],'test'));assert c.fetchone()['v'] is None
 r.file_command(uuid4(),'file_remove',{'object_id':str(n['id']),'binding_id':b['binding_id'],'expected_revision':1})
 with r.cursor() as c:
  c.execute('SELECT public.kb_media_result(%s,%s,1,%s) AS v',(n['id'],b['binding_id'],'test'));assert c.fetchone()['v'] is None


def test_semantic_similarity_and_manual_edit_invalidate_generated_vectors():
 from services.knowledge_workspace.semantic import cosine,DIMENSIONS
 from services.knowledge_workspace.derived_document import effective_document
 vector=[1.0]+[0.0]*(DIMENSIONS-1)
 assert cosine(vector,vector)==1
 assert cosine(vector,[float('nan')]*DIMENSIONS)==0
 data={'sections':[{'id':'section:a','title':'t','text':'old','source_refs':[],'kind':'material'}],'chunks':[{'id':'section:a','embedding':vector,'embedding_model':'test'}],'overrides':{'section:a':{'text':'new'}}}
 assert 'embedding' not in effective_document(data)['chunks'][0]


def test_search_cursor_accepts_explicit_default_parameters(derived_db):
 from tests.test_knowledge_chat_v3 import start,ref
 r=derived_db[0]();a=create(r,title='pagination-probe-one');create(r,a['container_id'],a['id'],'pagination-probe-two')
 run,_,_=start(derived_db[0],r,[ref(a)]);root=run.introduction()['roots'][0]['handle']
 first=run.execute('search',{'root_handle':root,'query':'pagination-probe','limit':1});assert first['next_cursor']
 second=run.execute('search',{'root_handle':root,'query':'pagination-probe','limit':1,'cursor':first['next_cursor'],'mode':'evidence','expand':False})
 assert second['items'] and second['items'][0]['handle']!=first['items'][0]['handle']


def test_revocation_after_model_prevents_embedding_and_publication(derived_db):
 make,actors=derived_db;owner=make();n=create(owner)
 owner.command(command('set_member',{'container_id':n['container_id'],'user_id':actors[1],'role':'editor','status':'active'}))
 editor=make(1);upload(editor,n)
 async def revoke(sources,title,path):
  owner.command(command('set_member',{'container_id':n['container_id'],'user_id':actors[1],'role':'editor','status':'removed'}))
  return await fake(sources,title,path)
 assert asyncio.run(process(editor,n['id'],revoke)) is None
 assert document(editor,n['id']) is None
 assert document(owner,n['id'])['generation_id'] is None


def test_media_stage_source_change_stops_before_summarizing(derived_db,monkeypatch):
 from services.knowledge_workspace import media
 r=derived_db[0]();n=create(r)
 r.file_command(uuid4(),'file_upload',{'object_id':str(n['id']),'version_id':str(uuid4()),'name':'图片.png','mime_type':'image/png','size':10,'sha256':'c'*64,'text_status':'unsupported','extracted_text':''})
 async def changed(*args):
  upload(r,n,'处理中有新版')
  return {'text':'旧图片内容','coverage':'complete'}
 monkeypatch.setattr(media,'extract',changed)
 async def forbidden(*args):raise AssertionError('must reauthorize before sending summary')
 assert asyncio.run(process(r,n['id'],forbidden)) is None


def test_media_decoder_rejects_excessive_image_dimensions():
 from services.knowledge_workspace.media import prepare
 from PIL import Image
 import io
 stream=io.BytesIO();Image.new('1',(5000,5000)).save(stream,format='PNG')
 with pytest.raises(ValueError,match='IMAGE_PIXEL_CAPACITY'):prepare(stream.getvalue(),'image/png')


def test_orphan_manual_correction_keeps_source_dependencies_after_branch_move(derived_db):
 r=derived_db[0]();a=create(r,title='原目录');b=create(r,a['container_id'],None,'新目录');child=create(r,a['container_id'],a['id'],'原资料');upload(r,child)
 asyncio.run(process(r,child['id'],fake));asyncio.run(process(r,a['id'],fake));section=document(r,a['id'])['sections'][0]['id']
 with r.cursor() as c:c.execute('SELECT public.kb_set_knowledge_override(%s,%s,%s,0)',(a['id'],section,Jsonb({'text':'基于子资料的人工更正'})))
 r.command(command('reparent',{'object_id':child['id'],'expected_revision':1,'parent_id':b['id']}))
 asyncio.run(process(r,a['id'],fake));assert document(r,a['id'])['generation_id']
 child=current(r,child['id'])
 r.command(command('delete_branches',{'container_id':a['container_id'],'expected_container_revision':rev(r,a['container_id']),'objects':[{'object_id':child['id'],'expected_revision':child['revision']}]}))
 assert document(r,a['id'])['generation_id'] is None
 assert section not in document(r,a['id'])['overrides']
 # The old directory is no longer an ancestor, but dependency invalidation still queues it.
 with r.cursor() as c:
  c.execute('SELECT public.kb_claim_derivation(%s) AS v',(a['id'],));assert c.fetchone()['v']


def test_embedding_outage_does_not_hide_valid_text(derived_db,monkeypatch):
 import httpx
 from services.knowledge_workspace import semantic
 r=derived_db[0]();n=create(r);upload(r,n)
 async def failed(*args,**kwargs):raise httpx.ConnectError('synthetic unavailable')
 monkeypatch.setattr(semantic,'embed_chunks',failed)
 assert asyncio.run(process(r,n['id'],fake))
 assert document(r,n['id'])['sections']


def test_citations_are_canonicalized_against_snapshot_identifiers():
 from services.knowledge_workspace.citations import normalize
 source='/knowledge?object=abc&revision=1&chunk=section%3Atest'
 assert normalize('[资料](https://km.example.com'+source+')',[source])=='[资料]('+source+')'
 assert normalize('https://example.com/knowledge?object=other&revision=1',[source])=='（知识来源未核验）'
 assert normalize('https://external.example/article',[source])=='https://external.example/article'


@pytest.mark.asyncio
async def test_default_runtime_uses_live_scope_without_noise_for_unrelated_chat(derived_db,monkeypatch):
 from types import SimpleNamespace
 from core.config import get_settings
 from services.handlers.context_snapshot import ContextAnchor
 from services.knowledge_workspace.runtime import prepare_knowledge_run
 from tests.test_knowledge_chat_v3 import start
 r=derived_db[0]();create(r);_,mid,cid=start(derived_db[0],r,[])
 monkeypatch.setattr(get_settings(),'knowledge_derivation_enabled',True)
 monkeypatch.setattr('api.routes.knowledge.enabled',lambda ctx:True)
 handler=SimpleNamespace(db=SimpleNamespace(pool=r.pool),org_id=r.scope.org_id,_personal_context_allowed=True)
 anchor=ContextAnchor(task_id=str(uuid4()),conversation_id=cid,turn_id=str(uuid4()),input_message_id=mid,base_revision=0,through_message_id=None,org_id=r.scope.org_id)
 await prepare_knowledge_run(handler,anchor,r.scope.actor_user_id,cid)
 assert handler._knowledge_run and handler._knowledge_run.snapshot['automatic']
 assert handler._knowledge_run.evidence_footer()==''
 text='用户要求的代码 /knowledge?object=example&revision=1'
 assert handler._knowledge_run.normalize_citations(text)==text
 handler._personal_context_allowed=False
 await prepare_knowledge_run(handler,anchor,r.scope.actor_user_id,cid)
 assert handler._knowledge_run is None


def test_model_view_translates_status_only_and_preserves_source_text():
 from services.knowledge_workspace.model_view import present
 original={'items':[{'text_status':'truncated','body':'接口的unsupported字符串说明','status':'partial'}],'coverage':'snippets_only'}
 result=present(original)
 assert result['items'][0]['text_status']=='部分解析，未核验完整原件'
 assert result['items'][0]['body']==original['items'][0]['body']
 assert original['items'][0]['status']=='partial'


def test_backfill_rollout_filters_before_batch_limit_and_returns_metadata_only(derived_db):
 from core.db_scope import DatabaseScope,DatabaseAccessKind
 from services.knowledge_workspace.repository import Repository
 make,actors=derived_db;actor=make(1);create(actor,title='开放账号资料')
 worker=Repository(actor.pool,DatabaseScope(None,None,DatabaseAccessKind.WORKER))
 with worker.cursor() as c:
  c.execute("SELECT set_config('app.knowledge_users',%s,true),set_config('app.knowledge_orgs','',true)",(str(actors[1]),))
  c.execute('SELECT public.kb_derivation_candidates() AS v');rows=c.fetchone()['v']
  assert rows and all(row['actor_id']==str(actors[1]) for row in rows)
  assert all(set(row)=={'object_id','actor_id','org_id','needs_enqueue'} for row in rows)
 with actor.cursor() as c:
  with pytest.raises(psycopg.errors.InsufficientPrivilege):c.execute('SELECT * FROM public.kb_media_extractions')


def test_bounded_video_decoder_preserves_actual_time_range(tmp_path):
 import shutil
 from services.knowledge_workspace.media import run,prepare
 if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):pytest.skip('optional media decoders are not installed')
 target=tmp_path/'synthetic.mp4'
 run(['ffmpeg','-v','error','-nostdin','-y','-f','lavfi','-i','color=c=white:s=320x240:d=2','-f','lavfi','-i','sine=frequency=440:duration=2','-c:v','libx264','-threads','2','-pix_fmt','yuv420p','-c:a','aac','-shortest',str(target)])
 images,audio,partial=prepare(target.read_bytes(),'video/mp4')
 assert images and audio and partial
 assert audio[0][0]==0 and 1.9<=audio[0][1]<=2.1


def test_media_extraction_reuses_version_cache_before_opening_original(derived_db,monkeypatch):
 from core.config import get_settings
 from services.knowledge_workspace import media
 settings=get_settings();monkeypatch.setattr(settings,'knowledge_media_enabled',True);monkeypatch.setattr(settings,'dashscope_api_key','local-test-not-a-provider-key')
 r=derived_db[0]();n=create(r);b=upload(r,n)
 calls=[]
 monkeypatch.setattr(media,'read_version',lambda *args:b'local synthetic image')
 async def recognize(content,mime,authorize=None):
  if authorize:authorize()
  calls.append(content);return {'text':'媒体核对照片','coverage':'partial'}
 monkeypatch.setattr(media,'recognize',recognize)
 first=asyncio.run(media.extract(r,n['id'],b['binding_id'],1));assert len(calls)==1
 def forbidden(*args,**kwargs):raise AssertionError('cached version must not reopen original')
 monkeypatch.setattr(media,'read_version',forbidden);monkeypatch.setattr(media,'recognize',forbidden)
 assert asyncio.run(media.extract(r,n['id'],b['binding_id'],1))==first
 assert asyncio.run(media.extract(derived_db[0](1),n['id'],b['binding_id'],1)) is None


def test_file_update_and_remove_refresh_entire_chain_without_overwriting_manual(derived_db):
 r=derived_db[0]();root=create(r);middle=create(r,root['container_id'],root['id'],'中间目录');leaf=create(r,root['container_id'],middle['id'],'业务目录');chain=[leaf,middle,root]
 r.command(command('save_document',{'object_id':leaf['id'],'expected_revision':1,'body':'人工内容必须保留'}))
 b=upload(r,leaf,'旧版核对订单')
 async def summarize(sources,title,path):
  return {'sections':[{'title':'来源汇总','text':'\n'.join(s['text'] for s in sources),'source_refs':[s['key'] for s in sources]}] if sources else [],'tags':['新版'] if any('新版' in s['text'] for s in sources) else ['旧版'] if any('旧版' in s['text'] for s in sources) else []}
 def drain():
  for n in chain:assert asyncio.run(process(r,n['id'],summarize))
 drain()
 r.file_command(uuid4(),'file_upload',{'object_id':str(leaf['id']),'binding_id':b['binding_id'],'expected_revision':1,'version_id':str(uuid4()),'name':'处理.txt','mime_type':'text/plain','size':12,'sha256':'b'*64,'text_status':'ready','extracted_text':'新版核对订单照片'})
 for n in chain:assert document(r,n['id'])['generation_id'] is None
 drain()
 for n in chain:
  d=document(r,n['id']);assert d['tags']==['新版'];assert '新版核对订单照片' in d['sections'][0]['text'];assert '旧版' not in d['sections'][0]['text']
 r.file_command(uuid4(),'file_remove',{'object_id':str(leaf['id']),'binding_id':b['binding_id'],'expected_revision':2})
 drain()
 for n in chain:
  d=document(r,n['id']);assert d['tags']==[];assert '新版核对订单照片' not in d['sections'][0]['text'];assert '人工内容必须保留' in d['sections'][0]['text']
 assert r.object(leaf['id'])['body']=='人工内容必须保留'


@pytest.mark.parametrize('initial_at_creation',[False,True])
def test_manual_text_create_edit_clear_refreshes_chunks_tags_and_ancestors(derived_db,monkeypatch,initial_at_creation):
 from core.config import get_settings
 from services.knowledge_workspace.derived_document import effective_document
 from tests.test_knowledge_chat_v3 import start,ref
 monkeypatch.setattr(get_settings(),'knowledge_derivation_enabled',True)
 r=derived_db[0]();root=create(r,title='人工资料总目录');middle=create(r,root['container_id'],root['id'],'业务分类')
 old='独有旧条款：漏发核对订单后补寄，食品拆封不适用。';new='独有新条款：漏发核对照片后退款，食品拆封转人工。'
 result=r.command(command('create_object',{'container_id':root['container_id'],'parent_id':middle['id'],'kind':'topic','title':'直接输入','body':old if initial_at_creation else '', 'position':{'x':600,'y':0}}));leaf=r.object(result['object_id'])
 if not initial_at_creation:r.command(command('save_document',{'object_id':leaf['id'],'expected_revision':leaf['revision'],'body':old}))
 r.command(command('update_canvas_data',payload(r,[leaf],{'labels':['人工保留标签']})))
 chain=[leaf,middle,root]
 async def summarize(sources,title,path):
  text='\n'.join(s['text'] for s in sources)
  return {'sections':[{'title':'已保存正文汇总','text':text,'source_refs':[s['key'] for s in sources]}] if sources else [],'tags':['自动新条款'] if '独有新条款' in text else ['自动旧条款'] if '独有旧条款' in text else []}
 def drain(expected,absent,tag):
  manual=[r.object(n['id']) for n in chain];history=r.canvas_history()
  for n in chain:assert asyncio.run(process(r,n['id'],summarize))
  assert [r.object(n['id']) for n in chain]==manual and r.canvas_history()==history
  for n in chain:
   d=document(r,n['id']);combined='\n'.join(c['text'] for c in d['chunks'])
   if expected:assert expected in combined
   assert absent not in combined;assert d['tags']==tag
  assert current(r,leaf['id'])['canvas_data']['labels']==['人工保留标签']
  assert '人工保留标签' in effective_document(document(r,leaf['id']),['人工保留标签'])['tags']
 def search(query):
  run,_,_=start(derived_db[0],r,[ref(root)])
  return run.execute('search',{'root_handle':run.introduction()['roots'][0]['handle'],'query':query})['items']
 assert search('独有旧条款') # 原文无需等待后台整理即能检索。
 drain('独有旧条款','独有新条款',['自动旧条款'])
 assert any(i['type']=='generated' for i in search('独有旧条款'))
 r.command(command('save_document',{'object_id':leaf['id'],'expected_revision':r.object(leaf['id'])['revision'],'body':new}))
 for n in chain:assert document(r,n['id'])['generation_id'] is None
 assert not search('独有旧条款');assert search('独有新条款')
 drain('独有新条款','独有旧条款',['自动新条款'])
 r.command(command('save_document',{'object_id':leaf['id'],'expected_revision':r.object(leaf['id'])['revision'],'body':''}))
 for n in chain:assert document(r,n['id'])['generation_id'] is None
 assert not search('独有新条款')
 drain(None,'独有新条款',[])
 assert r.object(leaf['id'])['body']=='' and document(r,leaf['id'])['chunks']==[]
 assert not search('独有旧条款') and not search('独有新条款')


@pytest.mark.parametrize('foreign_scope',[(1,0),(0,1)])
def test_manual_text_derivation_and_search_cannot_cross_actor_or_org(derived_db,monkeypatch,foreign_scope):
 from core.config import get_settings
 from core.exceptions import AppException
 from tests.test_knowledge_chat_v3 import start,ref
 monkeypatch.setattr(get_settings(),'knowledge_derivation_enabled',True)
 make,_=derived_db;r=make();root=create(r);leaf=create(r,root['container_id'],root['id'],'人工私有资料')
 r.command(command('save_document',{'object_id':leaf['id'],'expected_revision':1,'body':'隔离文本：核对订单后免费补寄。'}))
 assert asyncio.run(process(r,leaf['id'],fake));assert asyncio.run(process(r,root['id'],fake))
 foreign=make(*foreign_scope)
 for n in [root,leaf]:assert document(foreign,n['id']) is None
 with pytest.raises(AppException):start(make,foreign,[ref(root)])
 calls=[]
 async def forbidden(*args):calls.append(1);raise AssertionError('unauthorized text must never reach model')
 try:asyncio.run(process(foreign,leaf['id'],forbidden))
 except psycopg.errors.InsufficientPrivilege:pass
 assert not calls
 with pytest.raises(psycopg.errors.InsufficientPrivilege,match='KB_ACCESS_DENIED'):
  foreign.command(command('save_document',{'object_id':leaf['id'],'expected_revision':2,'body':'越权替换'}))
 assert r.object(leaf['id'])['body']=='隔离文本：核对订单后免费补寄。'


def test_delete_root_topic_with_derived_descendants_is_undoable(derived_db):
 r=derived_db[0]();root=create(r);child=create(r,root['container_id'],root['id'],'业务子主题')
 upload(r,child);assert asyncio.run(process(r,child['id'],fake));assert asyncio.run(process(r,root['id'],fake))
 before=r.canvas();cid=root['container_id'];revision=next(c['revision'] for c in before['containers'] if c['id']==cid)
 result=r.command(command('delete_branches',{'container_id':cid,'expected_container_revision':revision,'objects':[{'object_id':root['id'],'expected_revision':root['revision']}]}))
 assert result['status']=='applied';assert not any(n['id'] in [root['id'],child['id']] for n in r.canvas()['nodes'])
 assert any(c['id']==cid for c in r.canvas()['containers'])
 toggle(r);assert {n['id'] for n in r.canvas()['nodes']}=={n['id'] for n in before['nodes']}
 toggle(r,'redo');assert not any(n['id'] in [root['id'],child['id']] for n in r.canvas()['nodes'])
