"""Single-topic deletion preserves promoted content and atomic history."""
from pathlib import Path
import psycopg
import pytest
from tests.test_knowledge_topic_entry import entry_db, rev
from tests.test_knowledge_directional_paste import directional_db
from tests.test_knowledge_topic_structure import structure_db,current
from tests.test_knowledge_canvas_visual import visual_db
from tests.test_knowledge_canvas_tools import tools_db,payload
from tests.test_knowledge_workspace_v2 import local_db,command
from tests.test_knowledge_title_layout import layout_db
from tests.test_knowledge_insert_before import before_db
from tests.test_knowledge_branch_drag import create
from tests.test_knowledge_canvas_undo import toggle

@pytest.fixture(scope='module')
def editing_db(entry_db):
 make,actors=entry_db
 with psycopg.connect(**make.admin_args) as c:c.execute((Path(__file__).resolve().parents[1]/'migrations/322_knowledge_topic_editing.sql').read_text())
 return make,actors

def request(r,n):return {'container_id':n['container_id'],'expected_container_revision':rev(r,n['container_id']),'object_id':n['id'],'expected_revision':n['revision'],'nodes':[]}

def test_delete_only_parent_preserves_order_body_and_undo(editing_db):
 make,_=editing_db;r=make();root=create(r);cid=root['container_id'];a=create(r,cid,root['id'],'A');b=create(r,cid,root['id'],'B');oid=r.command(command('create_object',{'container_id':cid,'parent_id':a['id'],'kind':'document','title':'文档','body':'保留正文','position':{'x':500,'y':0}}))['object_id'];c=create(r,cid,a['id'],'C');leaf=create(r,cid,oid,'后代');cmd=command('delete_topic',request(r,a));r.command(cmd);assert r.command(cmd)['status']=='replayed';nodes=r.canvas()['nodes'];assert not any(n['id']==a['id'] for n in nodes);assert r.object(oid)['body']=='保留正文';assert current(r,oid)['parent_id']==root['id'];assert str(current(r,leaf['id'])['parent_id'])==oid;assert current(r,oid)['sort_order']<current(r,c['id'])['sort_order']<current(r,b['id'])['sort_order'];toggle(r);assert current(r,oid)['parent_id']==a['id'];assert current(r,a['id']);toggle(r,'redo');assert current(r,oid)['parent_id']==root['id']

def test_delete_root_promotes_children_and_leaf_is_recoverable(editing_db):
 make,_=editing_db;r=make();root=create(r);a=create(r,root['container_id'],root['id'],'A');r.command(command('delete_topic',request(r,root)));assert current(r,a['id'])['parent_id'] is None;toggle(r);assert current(r,a['id'])['parent_id']==root['id'];r.command(command('delete_topic',request(r,current(r,a['id']))));assert not any(n['id']==a['id'] for n in r.canvas()['nodes']);toggle(r);assert current(r,a['id'])

@pytest.mark.parametrize('bad',['version','container','permission','layout'])
def test_failed_delete_is_atomic(editing_db,bad):
 make,_=editing_db;r=make();a=create(r);p=request(r,a);actor=r
 if bad=='version':p['expected_revision']+=1
 if bad=='container':p['expected_container_revision']+=1
 if bad=='permission':actor=make(1)
 if bad=='layout':p['nodes']=[{'node_id':current(r,a['id'])['node_id'],'expected_revision':999,'position':{'x':1,'y':2}}]
 before=r.canvas()
 with pytest.raises(Exception,match='KB_'):actor.command(command('delete_topic',p))
 assert r.canvas()==before

def test_width_is_versioned_and_undoable(editing_db):
 make,_=editing_db;r=make();a=create(r);r.command(command('update_canvas_data',payload(r,[a],{'width':400})));assert current(r,a['id'])['canvas_data']['width']==400;toggle(r);assert 'width' not in current(r,a['id'])['canvas_data'];toggle(r,'redo');assert current(r,a['id'])['canvas_data']['width']==400

@pytest.mark.parametrize('width',[79,801,250.5])
def test_bad_width_rejected(width):
 from services.knowledge_workspace.contracts import CanvasData
 with pytest.raises(ValueError):CanvasData(width=width)

def test_delete_updates_summary_ranges_and_undo_restores_them(editing_db):
 make,_=editing_db;r=make();root=create(r);cid=root['container_id'];a=create(r,cid,root['id'],'A');b=create(r,cid,root['id'],'B');child=create(r,cid,a['id'],'Child');sid=r.command(command('create_summaries',{'container_id':cid,'expected_container_revision':rev(r,cid),'groups':[{'title':'概要','members':[a['id'],b['id']],'position':{'x':900,'y':0}}]}))['object_id'];r.command(command('delete_topic',request(r,a)));assert current(r,sid)['canvas_data']['summary']['members']==[str(child['id']),str(b['id'])];toggle(r);assert current(r,sid)['canvas_data']['summary']['members']==[str(a['id']),str(b['id'])]

def test_multiline_title_roundtrips_and_undo_preserves_old_title(editing_db):
 make,_=editing_db;r=make();a=create(r);r.command(command('update_title',{'object_id':a['id'],'expected_revision':a['revision'],'title':'第一行\n第二行'}));assert current(r,a['id'])['title']=='第一行\n第二行';toggle(r);assert current(r,a['id'])['title']==a['title'];toggle(r,'redo');assert current(r,a['id'])['title']=='第一行\n第二行'

def test_reorder_complete_branch_keeps_identity_and_is_undoable(editing_db):
 make,_=editing_db;r=make();root=create(r);cid=root['container_id'];a=create(r,cid,root['id'],'A');b=create(r,cid,root['id'],'B');child=create(r,cid,b['id'],'Child');moved=[current(r,x['id']) for x in [b,child]];r.command(command('move_branch',{'object_id':b['id'],'expected_revision':b['revision'],'expected_container_revision':rev(r,cid),'target_id':a['id'],'placement':'before','nodes':[{'node_id':n['node_id'],'expected_revision':n['layout_revision'],'position':{'x':n['x'],'y':n['y']-200}} for n in moved]}));assert current(r,b['id'])['sort_order']<current(r,a['id'])['sort_order'];assert current(r,child['id'])['parent_id']==b['id'];toggle(r);assert current(r,a['id'])['sort_order']<current(r,b['id'])['sort_order'];toggle(r,'redo');assert current(r,b['id'])['sort_order']<current(r,a['id'])['sort_order']

def test_boundary_range_survives_its_storage_node_deletion(editing_db):
 make,_=editing_db;r=make();root=create(r);cid=root['container_id'];a=create(r,cid,root['id'],'A');b=create(r,cid,root['id'],'B');child=create(r,cid,a['id'],'Child');r.command(command('update_canvas_data',payload(r,[a],{'boundary':{'title':'范围','members':[a['id'],b['id']]}})));r.command(command('delete_topic',request(r,current(r,a['id']))));groups=[n['canvas_data']['boundary'] for n in r.canvas()['nodes'] if n.get('canvas_data',{}).get('boundary')];assert len(groups)==1;assert groups[0]['members']==[str(child['id']),str(b['id'])];toggle(r);assert current(r,a['id'])['canvas_data']['boundary']['members']==[str(a['id']),str(b['id'])]
