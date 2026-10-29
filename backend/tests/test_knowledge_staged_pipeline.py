"""Real SQL contracts for staged ingestion and enforced hierarchical reads."""
import asyncio
from pathlib import Path
from uuid import uuid4
import psycopg
import pytest
from core.config import get_settings
from services.knowledge_workspace import derivation,files,media
from services.knowledge_workspace.extraction import config_for
from services.knowledge_workspace.file_jobs import process as process_file
from tests.test_knowledge_xmind import import_db,prepared,archive,project_delete_db,derived_db,cleanup_db,resize_db,manual_db,summary_db,geometry_db,groups_db,editing_db,entry_db,rev,directional_db,structure_db,current,visual_db,tools_db,payload,local_db,command,layout_db,before_db,create,toggle,theme_db,rich_db,no_real_models


@pytest.fixture(scope='module')
def staged_db(import_db):
 make,actors=import_db
 with psycopg.connect(**make.admin_args) as c:c.execute((Path(__file__).resolve().parents[1]/'migrations/334_knowledge_staged_pipeline.sql').read_text())
 with psycopg.connect(**make.admin_args) as c:c.execute((Path(__file__).resolve().parents[1]/'migrations/335_knowledge_file_directory.sql').read_text())
 return make,actors


def bind(r,n,root,name,data):
 vid=uuid4();target=files.private_path(root,r.scope.org_id,r.scope.actor_user_id,vid,create=True);target.write_bytes(data)
 metadata=files.inspect_file(target,name,extract_text=False)
 result=r.file_command(uuid4(),'file_upload',{'object_id':str(n['id']),'version_id':str(vid),**metadata})
 version=r.file_version(n['id'],result['binding_id'],1)
 return {'object_id':str(n['id']),'binding_id':str(result['binding_id']),'revision':1,'config':config_for(version)}


def enqueue(r,job):
 with r.cursor() as c:c.execute('SELECT public.kb_file_enqueue(%s,%s,%s,%s)',tuple(job.values()))


def test_framework_nodes_commit_before_files_and_retry_does_not_duplicate(staged_db,tmp_path):
 r=staged_db[0]();key,p=prepared(tmp_path,r);result=r.import_xmind(key,p)
 assert r.import_xmind(key,p)['status']=='replayed'
 nodes=[n for n in r.canvas()['nodes'] if str(n['container_id'])==result['container_id']]
 assert len(nodes)==5 and len([n for n in nodes if n['canvas_data'].get('image')])==1
 assert r.import_progress(result['container_id'])['framework']=='complete'
 assert all(f['metadata']['text_status']=='unsupported' for f in p['files'])


def test_projection_is_node_local_and_never_calls_a_model(staged_db,monkeypatch):
 r=staged_db[0]();parent=create(r,title='根');child=create(r,parent['container_id'],parent['id'],'子')
 r.command(command('save_document',{'object_id':child['id'],'expected_revision':1,'body':'仅属于子节点的原文'}))
 async def forbidden():raise AssertionError('local projection must not call provider')
 monkeypatch.setattr(derivation._client,'get',forbidden)
 assert asyncio.run(derivation.process(r,parent['id']))
 assert asyncio.run(derivation.process(r,child['id']))
 monkeypatch.setattr(get_settings(),'knowledge_derivation_enabled',True)
 doc=r.document(parent['id'])['derived'];assert all('仅属于子节点' not in s['text'] for s in doc['sections'])
 with r.cursor() as c:c.execute('SELECT public.kb_derivation_fingerprint(%s) AS value',(parent['id'],));before=c.fetchone()['value']
 r.command(command('save_document',{'object_id':child['id'],'expected_revision':2,'body':'子节点新版'}))
 with r.cursor() as c:c.execute('SELECT public.kb_derivation_fingerprint(%s) AS value',(parent['id'],));assert c.fetchone()['value']==before


def test_file_failure_isolated_native_success_cached_and_manual_retry(staged_db,tmp_path,monkeypatch):
 r=staged_db[0]();n=create(r);monkeypatch.setattr(get_settings(),'file_workspace_root',str(tmp_path))
 good=bind(r,n,tmp_path,'有效.txt','内容只属于有效文件'.encode());bad=bind(r,n,tmp_path,'失败.txt',b'\xff\xff')
 enqueue(r,good);enqueue(r,bad)
 with pytest.raises(Exception):asyncio.run(process_file(r,bad))
 assert asyncio.run(process_file(r,good)) is True
 rows={f['title']:f['version'] for f in r.files(n['id'])}
 assert rows['有效.txt']['text_status']=='ready' and rows['失败.txt']['parse_state']=='failed'
 assert asyncio.run(process_file(r,good)) is None
 assert r.retry_file(n['id'],bad['binding_id'],1)['queued']
 assert next(f for f in r.files(n['id']) if f['title']=='失败.txt')['version']['parse_state']=='queued'
 assert staged_db[0](1).file_version(n['id'],good['binding_id'],1) is None
 with pytest.raises(Exception):enqueue(staged_db[0](1),good)


def test_hierarchy_rejects_skips_and_search_never_reads_body(staged_db,monkeypatch,tmp_path):
 from tests.test_knowledge_chat_v3 import start,ref
 from services.knowledge_workspace.answer_plan import collect
 from services.knowledge_workspace.chat import KnowledgeRun
 r=staged_db[0]();a=create(r,title='总纲');b=create(r,a['container_id'],a['id'],'采购');c=create(r,a['container_id'],b['id'],'尺寸')
 file=bind(r,c,tmp_path,'规格.txt','底卡75*140mm'.encode());monkeypatch.setattr(get_settings(),'file_workspace_root',str(tmp_path));enqueue(r,file);asyncio.run(process_file(r,file))
 run,_,_=start(staged_db[0],r,[ref(a)]);root=run.introduction()['roots'][0]['handle'];assert run.snapshot['read_protocol']=='hierarchical-v1'
 f=next(i for i in run.snapshot['items'] if i['type']=='file' and i['binding_id']==file['binding_id'])
 with pytest.raises(Exception,match='请先打开上一级目录'):run.execute('read',{'root_handle':root,'item_handles':[f['handle']]})
 planning,_,_=start(staged_db[0],r,[ref(a)])
 assert collect(planning,'底卡多少尺寸？')['mode']=='expand'
 original=run._text
 def forbidden(*args):raise AssertionError('metadata operations must not load any body')
 monkeypatch.setattr(run,'_text',forbidden)
 top=run.execute('outline',{'root_handle':root});ah=top['items'][0]['handle']
 one=run.execute('outline',{'root_handle':root,'parent_handle':ah});bh=next(i['handle'] for i in one['items'] if i['title']=='采购')
 found=run.execute('search',{'root_handle':root,'scope_handle':bh,'query':'尺寸 规格'});ch=found['items'][0]['handle']
 assert '75*140' not in str(found) and found['coverage']=='outline_only'
 missed=run.execute('search',{'root_handle':root,'query':'不存在的名称'})
 assert [i['handle'] for i in missed['unopened_branches']]==[ch]
 assert f['handle'] not in str(missed['unopened_branches']) and '75*140' not in str(missed)
 with pytest.raises(Exception,match='请先打开上一级目录'):run.execute('read',{'root_handle':root,'item_handles':[f['handle']]})
 monkeypatch.setattr(run,'_text',original)
 node=run.execute('read',{'root_handle':root,'item_handles':[ch]})
 assert node['items'][0]['attachments'][0]['handle']==f['handle']
 assert node['coverage']['scope']=='requested_items' and 'missing_count' not in node['coverage']
 # Persisted navigation survives restoration; no client-supplied grants.
 resumed=KnowledgeRun(r,run.id,run.snapshot)
 read=resumed.execute('read',{'root_handle':root,'item_handles':[f['handle']]})
 assert read['items'][0]['body']=='底卡75*140mm'
 assert '75*140mm' in ''.join(v['text'] for v in resumed.grounding_sources())


def test_stale_file_lease_cannot_publish_success_or_apply_to_new_version(staged_db,tmp_path):
 from psycopg.types.json import Jsonb
 r=staged_db[0]();n=create(r);job=bind(r,n,tmp_path,'版本.txt',b'original');enqueue(r,job);args=tuple(job.values())
 with r.cursor() as c:
  c.execute('SELECT public.kb_file_claim(%s,%s,%s,%s) AS token',args);token=c.fetchone()['token'];assert token
  c.execute('SELECT public.kb_file_claim(%s,%s,%s,%s) AS token',args);assert c.fetchone()['token'] is None
  c.execute('SELECT public.kb_file_finish(%s,%s,%s,%s,%s) AS done',(*args,token));assert c.fetchone()['done'] is False
  c.execute('SELECT public.kb_save_media_result(%s,%s,%s,%s,%s)',(*args,Jsonb({'text':'original','coverage':'complete'})))
 # A new immutable version invalidates the old completion, regardless of cached output.
 r.file_command(uuid4(),'file_upload',{'object_id':str(n['id']),'binding_id':job['binding_id'],'expected_revision':1,'version_id':str(uuid4()),
     'name':'新版.txt','mime_type':'text/plain','size':3,'sha256':'a'*64,'text_status':'unsupported','extracted_text':None})
 with r.cursor() as c:
  c.execute('SELECT public.kb_file_finish(%s,%s,%s,%s,%s) AS done',(*args,token));assert c.fetchone()['done'] is False
 assert r.file_version(n['id'],job['binding_id'],2)['text_status']=='unsupported'


def test_local_processing_remains_available_without_ai_summary_flag(staged_db,monkeypatch):
 from services.knowledge_workspace.derivation_jobs import DerivationWorker
 r=staged_db[0]();n=create(r);monkeypatch.setattr(get_settings(),'knowledge_derivation_enabled',False)
 assert asyncio.run(derivation.process(r,n['id']))
 assert r.document(n['id'])['derived']['generation_id']
 candidates=DerivationWorker(r.pool).candidates()
 assert isinstance(candidates,list)


def test_restored_snapshot_keeps_original_recognition_configuration(staged_db,tmp_path,monkeypatch):
 from io import BytesIO
 from PIL import Image
 from psycopg.types.json import Jsonb
 from tests.test_knowledge_chat_v3 import start,ref
 r=staged_db[0]();n=create(r);buf=BytesIO();Image.new('RGB',(2,2),'white').save(buf,format='PNG')
 monkeypatch.setattr(get_settings(),'knowledge_media_enabled',True)
 job=bind(r,n,tmp_path,'图片.png',buf.getvalue())
 with r.cursor() as c:c.execute('SELECT public.kb_save_media_result(%s,%s,%s,%s,%s)',(*tuple(job.values()),Jsonb({'text':'旧识别版本的文字','coverage':'complete'})))
 run,_,_=start(staged_db[0],r,[ref(n)]);root=run.introduction()['roots'][0]['handle']
 node=run.execute('outline',{'root_handle':root})['items'][0]
 read=run.execute('read',{'root_handle':root,'item_handles':[node['handle']]});handle=read['items'][0]['attachments'][0]['handle']
 monkeypatch.setattr(get_settings(),'knowledge_vision_model','new-model-config')
 assert r.file_version(n['id'],job['binding_id'],1)['text_status']=='unsupported'
 assert next(i for i in run.snapshot['items'] if i['type']=='file')['directory_terms']==['旧识别版本的文字']
 newer,_,_=start(staged_db[0],r,[ref(n)])
 assert next(i for i in newer.snapshot['items'] if i['type']=='file')['directory_terms']==[]
 result=run.execute('read',{'root_handle':root,'item_handles':[handle]})
 assert result['items'][0]['body']=='旧识别版本的文字'


def test_node_read_only_reveals_bounded_next_layer_and_files_require_separate_read(staged_db):
 from tests.test_knowledge_chat_v3 import start,ref
 r=staged_db[0]();root=create(r,title='分页根')
 for i in range(12):create(r,root['container_id'],root['id'],f'子节点{i:02d}')
 run,_,_=start(staged_db[0],r,[ref(root)]);rh=run.introduction()['roots'][0]['handle'];top=run.execute('outline',{'root_handle':rh})['items'][0]
 opened=run.execute('read',{'root_handle':rh,'item_handles':[top['handle']]})
 assert opened['items'][0]['content_scope']=='node_only'
 assert len(opened['items'][0]['children'])==10 and opened['items'][0]['more_entries']
 assert '不含任何附件内容' in opened['read_guidance']
 first=run.execute('outline',{'root_handle':rh,'parent_handle':top['handle']})
 assert len(first['items'])==10 and first['next_cursor']
 second=run.execute('outline',{'root_handle':rh,'parent_handle':top['handle'],'cursor':first['next_cursor']})
 assert len(second['items'])==2


def test_expired_attachment_lease_can_resume_but_old_token_cannot_finish(staged_db,tmp_path):
 from psycopg.types.json import Jsonb
 r=staged_db[0]();n=create(r);job=bind(r,n,tmp_path,'租约.txt',b'contents');enqueue(r,job);args=tuple(job.values())
 with r.cursor() as c:
  c.execute('SELECT public.kb_file_claim(%s,%s,%s,%s) AS token',args);old=c.fetchone()['token']
  c.execute('SELECT public.kb_save_media_result(%s,%s,%s,%s,%s)',(*args,Jsonb({'text':'contents','coverage':'complete'})))
 with psycopg.connect(**staged_db[0].admin_args) as c:
  c.execute('SET LOCAL ROLE everydayai_owner')
  c.execute("UPDATE public.kb_file_parse_jobs SET lease_until=now()-interval '1 second' WHERE binding_id=%s",(job['binding_id'],))
 with r.cursor() as c:
  c.execute('SELECT public.kb_file_claim(%s,%s,%s,%s) AS token',args);new=c.fetchone()['token'];assert new and new!=old
  c.execute('SELECT public.kb_file_finish(%s,%s,%s,%s,%s) AS done',(*args,old));assert c.fetchone()['done'] is False
  c.execute('SELECT public.kb_file_finish(%s,%s,%s,%s,%s) AS done',(*args,new));assert c.fetchone()['done'] is True
 with pytest.raises(psycopg.errors.InsufficientPrivilege):
  with r.cursor() as c:c.execute('SELECT * FROM public.kb_file_parse_jobs')


def test_changed_source_keeps_correction_history_but_does_not_apply_it_to_new_version(staged_db,tmp_path,monkeypatch):
 from tests.test_knowledge_chat_v3 import start,ref
 r=staged_db[0]();n=create(r);monkeypatch.setattr(get_settings(),'file_workspace_root',str(tmp_path))
 job=bind(r,n,tmp_path,'原文.txt',b'first');enqueue(r,job);asyncio.run(process_file(r,job));asyncio.run(derivation.process(r,n['id']))
 section=r.document(n['id'])['derived']['sections'][0]['id']
 r.knowledge_override(n['id'],section,{'text':'仅旧版适用的人工更正'},0,uuid4())
 old,_,_=start(staged_db[0],r,[ref(n)])
 assert next(i for i in old.snapshot['items'] if i['type']=='object')['manual_corrections']
 r.file_command(uuid4(),'file_upload',{'object_id':str(n['id']),'binding_id':job['binding_id'],'expected_revision':1,'version_id':str(uuid4()),
    'name':'原文.txt','mime_type':'text/plain','size':3,'sha256':'b'*64,'text_status':'ready','extracted_text':'新版正文'})
 asyncio.run(derivation.process(r,n['id']))
 new,_,_=start(staged_db[0],r,[ref(n)])
 assert not next(i for i in new.snapshot['items'] if i['type']=='object')['manual_corrections']
 doc=r.document(n['id'])['derived'];assert any(s.get('manual') and s['text']=='仅旧版适用的人工更正' for s in doc['sections'])
 assert not any(c['kind']=='source' and '仅旧版适用' in c['text'] for c in doc['chunks'])


def test_literal_file_index_routes_only_next_hop_and_still_requires_read(staged_db,tmp_path,monkeypatch):
 from tests.test_knowledge_chat_v3 import start,ref
 r=staged_db[0]();a=create(r,title='目录');wrong=create(r,a['container_id'],a['id'],'常见业务');b=create(r,a['container_id'],a['id'],'其他事务');c=create(r,a['container_id'],b['id'],'表格格式')
 monkeypatch.setattr(get_settings(),'file_workspace_root',str(tmp_path))
 job=bind(r,c,tmp_path,'af093.txt','| 名称 | 编码 | 数量 |\n| :--- | :--- | :--- |\n| 松林磁吸收纳册 | TEST-09 | 3 |'.encode());enqueue(r,job);assert asyncio.run(process_file(r,job))
 run,_,_=start(staged_db[0],r,[ref(a)]);root=run.introduction()['roots'][0]['handle']
 f=next(i for i in run.snapshot['items'] if i['type']=='file' and i['binding_id']==job['binding_id'])
 assert f['directory_terms']==['名称','松林磁吸收纳册']
 def forbidden(*args):raise AssertionError('routing must never read bodies')
 monkeypatch.setattr(run,'_text',forbidden)
 top=run.execute('outline',{'root_handle':root});ah=top['items'][0]['handle']
 run.execute('outline',{'root_handle':root,'parent_handle':ah})
 found=run.execute('search',{'root_handle':root,'scope_handle':ah,'query':'松林磁吸收纳册的编码数量'})
 assert found['items'][0]['title']=='其他事务' and 'TEST-09' not in str(found) and f['handle'] not in str(found)
 bh=found['items'][0]['handle']
 assert found['items'][0]['next_step']['scope_handle']==bh
 wrong_h=next(i['handle'] for i in run.snapshot['items'] if i['type']=='object' and i['id']==str(wrong['id']))
 miss=run.execute('search',{'root_handle':root,'scope_handle':wrong_h,'query':'松林磁吸收纳册'})
 assert miss['items']==[] and miss['next_step']['scope_handle']==bh and 'TEST-09' not in str(miss)
 with r.cursor() as cursor:
  cursor.execute('SELECT state FROM kb_retrieval_states WHERE snapshot_id=%s',(run.id,));before=cursor.fetchone()['state']
 with pytest.raises(Exception,match='materialize只适用于文件附件'):run.execute('read',{'root_handle':root,'item_handles':[bh],'materialize':True})
 with r.cursor() as cursor:
  cursor.execute('SELECT state FROM kb_retrieval_states WHERE snapshot_id=%s',(run.id,));assert cursor.fetchone()['state']==before
 found=run.execute('search',{'root_handle':root,'scope_handle':bh,'query':'松林磁吸收纳册'})
 ch=found['items'][0]['handle'];assert found['items'][0]['title']=='表格格式'
 with pytest.raises(Exception,match='请先打开上一级目录'):run.execute('read',{'root_handle':root,'item_handles':[f['handle']]})
 with r.cursor() as cursor:
  cursor.execute('SELECT public.kb_file_directory(%s,%s,%s,%s) AS terms',tuple(job.values()));assert cursor.fetchone()['terms']==f['directory_terms']
 with staged_db[0](1).cursor() as cursor:
  cursor.execute('SELECT public.kb_file_directory(%s,%s,%s,%s) AS terms',tuple(job.values()));assert cursor.fetchone()['terms'] is None


def test_program_navigation_opens_layers_without_reading_or_answering(staged_db,tmp_path,monkeypatch):
 from tests.test_knowledge_chat_v3 import start,ref
 from services.knowledge_workspace.answer_plan import collect,message,eligible
 r=staged_db[0]();a=create(r,title='项目目录');b=create(r,a['container_id'],a['id'],'其他');c=create(r,a['container_id'],b['id'],'格式')
 monkeypatch.setattr(get_settings(),'file_workspace_root',str(tmp_path));job=bind(r,c,tmp_path,'opaque.txt','| 名称 | 编码 |\n| 山林磁吸文件册 | PRIVATE-03 |'.encode());enqueue(r,job);asyncio.run(process_file(r,job))
 run,_,_=start(staged_db[0],r,[ref(a)])
 def forbidden(*args):raise AssertionError('directory planner must never read business text')
 monkeypatch.setattr(run,'_text',forbidden)
 plan=collect(run,'山林磁吸文件册的编码是什么？')
 assert plan['mode']=='expand' and plan['reason']=='hierarchical_route' and 'PRIVATE-03' not in str(plan)
 assert len(plan['path'])>=3 and 'next_step' in message(plan)['content']
 restored=collect(run,'山林磁吸文件册的编码是什么？')
 assert restored['cache_hit'] and restored['items']==plan['items']
 with r.cursor() as cursor:
  cursor.execute("SELECT count(*) AS n FROM kb_read_receipts WHERE snapshot_id=%s AND state IN ('body_partial','body_complete')",(run.id,));assert cursor.fetchone()['n']==0
 assert eligible('上传失败教学里有什么步骤？',hierarchical=True)
 assert not eligible('能帮我下载这份资料吗？',hierarchical=True)


def test_route_does_not_combine_unrelated_words_from_a_large_branch():
 from services.knowledge_workspace.directory import route_score
 good={'id':'good','type':'object','title':'上传失败处理方式','handle':'good'}
 noise={'id':'noise','type':'object','title':'其他事务','handle':'noise'}
 items=[good,noise,*[{'id':'noise','type':'file','title':str(i)+'.png','handle':str(i),'directory_terms':[term]} for i,term in enumerate(['请明确说明','不能确认','未读取视频','排查步骤','客户店铺','三条说明'])]]
 question='店铺上传失败给了哪三条排查步骤？若未读取视频，请明确说明不能确认。'
 assert route_score(items,good,question)>route_score(items,noise,question)


def test_only_returned_full_original_titles_can_be_business_evidence(staged_db,tmp_path,monkeypatch):
 from tests.test_knowledge_chat_v3 import start,ref
 r=staged_db[0]();a=create(r,title='目录');b=create(r,a['container_id'],a['id'],'每周五提交');hidden=create(r,a['container_id'],b['id'],'三日内复核')
 monkeypatch.setattr(get_settings(),'file_workspace_root',str(tmp_path));job=bind(r,a,tmp_path,'说明.txt','本页说明'.encode());enqueue(r,job);asyncio.run(process_file(r,job))
 run,_,_=start(staged_db[0],r,[ref(a)]);rh=run.introduction()['roots'][0]['handle'];top=run.execute('outline',{'root_handle':rh})['items'][0]
 run.execute('read',{'root_handle':rh,'item_handles':[top['handle']]})
 sources=run.grounding_sources();assert not any(s['key'].startswith('title:') and s['text']=='三日内复核' for s in sources)
 run.execute('outline',{'root_handle':rh,'parent_handle':top['handle']})
 sources=run.grounding_sources()
 assert any(s['key'].startswith('title:') and s['text']=='每周五提交' and s['kind']=='original' for s in sources)
 assert not any(s['key'].startswith('title:') and s['text']=='三日内复核' for s in sources)


def test_retry_recovers_to_readable_then_new_version_keeps_old_snapshot(staged_db,tmp_path,monkeypatch):
 from tests.test_knowledge_chat_v3 import start,ref
 from services.knowledge_workspace.chat import KnowledgeRun
 r=staged_db[0]();n=create(r,title='版本与恢复验收');monkeypatch.setattr(get_settings(),'file_workspace_root',str(tmp_path))
 job=bind(r,n,tmp_path,'资料.txt','旧版收纳袋规格12厘米'.encode());enqueue(r,job)
 original=media.extract;attempts=[]
 async def transient(*args,**kwargs):
  attempts.append(True)
  if len(attempts)==1:raise TimeoutError('synthetic transient extraction failure')
  return await original(*args,**kwargs)
 monkeypatch.setattr(media,'extract',transient)
 with pytest.raises(TimeoutError):asyncio.run(process_file(r,job))
 assert r.file_version(n['id'],job['binding_id'],1)['parse_state']=='failed'
 assert r.import_progress(str(n['container_id']))['files']['failed']>=1
 assert r.retry_file(n['id'],job['binding_id'],1)['queued']
 assert asyncio.run(process_file(r,job)) is True
 assert r.file_version(n['id'],job['binding_id'],1)['parse_state']=='ready'
 assert r.files(n['id'])[0]['version']['text_status']=='ready'
 assert r.file_versions(n['id'],job['binding_id'])['items'][0]['parse_state']=='ready'
 old,_,_=start(staged_db[0],r,[ref(n)])
 vid=uuid4();path=files.private_path(tmp_path,r.scope.org_id,r.scope.actor_user_id,vid,create=True);path.write_bytes('新版收纳袋规格18厘米'.encode())
 metadata=files.inspect_file(path,'资料.txt',extract_text=False)
 r.file_command(uuid4(),'file_upload',{'object_id':str(n['id']),'binding_id':job['binding_id'],'expected_revision':1,'version_id':str(vid),**metadata})
 newer_job={**job,'revision':2};enqueue(r,newer_job);assert asyncio.run(process_file(r,newer_job))
 new,_,_=start(staged_db[0],r,[ref(n)])
 def read(run):
  rh=run.introduction()['roots'][0]['handle'];node=run.execute('outline',{'root_handle':rh})['items'][0]
  owner=run.execute('read',{'root_handle':rh,'item_handles':[node['handle']]})['items'][0]
  return run.execute('read',{'root_handle':rh,'item_handles':[owner['attachments'][0]['handle']]})['items'][0]
 old_result=read(KnowledgeRun(r,old.id,old.snapshot));new_result=read(new)
 assert old_result['body']=='旧版收纳袋规格12厘米' and old_result['source']['revision']==1
 assert new_result['body']=='新版收纳袋规格18厘米' and new_result['source']['revision']==2
 assert r.files(n['id'])[0]['current_revision']==2
 assert [v['revision'] for v in r.file_versions(n['id'],job['binding_id'])['items']]==[2,1]
 assert staged_db[0](1).file_version(n['id'],job['binding_id'],2) is None
 assert staged_db[0](0,1).file_version(n['id'],job['binding_id'],2) is None
 assert len(attempts)==3


def test_composite_directory_plan_covers_branches_and_matching_ancestors(staged_db,tmp_path,monkeypatch):
 from tests.test_knowledge_chat_v3 import start,ref
 from services.knowledge_workspace.answer_plan import collect
 r=staged_db[0]();a=create(r,title='业务框架');b=create(r,a['container_id'],a['id'],'费用申请须附原始单据');c=create(r,a['container_id'],a['id'],'仓库发货时间须确认批次')
 monkeypatch.setattr(get_settings(),'file_workspace_root',str(tmp_path))
 for node,text in [(b,'费用申请标准以原始单据为准'),(c,'仓库发货时间按通知中的批次安排')]:
  job=bind(r,node,tmp_path,'opaque.txt',text.encode());enqueue(r,job);asyncio.run(process_file(r,job))
 run,_,_=start(staged_db[0],r,[ref(a)])
 def forbidden(*args):raise AssertionError('composite planning must not read text')
 monkeypatch.setattr(run,'_text',forbidden)
 plan=collect(run,'费用申请的标准是什么？仓库发货时间应该怎么看？请分开回答。')
 assert len(plan['parts'])==2 and len(plan['items'])==2
 handles={h for item in plan['items'] for h in item['next_step']['item_handles']}
 assert all(next(i['handle'] for i in run.snapshot['items'] if i['type']=='object' and i['id']==str(n['id'])) in handles for n in [b,c])
 assert len([i for i in run.snapshot['items'] if i['type']=='file' and i['handle'] in handles])==2
 with r.cursor() as cursor:
  cursor.execute('SELECT state FROM kb_retrieval_states WHERE snapshot_id=%s',(run.id,));assert cursor.fetchone()['state']['calls']<=21
  cursor.execute("SELECT count(*) AS n FROM kb_read_receipts WHERE snapshot_id=%s AND state IN ('body_partial','body_complete')",(run.id,));assert cursor.fetchone()['n']==0


def test_starred_form_fields_route_above_a_generic_required_fields_guide():
 from services.knowledge_workspace.directory import route_score
 form={'id':'form','handle':'form','type':'file','title':'a0f.png','directory_terms':['*申请类型 请选择','*发生日期 请选择']}
 guide={'id':'guide','handle':'guide','type':'file','title':'必填项说明','directory_terms':['是否有规格','主编码需一致']}
 query='申请表带星号的必填项有哪些？'
 assert route_score([form,guide],form,query)>route_score([form,guide],guide,query)


def test_answer_checkpoint_waits_for_all_pages_retains_other_tools_and_resets_on_steer(staged_db,tmp_path,monkeypatch):
 from types import SimpleNamespace
 from tests.test_knowledge_chat_v3 import start,ref
 from services.knowledge_workspace.answer_plan import collect,constrain_route_tools,route_checkpoint_message,invalidate_for_steer,CHECKPOINT_PREFIX
 r=staged_db[0]();a=create(r,title='说明档案');monkeypatch.setattr(get_settings(),'file_workspace_root',str(tmp_path))
 job=bind(r,a,tmp_path,'opaque.txt',('档案说明资料'*2000).encode());enqueue(r,job);asyncio.run(process_file(r,job))
 run,_,_=start(staged_db[0],r,[ref(a)]);plan=collect(run,'档案说明内容是什么？');prepared=SimpleNamespace(knowledge_answer_plan=plan,messages=[])
 tools=[{'function':{'name':'knowledge_read'}},{'function':{'name':'query_orders'}}]
 assert asyncio.run(constrain_route_tools(prepared,run,tools))==tools
 step=plan['items'][0]['next_step'];read=run.execute('read',{k:v for k,v in step.items() if k!='tool'})
 partial=next(i for i in read['items'] if i.get('next_cursor'));assert route_checkpoint_message(run) is None
 run.execute('read',{'root_handle':step['root_handle'],'item_handles':[partial['handle']],'cursor':partial['next_cursor']})
 assert asyncio.run(constrain_route_tools(prepared,run,tools))==tools[1:]
 assert route_checkpoint_message(run) is not None
 with pytest.raises(Exception,match='进入答复阶段'):run.execute('outline',{'root_handle':step['root_handle']})
 asyncio.run(constrain_route_tools(prepared,run,tools));assert len(prepared.messages)==1
 invalidate_for_steer(prepared,run)
 assert not any(str(m['content']).startswith(CHECKPOINT_PREFIX) for m in prepared.messages)
 assert asyncio.run(constrain_route_tools(prepared,run,tools))==tools
 assert run.execute('outline',{'root_handle':step['root_handle']})['items']


def test_anaphoric_subquestion_keeps_topic_but_explicit_new_topic_does_not():
 from services.knowledge_workspace.directory import question_parts,route_score
 parts=question_parts('线上急单示例列了哪几个订单号？截图说明应该如何处理？')
 assert len(parts)==2 and '线上急单' in parts[1]
 assert '物品补发' in question_parts('物品补发需要拆什么？新的物品如何处理？')[1]
 parts=question_parts('申请资料要哪几项？另外仓库几点发货？')
 assert len(parts)==2 and '申请资料' not in parts[1]
 item={'id':'file','handle':'file','type':'file','title':'opaque.png','directory_terms':['310912-123456789012345','310913-223456789012345']}
 assert route_score([item],item,'急单示例列了哪几个完整订单号？')>0
 assert '123456789012345' not in str(question_parts('急单示例列了哪几个完整订单号？'))


def test_checkpoint_keeps_retrieval_open_until_applicable_rule_is_fully_read(staged_db,tmp_path,monkeypatch):
 from tests.test_knowledge_chat_v3 import start,ref
 from services.knowledge_workspace.answer_plan import collect,route_checkpoint_message
 r=staged_db[0]();a=create(r,title='规则范围');n=create(r,a['container_id'],a['id'],'收纳规范')
 rule_id=r.command(command('create_object',{'container_id':a['container_id'],'parent_id':a['id'],'kind':'rule','title':'适用约束','position':{'x':0,'y':0}}))['object_id']
 r.command(command('save_document',{'object_id':rule_id,'expected_revision':1,'body':'必须核对状态与例外条件。'*250}))
 monkeypatch.setattr(get_settings(),'file_workspace_root',str(tmp_path));job=bind(r,n,tmp_path,'规范.txt','收纳规范要求见状态说明'.encode());enqueue(r,job);asyncio.run(process_file(r,job))
 run,_,_=start(staged_db[0],r,[ref(a)]);plan=collect(run,'收纳规范是什么？');step=plan['items'][0]['next_step']
 read=run.execute('read',{k:v for k,v in step.items() if k!='tool'})
 rule=next(rule for item in read['items'] for rule in item['rules'] if rule['truncated'])
 assert route_checkpoint_message(run) is None
 from services.knowledge_workspace import grounding
 with pytest.raises(grounding.GroundingError,match='GROUNDING_RULES_INCOMPLETE'):run.grounding_sources()
 usage={}
 assert '适用规则尚未完整读取' in asyncio.run(grounding.release_answer(run,'可以直接处理。','收纳规范是什么？',usage=usage))
 assert usage['grounding_outcome']=='incomplete_rules' and not usage.get('grounding_calls')
 # Delivered rules grant only their actual handle; reading them settles the rule prerequisite.
 run.execute('read',{'root_handle':step['root_handle'],'item_handles':[rule['handle']]})
 assert route_checkpoint_message(run) is not None


def test_topic_prefix_breaks_a_generic_order_term_tie_without_uuid_dependence():
 from services.knowledge_workspace.directory import route_score
 topic={'id':'topic','handle':'z','type':'object','title':'线上订单处理'}
 generic={'id':'generic','handle':'a','type':'object','title':'刷单仓库对接单号'}
 query='线上急单对接的示例列了哪几个完整订单号？'
 assert route_score([topic,generic],topic,query)>route_score([topic,generic],generic,query)


def test_table_directory_keeps_only_row_names_with_or_without_outer_pipes(staged_db,tmp_path,monkeypatch):
 r=staged_db[0]();n=create(r,title='表格索引');monkeypatch.setattr(get_settings(),'file_workspace_root',str(tmp_path))
 variants=['名称 | 编码 | 数量\n---|---|---\n收纳样品 | DEMO-07 | 2','| 名称 | 编码 | 数量 |\n|---|---|---|\n| 收纳样品 | DEMO-07 | 2 |']
 for i,text in enumerate(variants):
  job=bind(r,n,tmp_path,f'格式{i}.txt',text.encode());enqueue(r,job);asyncio.run(process_file(r,job))
  with r.cursor() as c:
   c.execute('SELECT public.kb_file_directory(%s,%s,%s,%s) AS terms',tuple(job.values()));terms=c.fetchone()['terms']
  assert terms==['名称','收纳样品'] and 'DEMO-07' not in str(terms)


def test_satisfied_ancestor_scope_is_not_a_reason_to_choose_an_unrelated_child(staged_db,tmp_path,monkeypatch):
 from tests.test_knowledge_chat_v3 import start,ref
 r=staged_db[0]();a=create(r,title='研发对接流程');correct=create(r,a['container_id'],a['id'],'规格样张');other=create(r,a['container_id'],a['id'],'研发对接样品进度')
 monkeypatch.setattr(get_settings(),'file_workspace_root',str(tmp_path));job=bind(r,correct,tmp_path,'opaque.txt','底卡尺寸：81*121mm'.encode());enqueue(r,job);asyncio.run(process_file(r,job))
 run,_,_=start(staged_db[0],r,[ref(a)]);root=run.introduction()['roots'][0]['handle'];top=run.execute('outline',{'root_handle':root})['items'][0]
 result=run.execute('search',{'root_handle':root,'scope_handle':top['handle'],'query':'研发对接流程的商品底卡尺寸是多少？'})
 assert result['items'][0]['title']=='规格样张' and '81*121mm' not in str(result)


def test_directory_keeps_literal_caption_before_url_without_joining_fragments(staged_db,tmp_path,monkeypatch):
 r=staged_db[0]();n=create(r,title='图号');monkeypatch.setattr(get_settings(),'file_workspace_root',str(tmp_path))
 text='图号 AZ-08 https://example.invalid/source 后续说明\nhttps://example.invalid/other'
 job=bind(r,n,tmp_path,'opaque.txt',text.encode());enqueue(r,job);asyncio.run(process_file(r,job))
 with r.cursor() as c:
  c.execute('SELECT kb_file_directory(%s,%s,%s,%s) AS terms',tuple(job.values()));terms=c.fetchone()['terms']
 assert terms==['图号 AZ-08','后续说明'] and all(term in text for term in terms)


def test_pending_snapshot_observes_same_version_parse_completion_and_reopens_tools(staged_db,tmp_path,monkeypatch):
 from types import SimpleNamespace
 from tests.test_knowledge_chat_v3 import start,ref
 from services.knowledge_workspace.answer_plan import collect,constrain_route_tools,CHECKPOINT_PREFIX
 r=staged_db[0]();n=create(r,title='异步档案');monkeypatch.setattr(get_settings(),'file_workspace_root',str(tmp_path))
 job=bind(r,n,tmp_path,'异步档案.txt','异步档案中的完整内容'.encode());enqueue(r,job)
 run,_,_=start(staged_db[0],r,[ref(n)]);plan=collect(run,'异步档案内容是什么？')
 step=plan['items'][0]['next_step'];args={k:v for k,v in step.items() if k!='tool'}
 first=run.execute('read',args);file_item=next(i for i in run.snapshot['items'] if i['type']=='file');frozen=file_item.get('text_status')
 prepared=SimpleNamespace(knowledge_answer_plan=plan,messages=[]);tools=[{'function':{'name':'knowledge_read'}}]
 assert asyncio.run(constrain_route_tools(prepared,run,tools))==[]
 asyncio.run(process_file(r,job))
 assert r.files(n['id'])[0]['version']['text_status']=='ready'
 assert asyncio.run(constrain_route_tools(prepared,run,tools))==tools
 assert not getattr(run,'_retrieval_closed',False)
 assert not any(m['content'].startswith(CHECKPOINT_PREFIX) for m in prepared.messages)
 result=run.execute('read',args)
 parsed=next(i for i in result['items'] if i['handle']==file_item['handle'])
 assert parsed['body']=='异步档案中的完整内容' and parsed['text_status']=='ready' and parsed['status']=='complete'
 owner=next(i for i in result['items'] if i['handle']!=file_item['handle'])
 assert owner['attachments'][0]['has_readable_text'] is True
 assert file_item.get('text_status')==frozen
 with r.cursor() as c:assert run._coverage(c,run.snapshot['items'])['full_framework_ready']


@pytest.mark.asyncio
async def test_merged_preparation_routes_named_hierarchical_sources_without_reading_body(staged_db):
 from types import SimpleNamespace
 from dataclasses import replace
 from tests.test_knowledge_chat_v3 import start,ref
 from tests.test_chat_execution_engine import _request
 from schemas.message import TextPart
 from services.knowledge_workspace.answer_plan import prepare
 r=staged_db[0]();root=create(r,title='共享规范目录')
 doc_id=r.command(command('create_object',{'container_id':root['container_id'],'parent_id':root['id'],'kind':'document','title':'内部申请填写说明资料','position':{'x':0,'y':0}}))['object_id']
 r.command(command('save_document',{'object_id':doc_id,'expected_revision':1,'body':'填写申请必须保留原始附件。'}))
 run,_,_=start(staged_db[0],r,[ref(root)])
 handler=SimpleNamespace(_knowledge_run=run,org_id=r.scope.org_id,_personal_context_allowed=True)
 prepared=SimpleNamespace(execution_context=SimpleNamespace(context_scope='user',authorized_tool_names=None),messages=[])
 req=replace(_request(),content=[TextPart(text='“内部申请填写说明资料”有什么要求？')],user_id=r.scope.actor_user_id)
 await prepare(handler,prepared,req)
 assert prepared.knowledge_answer_plan['reason']=='hierarchical_route'
 assert '填写申请必须保留原始附件' not in str(prepared.messages)
 with r.cursor() as c:
  c.execute("SELECT count(*) AS n FROM kb_read_receipts WHERE snapshot_id=%s AND state IN ('body_partial','body_complete')",(run.id,));assert c.fetchone()['n']==0
 step=prepared.knowledge_answer_plan['items'][0]['next_step']
 read=run.execute('read',{k:v for k,v in step.items() if k!='tool'})
 assert any(i['body']=='填写申请必须保留原始附件。' for i in read['items'])
