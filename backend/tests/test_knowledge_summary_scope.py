"""Use real persisted root-level summaries through branch transactions and history."""
from pathlib import Path
from uuid import uuid4
import psycopg
import pytest
from tests.test_knowledge_relation_geometry import geometry_db
from tests.test_knowledge_relation_geometry import groups_db,editing_db,entry_db,rev,directional_db,structure_db,current,visual_db,tools_db,payload,local_db,command,layout_db,before_db,create,toggle,theme_db,rich_db

@pytest.fixture(scope='module')
def summary_db(geometry_db):
 make,actors=geometry_db
 with psycopg.connect(**make.admin_args) as c:c.execute((Path(__file__).resolve().parents[1]/'migrations/327_knowledge_summary_scope.sql').read_text())
 return make,actors

def graph(r):
 root=create(r);a=create(r,root['container_id'],root['id'],'A');b=create(r,root['container_id'],root['id'],'B');out=r.command(command('create_summaries',{'container_id':root['container_id'],'expected_container_revision':rev(r,root['container_id']),'groups':[{'members':[a['id'],b['id']],'title':'概要','position':{'x':700,'y':180}}]}));s=current(r,out['object_id']);child=create(r,root['container_id'],s['id'],'概要子主题');assert s['parent_id'] is None
 return tuple(current(r,n['id']) for n in [root,a,b,s,child])

def selection(r,cid,nodes):return {'container_id':cid,'expected_container_revision':rev(r,cid),'objects':[{'object_id':n['id'],'expected_revision':current(r,n['id'])['revision']} for n in nodes]}
def moves(r,nodes):return [{'node_id':current(r,n['id'])['node_id'],'expected_revision':current(r,n['id'])['layout_revision'],'position':{'x':n['x']+100,'y':n['y']+80}} for n in nodes]

def test_delete_root_includes_summary_and_children_atomically(summary_db):
 r=summary_db[0]();root,a,b,s,child=graph(r);cmd=command('delete_branches',selection(r,root['container_id'],[root]));r.command(cmd);assert not any(n['id'] in {root['id'],a['id'],b['id'],s['id'],child['id']} for n in r.canvas()['nodes']);assert r.command(cmd)['status']=='replayed';toggle(r);assert current(r,s['id'])['canvas_data']['summary']['members']==[str(a['id']),str(b['id'])];assert current(r,a['id'])['parent_id']==root['id'];toggle(r,'redo');assert not any(n['id']==s['id'] for n in r.canvas()['nodes'])

def test_delete_one_member_preserves_and_restores_summary_range(summary_db):
 r=summary_db[0]();root,a,b,s,child=graph(r);r.command(command('delete_branches',selection(r,root['container_id'],[a])));assert current(r,s['id'])['canvas_data']['summary']['members']==[str(b['id'])];assert current(r,child['id'])['parent_id']==s['id'];toggle(r);assert len(current(r,s['id'])['canvas_data']['summary']['members'])==2

def test_whole_branch_movement_requires_and_saves_summary_positions(summary_db):
 r=summary_db[0]();root,a,b,s,child=graph(r);p={'object_id':root['id'],'expected_revision':root['revision'],'expected_container_revision':rev(r,root['container_id']),'target_id':None,'placement':'free','nodes':moves(r,[root,a,b,s,child])};r.command(command('move_branch',p));assert current(r,s['id'])['x']==s['x']+100;assert current(r,child['id'])['y']==child['y']+80;toggle(r);assert current(r,s['id'])['x']==s['x'];p['expected_container_revision']=rev(r,root['container_id']);p['nodes']=moves(r,[root,a,b]);before=r.canvas()
 with pytest.raises(Exception,match='KB_INCOMPLETE_BRANCH'):r.command(command('move_branch',p))
 assert r.canvas()==before

@pytest.mark.parametrize('partial',[False,True])
def test_reparent_multiple_members_keeps_original_topic_semantics(summary_db,partial):
 r=summary_db[0]();root,a,b,s,child=graph(r);target=create(r,root['container_id'],root['id'],'新父');selected=[a] if partial else [a,b];all_nodes=[a] if partial else [a,b,s,child];p={**selection(r,root['container_id'],selected),'target_id':target['id'],'placement':'child','nodes':moves(r,all_nodes)};r.command(command('move_branches',p));assert current(r,a['id'])['parent_id']==target['id'];assert current(r,s['id'])['parent_id'] is None;assert current(r,s['id'])['canvas_data']['summary']['members']==([str(b['id'])] if partial else [str(a['id']),str(b['id'])]);toggle(r);assert current(r,a['id'])['parent_id']==root['id'];assert len(current(r,s['id'])['canvas_data']['summary']['members'])==2

def test_copy_complete_scope_remaps_summary_and_descendants(summary_db):
 r=summary_db[0]();root,a,b,s,child=graph(r);ns=[root,a,b,s,child];p={**selection(r,root['container_id'],ns),'snapshot_id':str(uuid4()),'parent_id':None,'offset':{'x':900,'y':0}};r.branch_snapshot(p['snapshot_id'],command('copy_branches',p).payload.model_dump(mode='json'));r.command(command('copy_branches',p));copies=[n for n in r.canvas()['nodes'] if n['container_id']==root['container_id'] and n['id'] not in {v['id'] for v in ns}];cs=next(n for n in copies if n['title']=='概要');assert set(cs['canvas_data']['summary']['members']).isdisjoint([str(a['id']),str(b['id'])]);assert any(n['parent_id']==cs['id'] for n in copies);toggle(r);assert not any(n['id']==cs['id'] for n in r.canvas()['nodes']);toggle(r,'redo');assert current(r,cs['id'])['canvas_data']==cs['canvas_data']

def test_delete_single_promotes_members_in_summary_without_orphaning(summary_db):
 r=summary_db[0]();root,a,b,s,child=graph(r);kid=create(r,root['container_id'],a['id'],'提升');r.command(command('delete_topic',{'container_id':root['container_id'],'expected_container_revision':rev(r,root['container_id']),'object_id':a['id'],'expected_revision':a['revision'],'nodes':[]}));assert current(r,s['id'])['canvas_data']['summary']['members']==[str(kid['id']),str(b['id'])];toggle(r);assert current(r,kid['id'])['parent_id']==a['id']

def test_delete_summary_only_keeps_original_branches(summary_db):
 r=summary_db[0]();root,a,b,s,child=graph(r);r.command(command('delete_branches',selection(r,root['container_id'],[s])));assert current(r,a['id'])['parent_id']==root['id'];assert current(r,b['id'])['parent_id']==root['id'];assert not any(n['id'] in {s['id'],child['id']} for n in r.canvas()['nodes']);toggle(r);assert current(r,s['id'])['canvas_data']['summary']['members']==[str(a['id']),str(b['id'])]

@pytest.mark.parametrize('mode',['permission','stale','layout'])
def test_summary_scope_keeps_acl_version_and_atomic_layout_guards(summary_db,mode):
 make,_=summary_db;r=make();root,a,b,s,child=graph(r);p=selection(r,root['container_id'],[a]);actor=r
 if mode=='permission':actor=make(1)
 if mode=='stale':p['expected_container_revision']+=1
 if mode=='layout':p['nodes']=[{'node_id':s['node_id'],'expected_revision':999,'position':{'x':80,'y':90}}]
 before=r.canvas()
 with pytest.raises(Exception):actor.command(command('delete_branches',p))
 assert r.canvas()==before

from tests.test_knowledge_files import file_client,url


def test_complete_mixed_branch_workflow_keeps_pinned_media_ranges_relations_and_history(summary_db,file_client):
 client,_,_,r,make=file_client
 root,a,b,s,child=graph(r);cid=root['container_id']
 def upload(node,name,content,mime,**extra):
  response=client.post(f'/api/knowledge/objects/{node["id"]}/files',data={'command_id':str(uuid4()),**extra},files={'file':(name,content,mime)})
  assert response.status_code==200,response.text
  return response.json()['data']['binding_id']
 picture=b'\x89PNG\r\n\x1a\nscenario-original';video=b'\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom'
 image_id=upload(a,'scene.png',picture,'image/png');video_id=upload(b,'scene.mp4',video,'video/mp4')
 upload(a,'scene.png',picture+b'-new','image/png',binding_id=image_id,expected_revision='1')
 frames=[{'id':str(uuid4()),'members':[str(a['id']),str(b['id'])],'title':'处理范围'},{'id':str(uuid4()),'members':[str(a['id'])],'title':'单独范围'}]
 adata={'image':{'binding_id':image_id,'revision':1,'width':180,'placement':'top'},'note':'售后备注','labels':['售后'],'boundaries':frames,'title_format':{'align':'right','runs':[{'text':'A','bold':True}]}}
 bdata={'video':{'binding_id':video_id,'revision':1,'title':'培训视频'},'link':{'url':'https://example.com/guide','title':'操作说明'},'task':{'progress':50,'priority':2,'assignee':'','start':'','due':''}}
 pa=payload(r,[current(r,a['id'])],adata);pb=payload(r,[current(r,b['id'])],bdata);pa['updates']+=pb['updates'];r.command(command('update_canvas_data',pa))
 r.command(command('connect',{'source_object_id':a['id'],'target_object_id':b['id'],'relation_type':'related'}));line=next(e for e in r.canvas()['relations'] if e['container_id']==cid)
 appearance={'text':'处理依赖','source_control':{'x':800,'y':-1200},'target_control':{'x':-400,'y':200},'bend':0}
 r.command(command('update_relation',{'container_id':cid,'expected_container_revision':rev(r,cid),'relation_id':line['id'],'appearance':appearance}))
 settings={'auto_balance':True,'compact':True,'equal_width':True}
 r.command(command('update_map_theme',{'container_id':cid,'expected_container_revision':rev(r,cid),'theme':{'layout':settings},'updates':[],'nodes':[]}))
 def semantics():
  scene=r.canvas();assert next(c for c in scene['containers'] if c['id']==cid)['canvas_theme']['layout']==settings
  return {'nodes':sorted([(str(n['id']),str(n['parent_id']),n['title'],n['x'],n['y'],n['sort_order'],str(n['canvas_data'])) for n in scene['nodes'] if n['container_id']==cid]),'relations':[(str(e['id']),str(e['source_object_id']),str(e['target_object_id']),str(e['appearance'])) for e in scene['relations'] if e['container_id']==cid]}
 baseline=semantics();ns=[current(r,n['id']) for n in [root,a,b,s,child]]
 r.command(command('move_branch',{'object_id':root['id'],'expected_revision':current(r,root['id'])['revision'],'expected_container_revision':rev(r,cid),'target_id':None,'placement':'free','nodes':moves(r,ns)}));moved=semantics()
 r.command(command('delete_branches',selection(r,cid,[current(r,a['id'])])));deleted=semantics();assert current(r,s['id'])['canvas_data']['summary']['members']==[str(b['id'])]
 toggle(r);assert semantics()==moved;toggle(r);assert semantics()==baseline;toggle(r,'redo');assert semantics()==moved;toggle(r,'redo');assert semantics()==deleted;toggle(r);assert semantics()==moved
 ns=[current(r,n['id']) for n in ns];p={**selection(r,cid,ns),'snapshot_id':str(uuid4()),'parent_id':None,'offset':{'x':1200,'y':100}}
 r.branch_snapshot(p['snapshot_id'],command('copy_branches',p).payload.model_dump(mode='json'));r.command(command('copy_branches',p))
 copies=[n for n in r.canvas()['nodes'] if n['container_id']==cid and n['id'] not in {v['id'] for v in ns}];assert len(copies)==5
 ca=next(n for n in copies if n['title']=='A');cb=next(n for n in copies if n['title']=='B');cs=next(n for n in copies if n['title']=='概要')
 assert set(cs['canvas_data']['summary']['members'])=={str(ca['id']),str(cb['id'])};assert len(ca['canvas_data']['boundaries'])==2;assert ca['canvas_data']['title_format']==adata['title_format'];assert cb['canvas_data']['task']==bdata['task']
 assert client.get(url(ca['id'],ca['canvas_data']['image']['binding_id'])).content==picture;assert client.get(url(cb['id'],cb['canvas_data']['video']['binding_id'])).content==video
 assert make(1).file_version(ca['id'],ca['canvas_data']['image']['binding_id'],1) is None
 assert any(e['source_object_id']==ca['id'] and e['target_object_id']==cb['id'] and e['appearance']['text']=='处理依赖' for e in r.canvas()['relations'])
 toggle(r);assert semantics()==moved;toggle(r,'redo');assert current(r,ca['id'])['canvas_data']==ca['canvas_data']
