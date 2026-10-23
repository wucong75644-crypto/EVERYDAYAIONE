"""Atomic title reflow preserves permissions, optimistic locks and history."""
from pathlib import Path
import pytest
import psycopg
from tests.test_knowledge_workspace_v2 import local_db, command
from tests.test_knowledge_branch_drag import create
from tests.test_knowledge_canvas_undo import toggle, visible

@pytest.fixture(scope='module')
def layout_db(local_db):
    make, actors = local_db
    with psycopg.connect(**make.admin_args) as c:
        for name in ['307_knowledge_branch_drag.sql','308_knowledge_canvas_undo.sql','309_knowledge_branch_batch.sql','310_knowledge_group_move.sql','311_knowledge_insert_after.sql','312_knowledge_clipboard_snapshots.sql','313_knowledge_relation_editing.sql','314_knowledge_title_layout.sql']:
            c.execute((Path(__file__).resolve().parents[1]/'migrations'/name).read_text())
    return make, actors

def setup(r):
    root=create(r); a=create(r,root['container_id'],root['id'],'A'); b=create(r,root['container_id'],root['id'],'B')
    scene=r.canvas(); n=next(n for n in scene['nodes'] if n['id']==b['id']); cid=a['container_id']
    return cid, {'object_id':a['id'],'expected_revision':1,'title':'长标题'*50,'expected_container_revision':next(c['revision'] for c in scene['containers'] if c['id']==cid), 'nodes':[{'node_id':n['node_id'],'expected_revision':n['layout_revision'],'position':{'x':n['x'],'y':n['y']+500}}]}

def test_title_and_layout_undo_redo_and_replay(layout_db):
    make,_=layout_db;r=make();cid,p=setup(r);before=visible(r,cid);cmd=command('update_title',p);r.command(cmd);after=visible(r,cid)
    assert after!=before and r.command(cmd)['status']=='replayed'
    toggle(r);assert visible(r,cid)==before
    toggle(r,'redo');assert visible(r,cid)==after

@pytest.mark.parametrize('bad',['container','layout','object','duplicate','foreign','permission'])
def test_conflicts_roll_back_title_and_all_layout(layout_db,bad):
    make,_=layout_db;r=make();cid,p=setup(r);actor=r
    if bad=='container':p['expected_container_revision']-=1
    if bad=='layout':p['nodes'][0]['expected_revision']=999
    if bad=='object':p['expected_revision']=999
    if bad=='duplicate':p['nodes'].append(p['nodes'][0])
    if bad=='foreign':
        other=create(r);p['nodes'][0]['node_id']=next(n['node_id'] for n in r.canvas()['nodes'] if n['id']==other['id'])
    if bad=='permission':actor=make(1)
    before=r.canvas()
    with pytest.raises(Exception,match='KB_'):actor.command(command('update_title',p))
    assert r.canvas()==before

def test_plain_title_keeps_old_receipt_shape_and_private_layout_helper(layout_db):
    make,_=layout_db;r=make();root=create(r);cmd=command('update_title',{'object_id':root['id'],'expected_revision':1,'title':'旧客户端标题'})
    r.command(cmd)
    with psycopg.connect(**make.admin_args) as c:
        payload=c.execute('SELECT request_payload FROM public.kb_mutation_receipts WHERE command_id=%s',(cmd.command_id,)).fetchone()[0]['payload']
        assert 'nodes' not in payload and 'expected_container_revision' not in payload
        assert not c.execute("SELECT has_function_privilege('everydayai_runtime','public.kb_title_layout(uuid,jsonb)','EXECUTE')").fetchone()[0]
    assert r.command(cmd)['status']=='replayed'
