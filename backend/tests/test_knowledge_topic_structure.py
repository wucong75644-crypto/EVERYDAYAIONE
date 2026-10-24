"""Structure metadata and positions form one atomic, permission-checked history step."""
from pathlib import Path
import psycopg
import pytest
from tests.test_knowledge_canvas_visual import visual_db
from tests.test_knowledge_canvas_tools import tools_db, payload
from tests.test_knowledge_workspace_v2 import local_db, command
from tests.test_knowledge_title_layout import layout_db
from tests.test_knowledge_insert_before import before_db
from tests.test_knowledge_branch_drag import create
from tests.test_knowledge_canvas_undo import toggle

@pytest.fixture(scope='module')
def structure_db(visual_db):
    make,actors=visual_db
    with psycopg.connect(**make.admin_args) as c:
        for name in ['318_knowledge_canvas_media_templates.sql','319_knowledge_topic_structure.sql']:
            c.execute((Path(__file__).resolve().parents[1]/'migrations'/name).read_text())
    return make,actors

def current(r,oid):return next(n for n in r.canvas()['nodes'] if str(n['id'])==str(oid))

@pytest.mark.parametrize('kind',['right','left','balanced','down','up'])
def test_change_structure_and_layout_is_one_undo_step(structure_db,kind):
    make,_=structure_db;r=make();a=create(r);b=create(r,a['container_id'],a['id'],'Child');before=current(r,b['id']);p=payload(r,[a],{'structure':kind,'note':'保留'})
    p['nodes']=[{'node_id':before['node_id'],'expected_revision':before['layout_revision'],'position':{'x':-400,'y':800}}]
    cmd=command('update_canvas_data',p);r.command(cmd);assert r.command(cmd)['status']=='replayed'
    assert current(r,a['id'])['canvas_data']=={'structure':kind,'note':'保留','labels':[]};assert current(r,b['id'])['x']==-400
    assert current(r,b['id'])['parent_id']==a['id'];toggle(r);assert not current(r,a['id'])['canvas_data'];assert current(r,b['id'])['x']==before['x']
    toggle(r,'redo');assert current(r,a['id'])['canvas_data']['structure']==kind;assert current(r,b['id'])['x']==-400

@pytest.mark.parametrize('bad',['revision','permission','foreign_node'])
def test_layout_update_failure_rolls_back_metadata(structure_db,bad):
    make,_=structure_db;r=make();a=create(r);b=create(r);p=payload(r,[a],{'structure':'down'});b=current(r,b['id']);p['nodes']=[{'node_id':b['node_id'],'expected_revision':b['layout_revision'],'position':{'x':1,'y':2}}]
    if bad=='revision':p['expected_container_revision']-=1
    actor=make(1) if bad=='permission' else r
    before=r.canvas()
    with pytest.raises(Exception,match='KB_'):actor.command(command('update_canvas_data',p))
    assert r.canvas()==before

def test_create_type_and_reflow_are_one_receipt_and_history_step(structure_db):
    make,_=structure_db;r=make();a=create(r);b=create(r,a['container_id'],a['id'],'First');before=current(r,b['id']);cid=a['container_id'];p={'container_id':cid,'expected_container_revision':next(c['revision'] for c in r.canvas()['containers'] if c['id']==cid),'parent_id':a['id'],'topics':[{'title':'新主题','parent_index':-1,'structure':'left','position':{'x':-400,'y':100}}],'after_object_id':b['id'],'nodes':[{'node_id':before['node_id'],'expected_revision':before['layout_revision'],'position':{'x':-400,'y':400}}]}
    cmd=command('create_template',p);oid=r.command(cmd)['object_id'];assert r.command(cmd)['status']=='replayed';assert current(r,oid)['canvas_data']['structure']=='left';assert current(r,oid)['parent_id']==a['id'];assert current(r,b['id'])['y']==400
    toggle(r);assert not any(n['id']==oid for n in r.canvas()['nodes']);assert current(r,b['id'])['y']==before['y'];toggle(r,'redo');assert current(r,oid)['canvas_data']['structure']=='left'

def test_create_reflow_conflict_does_not_leave_a_new_topic(structure_db):
    make,_=structure_db;r=make();a=create(r);p={'container_id':a['container_id'],'expected_container_revision':next(c['revision'] for c in r.canvas()['containers'] if c['id']==a['container_id']),'topics':[{'title':'新主题','parent_index':-1,'structure':'up','position':{'x':0,'y':0}}],'nodes':[{'node_id':current(r,a['id'])['node_id'],'expected_revision':999,'position':{'x':1,'y':2}}]};before=r.canvas()
    with pytest.raises(Exception,match='KB_VERSION_CONFLICT'):r.command(command('create_template',p))
    assert r.canvas()==before

@pytest.mark.parametrize('kind',['fishbone','',123])
def test_contract_rejects_unknown_structure(kind):
    from uuid import uuid4
    with pytest.raises(ValueError):command('update_canvas_data',{'container_id':uuid4(),'expected_container_revision':1,'updates':[{'object_id':uuid4(),'expected_layout_revision':1,'canvas_data':{'structure':kind}}]})

def test_copy_keeps_selected_structure_and_undo_restores_positions(structure_db):
    from uuid import uuid4
    make,_=structure_db;r=make();a=create(r);cid=a['container_id'];r.command(command('update_canvas_data',payload(r,[a],{'structure':'balanced'})))
    s=r.canvas();n=current(r,a['id']);p={'snapshot_id':str(uuid4()),'container_id':cid,'expected_container_revision':next(c['revision'] for c in s['containers'] if c['id']==cid),'objects':[{'object_id':n['id'],'expected_revision':n['revision']}],'parent_id':None,'offset':{'x':1500,'y':0}}
    r.branch_snapshot(p['snapshot_id'],command('copy_branches',p).payload.model_dump(mode='json'));r.command(command('copy_branches',p));copied=next(n for n in r.canvas()['nodes'] if n['container_id']==cid and n['id']!=a['id']);assert copied['canvas_data']['structure']=='balanced';toggle(r);assert len([n for n in r.canvas()['nodes'] if n['container_id']==cid])==1
