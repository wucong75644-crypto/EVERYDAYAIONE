"""Relative creation order and layout are a single reversible transaction."""
from pathlib import Path
import pytest
import psycopg
from tests.test_knowledge_workspace_v2 import local_db, command
from tests.test_knowledge_branch_drag import create
from tests.test_knowledge_canvas_undo import toggle, visible

@pytest.fixture(scope='module')
def insert_db(local_db):
    make,actors=local_db
    with psycopg.connect(**make.admin_args) as c:
        for name in ['307_knowledge_branch_drag.sql','308_knowledge_canvas_undo.sql','309_knowledge_branch_batch.sql','310_knowledge_group_move.sql','311_knowledge_insert_after.sql']:
            c.execute((Path(__file__).resolve().parents[1]/'migrations'/name).read_text())
    return make,actors

def insertion(r,a):
    scene=r.canvas();cid=a['container_id']
    return {'container_id':cid,'parent_id':a['parent_id'],'title':'Inserted','kind':'topic','body':'正文','position':{'x':100,'y':240},'after_object_id':a['id'],
            'expected_container_revision':next(c['revision'] for c in scene['containers'] if c['id']==cid),
            'nodes':[{'node_id':n['node_id'],'expected_revision':n['layout_revision'],'position':{'x':n['x'],'y':n['y']+300}} for n in scene['nodes'] if n['container_id']==cid and n['id']!=a['id']]}

def test_relative_create_layout_order_history_and_replay(insert_db):
    make,_=insert_db;r=make();root=create(r);cid=root['container_id'];a=create(r,cid,root['id'],'A');b=create(r,cid,root['id'],'B');child=create(r,cid,b['id'],'Child');create(r,cid,root['id'],'C')
    before=visible(r,cid);cmd=command('create_object',insertion(r,a));oid=r.command(cmd)['object_id']
    def titles():return [n['title'] for n in sorted(r.canvas()['nodes'],key=lambda n:n['sort_order']) if n['container_id']==cid and n['parent_id']==root['id']]
    assert titles()==['A','Inserted','B','C'];assert r.object(oid)['body']=='正文'
    after=visible(r,cid);assert r.command(cmd)['status']=='replayed' and visible(r,cid)==after
    toggle(r);assert visible(r,cid)==before;toggle(r,'redo');assert visible(r,cid)==after

@pytest.mark.parametrize('bad',['anchor','container','layout','duplicate','cross_layout','permission'])
def test_rejected_insertion_never_leaves_new_object_or_partial_layout(insert_db,bad):
    make,_=insert_db;r=make();root=create(r);a=create(r,root['container_id'],root['id'],'A');create(r,root['container_id'],root['id'],'B');p=insertion(r,a);actor=r
    if bad=='anchor':p['after_object_id']=create(r)['id']
    if bad=='container':p['expected_container_revision']-=1
    if bad=='layout':p['nodes'][0]['expected_revision']=99
    if bad=='duplicate':p['nodes'].append(p['nodes'][0])
    if bad=='cross_layout':
        other=create(r);p['nodes'][0]['node_id']=next(n['node_id'] for n in r.canvas()['nodes'] if n['id']==other['id'])
    if bad=='permission':actor=make(1)
    before=r.canvas()
    with pytest.raises(Exception,match='KB_'):actor.command(command('create_object',p))
    assert r.canvas()==before

def test_history_exposes_authorized_target_space_and_hides_revoked_title(insert_db):
    make,actors=insert_db;r=make();root=create(r,title='历史操作空间');cid=root['container_id']
    entry=r.canvas_history()['undo'];assert str(entry['container_id'])==str(cid) and entry['container_title']=='历史操作空间'
    r.command(command('set_member',{'container_id':cid,'user_id':actors[1],'role':'editor','status':'active'}))
    other=make(1);create(other,cid,root['id'])
    r.command(command('set_member',{'container_id':cid,'user_id':actors[1],'role':'viewer','status':'removed'}))
    entry=other.canvas_history()['undo'];assert not entry['can_apply'] and entry['container_title'] is None and entry['container_kind'] is None
