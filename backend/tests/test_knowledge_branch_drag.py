"""Atomic branch commands against a new loopback database, never application env."""
from pathlib import Path
from uuid import uuid4
import pytest
import psycopg
from pydantic import ValidationError
from tests.test_knowledge_workspace_v2 import local_db, command
from tests.test_knowledge_chat_v3 import v3_db, start, ref

@pytest.fixture(scope='module')
def branch_db(local_db):
    make, actors = local_db
    with psycopg.connect(**make.admin_args) as c:
        c.execute((Path(__file__).resolve().parents[1]/'migrations/307_knowledge_branch_drag.sql').read_text())
    return make, actors

def create(r, cid=None, parent=None, title='Node'):
    if cid is None:
        oid=r.command(command('create_project',{'title':title,'position':{'x':0,'y':0}}))['object_id']
    else:
        oid=r.command(command('create_object',{'container_id':cid,'parent_id':parent,'title':title,'kind':'topic','position':{'x':100,'y':100}}))['object_id']
    return r.object(oid)

def payload(r, source, target=None, placement='free'):
    scene=r.canvas(); n=next(n for n in scene['nodes'] if n['id']==source['id'])
    cid=source['container_id']; owner=next(c for c in scene['containers'] if c['id']==cid)
    return {'object_id':source['id'],'expected_revision':n['revision'],'expected_container_revision':owner['revision'],
            'target_id':target['id'] if target else None,'placement':placement,
            'nodes':[{'node_id':n['node_id'],'expected_revision':n['layout_revision'],'position':{'x':n['x']+50,'y':n['y']+60}} for n in scene['nodes'] if n['container_id']==cid]}

def test_move_parent_and_positions_atomic_with_history_and_replay(branch_db):
    make,_=branch_db;r=make();root=create(r);cid=root['container_id']
    a=create(r,cid,root['id'],'A');child=create(r,cid,a['id'],'Child');b=create(r,cid,root['id'],'B')
    cmd=command('move_branch',payload(r,a,b,'child'));r.command(cmd)
    assert r.object(a['id'])['parent_id']==b['id'] and r.object(a['id'])['revision']==2
    with r.cursor() as c:
        c.execute('SELECT parent_id FROM kb_object_versions WHERE object_id=%s AND revision=1',(a['id'],))
        assert c.fetchone()['parent_id']==root['id']
    assert r.object(child['id'])['parent_id']==a['id']
    saved=r.canvas();assert all(n['x']==150 for n in saved['nodes'] if n['id'] in (a['id'],child['id']))
    assert r.command(cmd)['status']=='replayed' and r.canvas()==saved

def test_order_survives_reload_and_new_nodes_append(branch_db):
    make,_=branch_db;r=make();root=create(r);cid=root['container_id']
    a=create(r,cid,root['id'],'A');b=create(r,cid,root['id'],'B');c=create(r,cid,root['id'],'C')
    r.command(command('move_branch',payload(r,c,a,'before')))
    def titles():return [n['title'] for n in sorted(r.canvas()['nodes'],key=lambda n:n['sort_order']) if n['parent_id']==root['id']]
    assert titles()==['C','A','B'];create(r,cid,root['id'],'D');assert titles()==['C','A','B','D']
    r.command(command('move_branch',payload(r,c,b,'after')));assert titles()==['A','B','C','D']

@pytest.mark.parametrize('failure',['cycle','layout','container','incomplete','duplicate','cross'])
def test_rejected_drag_never_partially_saves(branch_db,failure):
    make,_=branch_db;r=make();root=create(r);cid=root['container_id'];a=create(r,cid,root['id']);child=create(r,cid,a['id'])
    p=payload(r,a)
    if failure=='cycle':p.update(target_id=child['id'],placement='child')
    if failure=='layout':p['nodes'][-1]['expected_revision']=999
    if failure=='container':p['expected_container_revision']-=1
    if failure=='incomplete':p['nodes']=[n for n in p['nodes'] if n['node_id']==next(n['node_id'] for n in r.canvas()['nodes'] if n['id']==a['id'])]
    if failure=='duplicate':p['nodes'].append(p['nodes'][0])
    if failure=='cross':p.update(target_id=create(r)['id'],placement='child')
    before=r.canvas()
    with pytest.raises(Exception,match='KB_'):r.command(command('move_branch',p))
    assert r.canvas()==before and r.object(a['id'])['revision']==1

def test_permission_and_actor_scope_rechecked_on_new_and_replayed_commands(branch_db):
    make,actors=branch_db;r=make();root=create(r);a=create(r,root['container_id'],root['id']);p=payload(r,a)
    with pytest.raises(Exception,match='KB_NOT_FOUND'):make(1).command(command('move_branch',p))
    with pytest.raises(Exception,match='KB_NOT_FOUND'):make(0,1).command(command('move_branch',p))
    r.command(command('set_member',{'container_id':root['container_id'],'user_id':actors[1],'role':'editor','status':'active'}))
    other=make(1);cmd=command('move_branch',payload(other,a));other.command(cmd)
    r.command(command('set_member',{'container_id':root['container_id'],'user_id':actors[1],'role':'viewer','status':'active'}))
    with pytest.raises(Exception,match='KB_ACCESS_DENIED'):other.command(cmd)
    with pytest.raises(Exception,match='KB_ACCESS_DENIED'):other.command(command('move_branch',payload(r,a)))

def test_strict_branch_boundary():
    with pytest.raises(ValidationError):command('move_branch',{'object_id':uuid4(),'expected_revision':1,'expected_container_revision':1,'target_id':None,'placement':'child','nodes':[],'actor':'spoof'})


@pytest.mark.parametrize('role',['everydayai','everydayai_runtime'])
def test_application_and_runtime_role_execute_same_guarded_command(branch_db,role):
    from psycopg_pool import ConnectionPool
    from core.db_scope import DatabaseScope, DatabaseAccessKind
    from services.knowledge_workspace.repository import Repository
    make,_=branch_db;base=make()
    with ConnectionPool(kwargs={**make.admin_args,'options':'-c role='+role},min_size=1,max_size=1) as pool:
        r=Repository(pool,base.scope);root=create(r);a=create(r,root['container_id'],root['id'])
        r.command(command('move_branch',payload(r,a)))
        assert next(n for n in r.canvas()['nodes'] if n['id']==a['id'])['x']==150


def test_concurrent_layout_change_allows_only_one_atomic_candidate(branch_db):
    from concurrent.futures import ThreadPoolExecutor
    make,_=branch_db;r=make();root=create(r);a=create(r,root['container_id'],root['id']);p=payload(r,a)
    def save(_):
        try:make().command(command('move_branch',p));return 'saved'
        except Exception as e:
            assert 'KB_VERSION_CONFLICT' in str(e);return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as executor:assert sorted(executor.map(save,range(2)))==['conflict','saved']


def test_existing_snapshot_retains_hierarchy_after_new_parent_is_saved(branch_db,v3_db):
    make,_=branch_db;r=make();root=create(r,title='快照根');a=create(r,root['container_id'],root['id'],'快照分支A');b=create(r,root['container_id'],root['id'],'快照分支B')
    run,_,_=start(make,r,[ref(root)]);handle=run.introduction()['roots'][0]['handle']
    top=next(x for x in run.execute('outline',{'root_handle':handle})['items'] if x['title']=='快照根')
    args={'root_handle':handle,'parent_handle':top['handle']}
    assert '快照分支A' in [x['title'] for x in run.execute('outline',args)['items']]
    r.command(command('move_branch',payload(r,a,b,'child')))
    assert '快照分支A' in [x['title'] for x in run.execute('outline',args)['items']]
    assert r.object(a['id'])['parent_id']==b['id']
