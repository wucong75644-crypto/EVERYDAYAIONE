"""Canvas annotations and summaries retain ACL, atomic history and copy snapshots."""
from pathlib import Path
import uuid
import psycopg
import pytest
from tests.test_knowledge_workspace_v2 import local_db, command
from tests.test_knowledge_title_layout import layout_db
from tests.test_knowledge_insert_before import before_db
from tests.test_knowledge_branch_drag import create
from tests.test_knowledge_canvas_undo import toggle

@pytest.fixture(scope='module')
def tools_db(before_db):
    make, actors=before_db
    with psycopg.connect(**make.admin_args) as c:
        c.execute((Path(__file__).resolve().parents[1]/'migrations/316_knowledge_canvas_tools.sql').read_text())
    return make,actors

def payload(r,nodes,data):
    s=r.canvas();cid=nodes[0]['container_id']
    return {'container_id':cid,'expected_container_revision':next(c['revision'] for c in s['containers'] if c['id']==cid),'updates':[{'object_id':n['id'],'expected_layout_revision':next(x['layout_revision'] for x in s['nodes'] if x['id']==n['id']),'canvas_data':data} for n in nodes]}

def test_annotation_history_and_replay(tools_db):
    make,_=tools_db;r=make();root=create(r);p=payload(r,[root],{'note':'补充说明','labels':['售后'],'task':{'progress':50,'priority':2}});cmd=command('update_canvas_data',p)
    r.command(cmd);assert r.command(cmd)['status']=='replayed'
    def data():return next(n['canvas_data'] for n in r.canvas()['nodes'] if n['id']==root['id'])
    assert data()['task']['progress']==50
    toggle(r);assert data()=={}
    toggle(r,'redo');assert data()['note']=='补充说明'

@pytest.mark.parametrize('bad',['layout','container','foreign','permission'])
def test_atomic_rollback_and_acl(tools_db,bad):
    make,_=tools_db;r=make();root=create(r);b=create(r,root['container_id'],root['id'],'B');p=payload(r,[root,b],{'labels':['A']});actor=r
    if bad=='layout':p['updates'][1]['expected_layout_revision']=999
    if bad=='container':p['expected_container_revision']-=1
    if bad=='foreign':p['updates'][1]['object_id']=create(r)['id']
    if bad=='permission':actor=make(1)
    old=r.canvas()
    with pytest.raises(Exception,match='KB_'):actor.command(command('update_canvas_data',p))
    assert r.canvas()==old

def test_summary_keeps_parents_and_undo(tools_db):
    make,_=tools_db;r=make();root=create(r);a=create(r,root['container_id'],root['id'],'A');b=create(r,root['container_id'],root['id'],'B');cid=root['container_id']
    p={'container_id':cid,'expected_container_revision':next(c['revision'] for c in r.canvas()['containers'] if c['id']==cid),'groups':[{'members':[a['id'],b['id']],'title':'售后概要','position':{'x':1000,'y':300}}]}
    cmd=command('create_summaries',p);oid=r.command(cmd)['object_id'];assert r.command(cmd)['object_id']==oid
    s=r.canvas();assert next(n for n in s['nodes'] if str(n['id'])==oid)['canvas_data']['summary']['members']==[str(a['id']),str(b['id'])]
    assert all(n['parent_id']==root['id'] for n in s['nodes'] if n['id'] in [a['id'],b['id']])
    toggle(r);assert not any(str(n['id'])==oid for n in r.canvas()['nodes'])
    toggle(r,'redo');assert any(str(n['id'])==oid for n in r.canvas()['nodes'])

def test_copy_pins_note_and_remaps_group_members(tools_db):
    make,_=tools_db;r=make();root=create(r);a=create(r,root['container_id'],root['id'],'A');b=create(r,root['container_id'],root['id'],'B');cid=root['container_id']
    r.command(command('update_canvas_data',payload(r,[a],{'note':'固定备注','boundary':{'title':'组','members':[a['id'],b['id']]}})))
    s=r.canvas();ns=[n for n in s['nodes'] if n['id'] in [a['id'],b['id']]];p={'snapshot_id':str(uuid.uuid4()),'container_id':cid,'expected_container_revision':next(c['revision'] for c in s['containers'] if c['id']==cid),'objects':[{'object_id':n['id'],'expected_revision':n['revision']} for n in ns],'parent_id':root['id'],'offset':{'x':0,'y':500}}
    r.branch_snapshot(p['snapshot_id'],command('copy_branches',p).payload.model_dump(mode='json'))
    r.command(command('copy_branches',p));copies=[n for n in r.canvas()['nodes'] if n['container_id']==cid and n['id'] not in [root['id'],a['id'],b['id']]]
    annotated=next(n for n in copies if n['canvas_data'].get('note'))
    assert annotated['canvas_data']['note']=='固定备注'
    assert set(annotated['canvas_data']['boundary']['members'])=={str(n['id']) for n in copies}

@pytest.mark.parametrize('bad',['foreign','root','stale','permission'])
def test_summary_rejects_invalid_members_and_keeps_scene(tools_db,bad):
    make,_=tools_db;r=make();root=create(r);a=create(r,root['container_id'],root['id'],'A');cid=root['container_id'];members=[a['id']];actor=r
    if bad=='foreign':members=[create(r)['id']]
    if bad=='root':members=[root['id']]
    p={'container_id':cid,'expected_container_revision':next(c['revision'] for c in r.canvas()['containers'] if c['id']==cid),'groups':[{'members':members,'title':'概要','position':{'x':1000,'y':300}}]}
    if bad=='stale':p['expected_container_revision']-=1
    if bad=='permission':actor=make(1)
    old=r.canvas()
    with pytest.raises(Exception,match='KB_'):actor.command(command('create_summaries',p))
    assert r.canvas()==old

@pytest.mark.parametrize('task',[{'progress':101},{'priority':4},{'start':'2026-02-30'},{'start':'2026-10-24','due':'2026-10-23'}])
def test_task_contract_rejects_invalid_values(task):
    with pytest.raises(ValueError):command('update_canvas_data',{'container_id':str(uuid.uuid4()),'expected_container_revision':1,'updates':[{'object_id':str(uuid.uuid4()),'expected_layout_revision':1,'canvas_data':{'task':task}}]})

def test_deleting_member_shrinks_summary_and_undo_restores_members(tools_db):
    make,_=tools_db;r=make();root=create(r);a=create(r,root['container_id'],root['id'],'A');b=create(r,root['container_id'],root['id'],'B');cid=root['container_id']
    r.command(command('create_summaries',{'container_id':cid,'expected_container_revision':next(c['revision'] for c in r.canvas()['containers'] if c['id']==cid),'groups':[{'members':[a['id'],b['id']],'title':'概要','position':{'x':1000,'y':300}}]}))
    def members():return next(n['canvas_data']['summary']['members'] for n in r.canvas()['nodes'] if n['container_id']==cid and n['canvas_data'].get('summary'))
    r.command(command('delete_branches',{'container_id':cid,'expected_container_revision':next(c['revision'] for c in r.canvas()['containers'] if c['id']==cid),'objects':[{'object_id':a['id'],'expected_revision':1}]}))
    assert members()==[str(b['id'])]
    toggle(r);assert members()==[str(a['id']),str(b['id'])]

def test_multiple_parent_groups_are_one_atomic_undo_step(tools_db):
    make,_=tools_db;r=make();root=create(r);a=create(r,root['container_id'],root['id'],'A');b=create(r,root['container_id'],root['id'],'B');c=create(r,root['container_id'],a['id'],'C');cid=root['container_id'];old={n['id'] for n in r.canvas()['nodes']}
    r.command(command('create_summaries',{'container_id':cid,'expected_container_revision':next(x['revision'] for x in r.canvas()['containers'] if x['id']==cid),'groups':[{'members':[b['id']],'title':'概要一','position':{'x':1000,'y':300}},{'members':[c['id']],'title':'概要二','position':{'x':1500,'y':300}}]}))
    assert len({n['id'] for n in r.canvas()['nodes']}-old)==2
    toggle(r);assert {n['id'] for n in r.canvas()['nodes']}==old
    toggle(r,'redo');assert len({n['id'] for n in r.canvas()['nodes']}-old)==2

def test_dragged_sibling_preserves_exact_position_order_and_atomic_history(tools_db):
    make,_=tools_db;r=make();root=create(r);a=create(r,root['container_id'],root['id'],'A');b=create(r,root['container_id'],root['id'],'B');cid=root['container_id'];p={'container_id':cid,'parent_id':root['id'],'kind':'topic','title':'拖动同级','position':{'x':-250,'y':180},'after_object_id':a['id'],'expected_container_revision':next(c['revision'] for c in r.canvas()['containers'] if c['id']==cid),'nodes':[]}
    cmd=command('create_object',p);oid=r.command(cmd)['object_id'];assert r.command(cmd)['object_id']==oid
    n=next(n for n in r.canvas()['nodes'] if str(n['id'])==oid);assert (n['x'],n['y'],n['parent_id'])==(-250,180,root['id'])
    assert [n['title'] for n in sorted(r.canvas()['nodes'],key=lambda n:n['sort_order']) if n['parent_id']==root['id']]==['A','拖动同级','B']
    toggle(r);assert not any(str(n['id'])==oid for n in r.canvas()['nodes']);toggle(r,'redo');assert any(str(n['id'])==oid and n['x']==-250 and n['y']==180 for n in r.canvas()['nodes'])
