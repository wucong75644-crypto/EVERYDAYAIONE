"""Undo/redo uses real isolated PostgreSQL, actor scope and immutable versions."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import pytest
import psycopg
from tests.test_knowledge_workspace_v2 import local_db, command
from tests.test_knowledge_branch_drag import create, payload
from tests.test_knowledge_chat_v3 import v3_db, start, ref

@pytest.fixture(scope='module')
def undo_db(local_db):
    make, actors = local_db
    with psycopg.connect(**make.admin_args) as c:
        for name in ['307_knowledge_branch_drag.sql','308_knowledge_canvas_undo.sql']:
            c.execute((Path(__file__).resolve().parents[1]/'migrations'/name).read_text())
    return make, actors

def toggle(r, direction='undo'):
    item=r.canvas_history()[direction]
    assert item and item['can_apply']
    cmd=command(direction+'_canvas',{'history_id':item['history_id']})
    return r.command(cmd),cmd

def visible(r,cid):
    scene=r.canvas()
    return sorted([(str(n['id']),str(n['parent_id']),n['title'],n['x'],n['y'],n['sort_order']) for n in scene['nodes'] if n['container_id']==cid]),scene['relations']

def test_create_undo_redo_preserves_uuid_and_fixed_version(undo_db):
    make,_=undo_db;r=make();root=create(r);obj=create(r,root['container_id'],root['id'],'可撤销创建');cid=obj['container_id'];before=visible(r,cid)
    toggle(r);assert all(n[0]!=str(obj['id']) for n in visible(r,cid)[0]);assert r.version(obj['id'],1)['title']=='可撤销创建'
    assert r.search(cid,'可撤销创建')['items']==[]
    toggle(r,'redo');assert visible(r,cid)==before and r.object(obj['id'])['id']==obj['id']

def test_project_create_can_hide_and_restore_its_container(undo_db):
    make,_=undo_db;r=make();root=create(r,title='撤销项目');cid=root['container_id'];toggle(r)
    assert not any(c['id']==cid for c in r.canvas()['containers']);assert r.version(root['id'],1)
    toggle(r,'redo');assert any(c['id']==cid for c in r.canvas()['containers'])

def test_rename_creates_new_versions_without_rewinding(undo_db):
    make,_=undo_db;r=make();root=create(r)
    r.command(command('update_title',{'object_id':root['id'],'expected_revision':1,'title':'新标题'}));toggle(r)
    assert r.object(root['id'])['title']=='Node' and r.object(root['id'])['revision']==3
    toggle(r,'redo');assert r.object(root['id'])['title']=='新标题' and r.object(root['id'])['revision']==4
    assert r.version(root['id'],2)['title']=='新标题'

def test_line_relation_can_be_undone_and_redone_without_reparenting(undo_db):
    make,_=undo_db;r=make();root=create(r);a=create(r,root['container_id'],root['id']);b=create(r,root['container_id'],root['id'])
    cmd=command('connect',{'source_object_id':a['id'],'target_object_id':b['id'],'relation_type':'related'});r.command(cmd)
    rid=next(edge['id'] for edge in r.canvas()['relations'] if edge['source_object_id']==a['id']);toggle(r)
    assert all(edge['id']!=rid for edge in r.canvas()['relations']);toggle(r,'redo')
    assert any(edge['id']==rid for edge in r.canvas()['relations']);assert r.object(b['id'])['parent_id']==root['id']

@pytest.mark.parametrize('placement',['free','child','before','after'])
def test_branch_operation_restores_hierarchy_order_and_all_positions(undo_db,placement):
    make,_=undo_db;r=make();root=create(r);cid=root['container_id'];a=create(r,cid,root['id'],'A');child=create(r,cid,a['id'],'Child');b=create(r,cid,root['id'],'B')
    before=visible(r,cid);r.command(command('move_branch',payload(r,a,b if placement!='free' else None,placement)));after=visible(r,cid)
    toggle(r);assert visible(r,cid)==before;toggle(r,'redo');assert visible(r,cid)==after


def test_multiple_undos_rebase_own_revisions_and_new_write_clears_redo(undo_db):
    make,_=undo_db;r=make();root=create(r);a=create(r,root['container_id'],root['id'],'A');b=create(r,root['container_id'],root['id'],'B')
    toggle(r);toggle(r);assert all(n['id'] not in [a['id'],b['id']] for n in r.canvas()['nodes'])
    toggle(r,'redo');assert r.object(a['id']) and r.canvas_history()['redo']
    create(r,root['container_id'],root['id'],'New');assert r.canvas_history()['redo'] is None


def test_foreign_write_blocks_old_undo_without_overwriting(undo_db):
    make,actors=undo_db;r=make();root=create(r);cid=root['container_id'];a=create(r,cid,root['id'])
    r.command(command('set_member',{'container_id':cid,'user_id':actors[1],'role':'editor','status':'active'}));create(r,cid,root['id'],'Owner step')
    old=r.canvas_history()['undo'];create(make(1),cid,root['id'],'Other writer');before=visible(r,cid)
    assert not r.canvas_history()['undo']['can_apply']
    with pytest.raises(Exception,match='KB_HISTORY_CONFLICT'):r.command(command('undo_canvas',{'history_id':old['history_id']}))
    assert visible(r,cid)==before
    create(r,cid,root['id'],'Fresh');toggle(r);assert r.canvas_history()['undo'] is None or r.canvas_history()['undo']['history_id']!=old['history_id']


def test_stack_id_and_actor_scope_permissions_and_replay(undo_db):
    make,actors=undo_db;r=make();root=create(r);cid=root['container_id'];item=r.canvas_history()['undo']
    with pytest.raises(Exception,match='KB_HISTORY_CONFLICT'):make(1).command(command('undo_canvas',{'history_id':item['history_id']}))
    with pytest.raises(Exception,match='KB_HISTORY_CONFLICT'):make(0,1).command(command('undo_canvas',{'history_id':item['history_id']}))
    create(r,cid,root['id'])
    with pytest.raises(Exception,match='KB_HISTORY_CONFLICT'):r.command(command('undo_canvas',{'history_id':item['history_id']}))
    _,cmd=toggle(r);scene=visible(r,cid);assert r.command(cmd)['status']=='replayed' and visible(r,cid)==scene
    r.command(command('set_member',{'container_id':cid,'user_id':actors[1],'role':'editor','status':'active'}))
    other=make(1);create(other,cid,root['id']);own=other.canvas_history()['undo']
    r.command(command('set_member',{'container_id':cid,'user_id':actors[1],'role':'viewer','status':'active'}))
    with pytest.raises(Exception,match='KB_ACCESS_DENIED'):other.command(command('undo_canvas',{'history_id':own['history_id']}))


def test_simultaneous_undo_requests_apply_only_once(undo_db):
    make,_=undo_db;r=make();root=create(r);item=r.canvas_history()['undo']
    def undo(_):
        try:make().command(command('undo_canvas',{'history_id':item['history_id']}));return 'ok'
        except Exception as e:assert 'KB_HISTORY_CONFLICT' in str(e);return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as executor:assert sorted(executor.map(undo,range(2)))==['conflict','ok']


def test_hidden_create_not_in_new_snapshot_but_old_snapshot_and_source_survive(undo_db,v3_db):
    from services.knowledge_workspace.chat import roots
    from core.exceptions import AppException
    make,_=undo_db;r=make();root=create(r,title='已引用的主题');reference=ref(root)
    run,_,_=start(make,r,[reference]);handle=run.introduction()['roots'][0]['handle']
    toggle(r);assert all(str(i['id'])!=str(root['container_id']) for i in roots(r)['items'])
    assert run.execute('outline',{'root_handle':handle})['items'][0]['title']=='已引用的主题'
    assert r.version(root['id'],1)['title']=='已引用的主题'
    with pytest.raises(AppException):start(make,r,[reference])
    toggle(r,'redo');assert start(make,r,[reference])[0]


def test_hidden_child_does_not_block_moving_parent_and_does_not_reappear(undo_db):
    make,_=undo_db;r=make();root=create(r);child=create(r,root['container_id'],root['id']);toggle(r)
    assert r.canvas_history()['redo']
    r.command(command('move_branch',payload(r,root)))
    assert r.canvas_history()['redo'] is None
    assert not any(n['id']==child['id'] for n in r.canvas()['nodes'])
    toggle(r);assert not any(n['id']==child['id'] for n in r.canvas()['nodes'])
    assert r.version(child['id'],1)
