"""Container theme and explicit style resets share one history transaction."""
from pathlib import Path
import psycopg
import pytest
from tests.test_knowledge_topic_editing import editing_db
from tests.test_knowledge_topic_entry import entry_db,rev
from tests.test_knowledge_directional_paste import directional_db
from tests.test_knowledge_topic_structure import structure_db,current
from tests.test_knowledge_canvas_visual import visual_db
from tests.test_knowledge_canvas_tools import tools_db,payload
from tests.test_knowledge_workspace_v2 import local_db,command
from tests.test_knowledge_title_layout import layout_db
from tests.test_knowledge_insert_before import before_db
from tests.test_knowledge_branch_drag import create
from tests.test_knowledge_canvas_undo import toggle

from tests.test_knowledge_map_theme import theme_db

from tests.test_knowledge_rich_titles import rich_db

@pytest.fixture(scope='module')
def groups_db(rich_db):
 make,actors=rich_db
 with psycopg.connect(**make.admin_args) as c:c.execute((Path(__file__).resolve().parents[1]/'migrations/325_knowledge_independent_boundaries.sql').read_text())
 return make,actors

from uuid import uuid4

def boundaries(r,n):return current(r,n['id'])['canvas_data'].get('boundaries',[])
def frame(members,title='外框'):return {'id':str(uuid4()),'members':[str(m) for m in members],'title':title}
def update(r,n,data):return r.command(command('update_canvas_data',payload(r,[current(r,n['id'])],data)))

def test_multiple_frames_legacy_preserved_and_independent_history(groups_db):
 r=groups_db[0]();root=create(r);a=create(r,root['container_id'],root['id'],'A');b=create(r,root['container_id'],root['id'],'B');legacy={'members':[str(a['id'])],'title':'旧外框'};one=frame([a['id'],b['id']],'范围一');two=frame([a['id']],'范围二');update(r,a,{'boundary':legacy,'boundaries':[one]});p=payload(r,[current(r,a['id'])],{'boundary':legacy,'boundaries':[one,two]});cmd=command('update_canvas_data',p);r.command(cmd);assert r.command(cmd)['status']=='replayed';assert boundaries(r,a)==[one,two];assert current(r,a['id'])['canvas_data']['boundary']==legacy;update(r,a,{'boundary':legacy,'boundaries':[one]});assert boundaries(r,a)==[one];assert current(r,b['id'])['title']=='B';toggle(r);assert boundaries(r,a)==[one,two];toggle(r,'redo');assert boundaries(r,a)==[one]

def test_copy_remaps_frame_ids_members_and_history(groups_db):
 r=groups_db[0]();root=create(r);a=create(r,root['container_id'],root['id'],'A');b=create(r,root['container_id'],root['id'],'B');g=frame([a['id'],b['id']]);update(r,a,{'boundaries':[g]});ns=[current(r,x['id']) for x in [root,a,b]];p={'snapshot_id':str(uuid4()),'container_id':root['container_id'],'expected_container_revision':rev(r,root['container_id']),'objects':[{'object_id':n['id'],'expected_revision':n['revision']} for n in ns],'parent_id':None,'offset':{'x':800,'y':0}};r.branch_snapshot(p['snapshot_id'],command('copy_branches',p).payload.model_dump(mode='json'));r.command(command('copy_branches',p));copy=next(n for n in r.canvas()['nodes'] if n['container_id']==root['container_id'] and n['title']=='A' and n['id']!=a['id']);cg=copy['canvas_data']['boundaries'][0];assert cg['id']!=g['id'];assert set(cg['members']).isdisjoint(g['members']);toggle(r);toggle(r,'redo');assert current(r,copy['id'])['canvas_data']['boundaries'][0]==cg

def test_parent_insertion_moves_frame_and_rewrites_range(groups_db):
 r=groups_db[0]();root=create(r);a=create(r,root['container_id'],root['id'],'A');b=create(r,root['container_id'],root['id'],'B');g=frame([a['id'],b['id']]);update(r,a,{'boundaries':[g]});a=current(r,a['id']);p={'container_id':root['container_id'],'expected_container_revision':rev(r,root['container_id']),'object_id':a['id'],'expected_revision':a['revision'],'parent_id':root['id'],'topics':[{'title':'父主题','parent_index':-1,'position':{'x':300,'y':300}}]};out=r.command(command('insert_parent',p));parent=current(r,out['object_id']);assert parent['canvas_data']['boundaries'][0]['members']==[str(parent['id']),str(b['id'])];assert not boundaries(r,a);toggle(r);assert boundaries(r,a)==[g]

def test_delete_only_promotes_children_and_preserves_frames(groups_db):
 r=groups_db[0]();root=create(r);a=create(r,root['container_id'],root['id'],'A');kid=create(r,root['container_id'],a['id'],'子主题');b=create(r,root['container_id'],root['id'],'B');g=frame([a['id'],b['id']]);update(r,a,{'boundaries':[g]});a=current(r,a['id']);r.command(command('delete_topic',{'container_id':a['container_id'],'expected_container_revision':rev(r,a['container_id']),'object_id':a['id'],'expected_revision':a['revision'],'nodes':[]}));frames=[g for n in r.canvas()['nodes'] if n['container_id']==a['container_id'] for g in n['canvas_data'].get('boundaries',[])];assert frames[0]['id']==g['id'];assert frames[0]['members']==[str(kid['id']),str(b['id'])];toggle(r);assert boundaries(r,a)==[g]

@pytest.mark.parametrize('bad',['duplicate','foreign','permission','stale'])
def test_frame_validation_and_guards_rollback(groups_db,bad):
 make,_=groups_db;r=make();root=create(r);a=create(r,root['container_id'],root['id'],'A');g=frame([a['id']]);p=payload(r,[a],{'boundaries':[g]});actor=r
 if bad=='duplicate':p['updates'][0]['canvas_data']['boundaries'].append(g)
 if bad=='foreign':p['updates'][0]['canvas_data']['boundaries'][0]['members']=[create(r)['id']]
 if bad=='permission':actor=make(1)
 if bad=='stale':p['updates'][0]['expected_layout_revision']=999
 before=r.canvas()
 with pytest.raises(Exception):actor.command(command('update_canvas_data',p))
 assert r.canvas()==before

def test_delete_branch_transfers_frame_to_surviving_member(groups_db):
 r=groups_db[0]();root=create(r);a=create(r,root['container_id'],root['id'],'A');b=create(r,root['container_id'],root['id'],'B');g=frame([a['id'],b['id']]);update(r,a,{'boundaries':[g]});a=current(r,a['id']);r.command(command('delete_branches',{'container_id':a['container_id'],'expected_container_revision':rev(r,a['container_id']),'objects':[{'object_id':a['id'],'expected_revision':a['revision']}]}));assert boundaries(r,b)==[{**g,'members':[str(b['id'])]}];toggle(r);assert boundaries(r,a)==[g];assert not boundaries(r,b)

def test_reparenting_host_preserves_frame_on_original_sibling_range(groups_db):
 r=groups_db[0]();root=create(r);a=create(r,root['container_id'],root['id'],'A');b=create(r,root['container_id'],root['id'],'B');other=create(r,root['container_id'],root['id'],'其他');g=frame([a['id'],b['id']]);update(r,a,{'boundaries':[g]});a=current(r,a['id']);r.command(command('move_branch',{'object_id':a['id'],'expected_revision':a['revision'],'expected_container_revision':rev(r,a['container_id']),'target_id':other['id'],'placement':'child','nodes':[{'node_id':a['node_id'],'expected_revision':a['layout_revision'],'position':{'x':700,'y':600}}]}));assert boundaries(r,b)==[{**g,'members':[str(b['id'])]}];assert not boundaries(r,a);toggle(r);assert boundaries(r,a)==[g]

from tests.test_knowledge_files import file_client,url

def test_boundary_copy_keeps_pinned_image_bytes(groups_db,file_client):
 client,_,_,r,_=file_client;root=create(r);a=create(r,root['container_id'],root['id'],'A');b=create(r,root['container_id'],root['id'],'B');response=client.post(f'/api/knowledge/objects/{a["id"]}/files',data={'command_id':str(uuid4())},files={'file':('image.png',b'\x89PNG\r\n\x1a\nold','image/png')});assert response.status_code==200,response.text;bid=response.json()['data']['binding_id'];response=client.post(f'/api/knowledge/objects/{a["id"]}/files',data={'command_id':str(uuid4()),'binding_id':bid,'expected_revision':'1'},files={'file':('image.png',b'\x89PNG\r\n\x1a\nnew','image/png')});assert response.status_code==200,response.text;g=frame([a['id'],b['id']]);update(r,a,{'boundaries':[g],'image':{'binding_id':bid,'revision':1,'width':120,'placement':'top'}});scene=r.canvas();p={'snapshot_id':str(uuid4()),'container_id':a['container_id'],'expected_container_revision':rev(r,a['container_id']),'objects':[{'object_id':n['id'],'expected_revision':n['revision']} for n in scene['nodes'] if n['container_id']==a['container_id']],'parent_id':None,'offset':{'x':800,'y':0}};r.branch_snapshot(p['snapshot_id'],command('copy_branches',p).payload.model_dump(mode='json'));r.command(command('copy_branches',p));copied=next(n for n in r.canvas()['nodes'] if n['container_id']==a['container_id'] and n['title']=='A' and n['id']!=a['id']);image=copied['canvas_data']['image'];assert image['binding_id']!=bid;assert image['revision']==1;assert client.get(url(copied['id'],image['binding_id'])).content.endswith(b'old');assert copied['canvas_data']['boundaries'][0]['members']!=g['members']
