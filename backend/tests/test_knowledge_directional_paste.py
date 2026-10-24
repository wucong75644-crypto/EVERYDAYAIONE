"""Directional paste reflows the target in the same ACL/version guarded undo step."""
from pathlib import Path
from uuid import uuid4
import psycopg
import pytest
from tests.test_knowledge_topic_structure import structure_db, current
from tests.test_knowledge_canvas_visual import visual_db
from tests.test_knowledge_canvas_tools import tools_db,payload
from tests.test_knowledge_workspace_v2 import local_db,command
from tests.test_knowledge_title_layout import layout_db
from tests.test_knowledge_insert_before import before_db
from tests.test_knowledge_branch_drag import create
from tests.test_knowledge_canvas_undo import toggle

@pytest.fixture(scope='module')
def directional_db(structure_db):
    make,actors=structure_db
    with psycopg.connect(**make.admin_args) as c:c.execute((Path(__file__).resolve().parents[1]/'migrations/320_knowledge_directional_paste.sql').read_text())
    return make,actors

def setup(r):
    a=create(r);cid=a['container_id'];b=create(r,cid,a['id'],'source child');t=create(r,cid,None,'target');tc=create(r,cid,t['id'],'target child');r.command(command('update_canvas_data',payload(r,[t],{'structure':'left'})))
    ns=[current(r,n['id']) for n in [a,b]];scene=r.canvas();p={'snapshot_id':str(uuid4()),'container_id':cid,'expected_container_revision':next(c['revision'] for c in scene['containers'] if c['id']==cid),'objects':[{'object_id':n['id'],'expected_revision':n['revision']} for n in ns],'parent_id':t['id'],'offset':{'x':0,'y':0}}
    r.branch_snapshot(p['snapshot_id'],command('copy_branches',p).payload.model_dump(mode='json'))
    before=current(r,tc['id']);p['snapshot_positions']=[{'source_object_id':ns[0]['id'],'position':{'x':-400,'y':200}},{'source_object_id':ns[1]['id'],'position':{'x':-800,'y':200}}];p['nodes']=[{'node_id':before['node_id'],'expected_revision':before['layout_revision'],'position':{'x':-400,'y':500}}]
    return a,b,t,tc,p,before

def test_copy_layout_is_one_idempotent_history_step(directional_db):
    make,_=directional_db;r=make();a,b,t,tc,p,before=setup(r);old={str(n['id']) for n in r.canvas()['nodes']};cmd=command('copy_branches',p);r.command(cmd);assert r.command(cmd)['status']=='replayed'
    copies=[n for n in r.canvas()['nodes'] if str(n['id']) not in old];assert len(copies)==2;root=next(n for n in copies if n['parent_id']==t['id']);child=next(n for n in copies if n['parent_id']==root['id']);assert root['x']==-400 and child['x']==-800;assert current(r,tc['id'])['y']==500
    assert current(r,a['id'])['x']==0
    toggle(r);assert {str(n['id']) for n in r.canvas()['nodes']}==old;assert current(r,tc['id'])['y']==before['y'];toggle(r,'redo');assert current(r,tc['id'])['y']==500

@pytest.mark.parametrize('bad',['missing','duplicate','foreign','stale','permission'])
def test_copy_rejects_invalid_plan_without_partial_reflow(directional_db,bad):
    make,_=directional_db;r=make();a,b,t,tc,p,before=setup(r);actor=r
    if bad=='missing':p['snapshot_positions'].pop()
    if bad=='duplicate':p['snapshot_positions'][1]['source_object_id']=a['id']
    if bad=='foreign':p['snapshot_positions'][1]['source_object_id']=str(uuid4())
    if bad=='stale':p['nodes'][0]['expected_revision']=999
    if bad=='permission':actor=make(1)
    before=r.canvas()
    with pytest.raises(Exception,match='KB_'):actor.command(command('copy_branches',p))
    assert r.canvas()==before

def test_cut_reflow_restores_original_parent_and_target_positions(directional_db):
    make,_=directional_db;r=make();a,b,t,tc,p,before=setup(r);nodes=[current(r,n['id']) for n in [a,b]];moves=[{'node_id':n['node_id'],'expected_revision':n['layout_revision'],'position':p['snapshot_positions'][i]['position']} for i,n in enumerate(nodes)]+p['nodes'];q={'container_id':p['container_id'],'expected_container_revision':p['expected_container_revision'],'objects':p['objects'],'target_id':t['id'],'placement':'child','nodes':moves};r.command(command('move_branches',q));assert current(r,a['id'])['parent_id']==t['id'];assert current(r,b['id'])['parent_id']==a['id'];assert current(r,tc['id'])['y']==500;toggle(r);assert current(r,a['id'])['parent_id'] is None;assert current(r,tc['id'])['y']==before['y'];toggle(r,'redo');assert current(r,a['id'])['x']==-400
