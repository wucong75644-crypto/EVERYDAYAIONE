"""Imports append to a selected ACL scope without creating containers or memberships."""
import copy
from pathlib import Path
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
import psycopg
import pytest
from core.db_scope import DatabaseScope,DatabaseAccessKind
from services.knowledge_workspace.repository import Repository
from tests.test_knowledge_xmind import (import_db,prepared,archive,project_delete_db,derived_db,cleanup_db,resize_db,manual_db,summary_db,geometry_db,groups_db,editing_db,entry_db,rev,directional_db,structure_db,current,visual_db,tools_db,payload,local_db,command,layout_db,before_db,create,toggle,theme_db,rich_db,no_real_models)

@pytest.fixture(scope='module')
def space_db(import_db):
 make,actors=import_db
 with psycopg.connect(**make.admin_args) as c:
  for name in ['337_knowledge_import_current_space.sql','338_knowledge_source_presentation.sql']:c.execute((Path(__file__).resolve().parents[1]/'migrations'/name).read_text())
 return make,actors

def request(r,tmp_path,cid,key=None):
 key,p=prepared(tmp_path,r,key)
 p.update(container_id=str(cid),expected_container_revision=next(c['revision'] for c in r.canvas()['containers'] if str(c['id'])==str(cid)))
 return key,p

@pytest.mark.parametrize('kind,personal_only',[('project',False),('team',False),('personal',False),('personal',True)])
def test_import_preserves_space_existing_nodes_and_roundtrips_undo(space_db,tmp_path,kind,personal_only):
 make,_=space_db;r=make()
 if personal_only:r=Repository(r.pool,DatabaseScope(r.scope.actor_user_id,None,DatabaseAccessKind.RUNTIME))
 r.initialize()
 cid=create(r,title='既有项目')['container_id'] if kind=='project' else next(c['id'] for c in r.canvas()['containers'] if c['kind']==kind)
 existing=create(r,cid,None,'已有正文不可覆盖');before=r.canvas();key,p=request(r,tmp_path,cid)
 result=r.import_xmind(key,p);assert result['container_id']==str(cid)
 after=r.canvas();assert {c['id'] for c in after['containers']}=={c['id'] for c in before['containers']}
 imported=[n for n in after['nodes'] if n['id'] not in {v['id'] for v in before['nodes']}]
 assert len(imported)==5 and all(str(n['container_id'])==str(cid) for n in imported)
 assert r.object(existing['id'])==existing
 assert len(r.files(result['object_id']))==1
 image=next(n for n in imported if n['canvas_data'].get('image'));assert r.file_version(image['id'],image['canvas_data']['image']['binding_id'],1)
 assert min(n['y'] for n in imported)>next(n['y'] for n in before['nodes'] if n['id']==existing['id'])
 assert r.import_xmind(key,p)['status']=='replayed'
 assert r.xmind_receipt(key,p['sha256'],'synthetic.xmind',cid,p['expected_container_revision'])['status']=='replayed'
 toggle(r);assert {n['id'] for n in r.canvas()['nodes']}=={n['id'] for n in before['nodes']}
 assert any(str(c['id'])==str(cid) for c in r.canvas()['containers'])
 assert r.import_xmind(key,p)['hidden'] and r.xmind_receipt(key,p['sha256'],'synthetic.xmind',cid,p['expected_container_revision'])['hidden']
 toggle(r,'redo');assert r.object(result['object_id']) and r.object(existing['id'])==existing


def test_editor_import_inherits_membership_and_denies_viewer_wrong_org(space_db,tmp_path):
 make,actors=space_db;r=make();target=create(r,title='共享项目');cid=target['container_id']
 r.command(command('set_member',{'container_id':cid,'user_id':actors[1],'role':'editor','status':'active'}))
 r.command(command('set_member',{'container_id':cid,'user_id':actors[2],'role':'viewer','status':'active'}))
 editor=make(1);key,p=request(editor,tmp_path,cid);result=editor.import_xmind(key,p)
 assert make(2).object(result['object_id']) is not None
 source=make(2).files(result['object_id'])[0];assert make(2).file_version(result['object_id'],source['id'],1) is not None
 assert next(c['role'] for c in editor.canvas()['containers'] if str(c['id'])==str(cid))=='editor'
 assert editor.import_xmind(key,p)['status']=='replayed'
 for denied in [make(2),make(0,1)]:
  k,bad=prepared(tmp_path,denied);bad.update(container_id=str(cid),expected_container_revision=p['expected_container_revision'])
  with pytest.raises(psycopg.Error,match='KB_ACCESS_DENIED'):denied.import_xmind(k,bad)


def test_invalid_import_rolls_back_existing_space_and_stale_target_conflicts(space_db,tmp_path):
 r=space_db[0]();target=create(r,title='回滚目标');key,p=request(r,tmp_path,target['container_id']);before=r.canvas()
 bad=copy.deepcopy(p);bad['topics'][-1]['summary']=[9999]
 with pytest.raises(psycopg.Error):r.import_xmind(key,bad)
 assert r.canvas()==before
 stale=copy.deepcopy(p);stale['expected_container_revision']-=1
 with pytest.raises(psycopg.Error,match='KB_VERSION_CONFLICT'):r.import_xmind(key,stale)
 assert r.import_xmind(key,p)['status']=='applied'
 changed=copy.deepcopy(p);changed['container_id']=str(create(r,title='另一个目标')['container_id'])
 with pytest.raises(psycopg.Error,match='KB_IDEMPOTENCY_PAYLOAD_CONFLICT'):r.import_xmind(key,changed)


def test_concurrent_retry_into_one_space_creates_exactly_once(space_db,tmp_path):
 make,_=space_db;r=make();target=create(r,title='并发导入目标');key,p=request(r,tmp_path,target['container_id'])
 with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:make().import_xmind(key,p),range(2)))
 assert {v['status'] for v in results}=={'applied','replayed'}
 assert len([n for n in r.canvas()['nodes'] if str(n['container_id'])==str(target['container_id'])])==6


def test_multipart_import_uses_target_receipt_and_rejects_missing_or_readonly_target(space_db,tmp_path,monkeypatch):
 import io
 from types import SimpleNamespace
 from fastapi import FastAPI
 from fastapi.testclient import TestClient
 from api import deps
 from api.routes import knowledge_files as route
 from core.config import get_settings
 from services.knowledge_workspace import xmind
 make,actors=space_db;r=make();target=create(r,title='接口目标');cid=str(target['container_id'])
 r.command(command('set_member',{'container_id':cid,'user_id':actors[2],'role':'viewer','status':'active'}))
 revision=next(c['revision'] for c in r.canvas()['containers'] if str(c['id'])==cid)
 ctx=SimpleNamespace(user_id=r.scope.actor_user_id,org_id=r.scope.org_id)
 monkeypatch.setattr(get_settings(),'file_workspace_root',str(tmp_path));monkeypatch.setattr(get_settings(),'file_workspace_enabled',True);monkeypatch.setattr(route,'enabled',lambda _:True)
 app=FastAPI();app.include_router(route.router);app.dependency_overrides[deps.get_org_context]=lambda:ctx;app.dependency_overrides[deps.get_db]=lambda:SimpleNamespace(pool=r.pool)
 key=str(uuid4());body=archive();before=len(r.canvas()['containers'])
 with TestClient(app) as client:
  def post(fields=None):return client.post('/knowledge/imports/xmind',data=fields or {'command_id':key,'container_id':cid,'expected_container_revision':revision},files={'file':('synthetic.xmind',body,'application/octet-stream')})
  response=post();assert response.status_code==200 and response.json()['data']['container_id']==cid
  assert len(r.canvas()['containers'])==before
  monkeypatch.setattr(xmind,'parse',lambda _:pytest.fail('receipt replay or rejected target must not parse again'))
  assert post().json()['data']['status']=='replayed'
  assert post({'command_id':key}).status_code==422
  ctx.user_id=actors[2];assert post().status_code==403


def test_source_presentation_and_relationships_are_atomic_and_reversible(space_db,tmp_path):
 from services.knowledge_workspace import xmind
 from tests.test_knowledge_source_canvas import source_archive
 r=space_db[0]();target=create(r,title='保留已有内容');key=uuid4();raw=source_archive()
 p=xmind.prepare(xmind.parse(raw),raw,'source.xmind',tmp_path,r.scope.actor_user_id,r.scope.org_id,key)
 p.update(container_id=str(target['container_id']),expected_container_revision=rev(r,target['container_id']))
 result=r.import_xmind(key,p);scene=r.canvas();root=next(n for n in scene['nodes'] if str(n['id'])==result['object_id'])
 assert root['canvas_data']['boundaries'][0]['title']=='原始外框'
 assert len(root['canvas_data']['boundaries'][0]['members'])==2
 assert root['canvas_data']['structure']=='left'
 assert root['canvas_data']['style']['font_size']==32
 child=next(n for n in scene['nodes'] if n['title']=='ERP编码 XYDSLBQB01-01')
 assert child['x']<root['x']
 assert child['canvas_data']['width']==300
 assert child['canvas_data']['title_format']['runs'][1]['color']=='#F22816'
 relation=next(a for a in scene['relations'] if str(a['source_object_id'])==str(child['id']))
 assert relation['appearance']['text']=='人工建立的关联'
 assert r.import_xmind(key,p)['status']=='replayed'
 toggle(r);assert not any(str(a['id'])==str(relation['id']) for a in r.canvas()['relations'])
 toggle(r,'redo');restored=r.canvas();assert any(str(a['id'])==str(relation['id']) for a in restored['relations'])
 assert next(n for n in restored['nodes'] if str(n['id'])==str(child['id']))['canvas_data']==child['canvas_data']


def test_invalid_source_presentation_and_relationship_cannot_partially_import(space_db,tmp_path):
 r=space_db[0]();target=create(r,title='目标');key,p=request(r,tmp_path,target['container_id']);before=r.canvas()
 for changes in [dict(relations=[{'source_index':0,'target_index':999,'appearance':{}}]),
                 dict(topics=[{**t,'presentation':{'width':9999}} for t in p['topics']])]:
  with pytest.raises(psycopg.Error):r.import_xmind(key,{**p,**changes})
  assert r.canvas()==before


def test_scene_exposes_body_availability_without_copying_text_or_leaking_other_spaces(space_db):
 r=space_db[0]();n=create(r,title='原始主题');oid=str(n['id'])
 assert next(x for x in r.canvas()['nodes'] if str(x['id'])==oid)['has_body'] is False
 r.command(command('save_document',{'object_id':oid,'expected_revision':1,'body':'原始主题'}))
 assert next(x for x in r.canvas()['nodes'] if str(x['id'])==oid)['has_body'] is False
 r.command(command('save_document',{'object_id':oid,'expected_revision':2,'body':'需要直接显示的完整正文'}))
 assert next(x for x in r.canvas()['nodes'] if str(x['id'])==oid)['has_body'] is True
 assert '需要直接显示的完整正文' not in str(r.canvas())
 assert not any(str(x['id'])==oid for x in space_db[0](1).canvas()['nodes'])


def test_new_import_after_undo_uses_new_command_without_resurrecting_old_receipt(space_db,tmp_path):
 make,_=space_db;r=make();r.initialize();cid=next(c['id'] for c in r.canvas()['containers'] if c['kind']=='team')
 baseline={n['id'] for n in r.canvas()['nodes'] if n['container_id']==cid}
 first_key,first_payload=request(r,tmp_path,cid);first=r.import_xmind(first_key,first_payload);toggle(r)
 assert r.xmind_receipt(first_key,first_payload['sha256'],'synthetic.xmind',cid,first_payload['expected_container_revision'])['hidden']
 new_key,new_payload=request(r,tmp_path,cid);second=r.import_xmind(new_key,new_payload)
 assert second['object_id']!=first['object_id'] and r.object(second['object_id'])
 assert first['object_id'] not in {str(n['id']) for n in r.canvas()['nodes']}
 assert len({n['id'] for n in r.canvas()['nodes'] if n['container_id']==cid}-baseline)==5
 assert r.import_xmind(new_key,new_payload)['status']=='replayed'
 assert r.xmind_receipt(first_key,first_payload['sha256'],'synthetic.xmind',cid,first_payload['expected_container_revision'])['hidden']
