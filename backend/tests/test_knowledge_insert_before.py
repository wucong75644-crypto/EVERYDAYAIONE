"""Shift+Enter uses atomic before-anchor creation and backward-compatible receipts."""
from pathlib import Path
import psycopg
import pytest
from tests.test_knowledge_workspace_v2 import local_db, command
from tests.test_knowledge_title_layout import layout_db
from tests.test_knowledge_branch_drag import create
from tests.test_knowledge_canvas_undo import toggle, visible

@pytest.fixture(scope='module')
def before_db(layout_db):
    make, actors = layout_db
    with psycopg.connect(**make.admin_args) as c:
        c.execute((Path(__file__).resolve().parents[1]/'migrations/315_knowledge_insert_before.sql').read_text())
    return make, actors

def setup(r):
    root=create(r);cid=root['container_id'];a=create(r,cid,root['id'],'A');b=create(r,cid,root['id'],'B');scene=r.canvas()
    return cid, {'container_id':cid,'parent_id':root['id'],'title':'Before B','kind':'topic','body':'','position':{'x':300,'y':200},'before_object_id':b['id'], 'expected_container_revision':next(c['revision'] for c in scene['containers'] if c['id']==cid),'nodes':[{'node_id':n['node_id'],'expected_revision':n['layout_revision'],'position':{'x':n['x'],'y':n['y']+200}} for n in scene['nodes'] if n['id']==b['id']]}

def test_before_order_layout_history_and_receipt(before_db):
    make,_=before_db;r=make();cid,p=setup(r);before=visible(r,cid);cmd=command('create_object',p);oid=r.command(cmd)['object_id'];after=visible(r,cid)
    assert [n['title'] for n in sorted(r.canvas()['nodes'],key=lambda n:n['sort_order']) if n['parent_id']==p['parent_id']]==['A','Before B','B']
    assert r.command(cmd)['object_id']==oid
    toggle(r);assert visible(r,cid)==before
    toggle(r,'redo');assert visible(r,cid)==after

@pytest.mark.parametrize('bad',['anchor','layout','container','both','permission'])
def test_invalid_before_is_atomic(before_db,bad):
    make,_=before_db;r=make();cid,p=setup(r);actor=r
    if bad=='anchor':p['before_object_id']=create(r)['id']
    if bad=='layout':p['nodes'][0]['expected_revision']=999
    if bad=='container':p['expected_container_revision']-=1
    if bad=='both':p['after_object_id']=p['before_object_id']
    if bad=='permission':actor=make(1)
    old=r.canvas()
    with pytest.raises(Exception,match='KB_'):actor.command(command('create_object',p))
    assert r.canvas()==old

def test_old_create_receipts_do_not_gain_a_null_before_field(before_db):
    make,_=before_db;r=make();root=create(r);cmd=command('create_object',{'container_id':root['container_id'],'parent_id':root['id'],'title':'Old Client','kind':'topic','body':'','position':{'x':300,'y':200}})
    r.command(cmd)
    with psycopg.connect(**make.admin_args) as c:
        assert 'before_object_id' not in c.execute('SELECT request_payload FROM public.kb_mutation_receipts WHERE command_id=%s',(cmd.command_id,)).fetchone()[0]['payload']
    assert r.command(cmd)['status']=='replayed'
