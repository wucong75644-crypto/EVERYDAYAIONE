"""Copy-time snapshots on a fresh loopback database, never production."""
from pathlib import Path
from uuid import uuid4
import pytest
import psycopg
from tests.test_knowledge_workspace_v2 import local_db, command
from tests.test_knowledge_branch_drag import create
from tests.test_knowledge_canvas_undo import toggle, visible
from tests.test_knowledge_branch_batch import data
from services.knowledge_workspace.contracts import BranchSnapshotRequest

@pytest.fixture(scope='module')
def snapshot_db(local_db):
    make, actors = local_db
    with psycopg.connect(**make.admin_args) as c:
        for n in range(307,313):
            path = next((Path(__file__).resolve().parents[1]/'migrations').glob(f'{n}_*.sql'))
            c.execute(path.read_text())
    return make, actors

def capture(r, items):
    payload = BranchSnapshotRequest.model_validate({**data(r,items), 'snapshot_id':str(uuid4())}).model_dump(mode='json')
    return r.branch_snapshot(payload['snapshot_id'],payload), payload

def paste(r, snap, cid, parent=None):
    p = data(r,[{'id':n['id'],'revision':n['revision'],'container_id':cid} for n in snap['nodes']],parent)
    p['snapshot_id']=snap['snapshot_id']
    return r.command(command('copy_branches',p))

def test_snapshot_keeps_original_content_structure_layout_relations_and_history(snapshot_db):
    make,_=snapshot_db;r=make();root=create(r);cid=root['container_id'];a=create(r,cid,root['id'],'原标题');b=create(r,cid,a['id'],'原子级')
    r.command(command('save_document',{'object_id':a['id'],'expected_revision':1,'body':'复制时正文'}))
    r.command(command('connect',{'source_object_id':a['id'],'target_object_id':b['id'],'relation_type':'related'}))
    source=[n for n in r.canvas()['nodes'] if n['id'] in [a['id'],b['id']]]
    scene=r.canvas();history=r.canvas_history();snap,payload=capture(r,source)
    assert r.canvas()==scene and r.canvas_history()==history
    r.command(command('update_title',{'object_id':a['id'],'expected_revision':2,'title':'后改标题'}))
    r.command(command('save_document',{'object_id':a['id'],'expected_revision':3,'body':'后改正文'}))
    create(r,cid,a['id'],'新增子级不应复制')
    latest=next(n for n in r.canvas()['nodes'] if n['id']==a['id'])
    r.command(command('delete_branches',data(r,[latest])))
    before=visible(r,cid);paste(r,snap,cid,root['id'])
    copied=[n for n in r.canvas()['nodes'] if n['id'] not in {root['id'],a['id'],b['id']} and n['container_id']==cid]
    assert sorted(n['title'] for n in copied)==['原子级','原标题']
    ca=next(n for n in copied if n['title']=='原标题');cb=next(n for n in copied if n['title']=='原子级')
    assert cb['parent_id']==ca['id'] and r.object(ca['id'])['body']=='复制时正文'
    original=next(n for n in snap['nodes'] if n['id']==str(a['id']));assert ca['x']==original['x']+80 and ca['y']==original['y']+300
    assert any(e['source_object_id']==ca['id'] and e['target_object_id']==cb['id'] for e in r.canvas()['relations'])
    after=visible(r,cid);toggle(r);assert visible(r,cid)==before;toggle(r,'redo');assert visible(r,cid)==after
    assert r.branch_snapshot(snap['snapshot_id'],payload)==snap

@pytest.mark.parametrize('bad',['other_actor','other_org','expired','cross_container','revision','idempotency'])
def test_snapshot_rejects_invalid_use_without_writes(snapshot_db,bad):
    make,_=snapshot_db;r=make();root=create(r);a=create(r,root['container_id'],root['id']);snap,payload=capture(r,[a]);before=r.canvas()
    with pytest.raises(Exception,match='KB_'):
        if bad=='other_actor':make(1).branch_snapshot(snap['snapshot_id'])
        elif bad=='other_org':
            from core.db_scope import DatabaseScope, DatabaseAccessKind
            from services.knowledge_workspace.repository import Repository
            Repository(r.pool,DatabaseScope(r.scope.actor_user_id,None,DatabaseAccessKind.RUNTIME)).branch_snapshot(snap['snapshot_id'])
        elif bad=='expired':
            with psycopg.connect(**make.admin_args) as c:
                c.execute('SET ROLE everydayai_owner');c.execute("UPDATE public.kb_branch_snapshots SET expires_at=now()-interval '1 second' WHERE id=%s",(snap['snapshot_id'],))
            paste(r,snap,root['container_id'])
        elif bad=='cross_container':paste(r,snap,create(r)['container_id'])
        elif bad=='revision':
            p=data(r,[a]);p['snapshot_id']=snap['snapshot_id'];p['expected_container_revision']-=1;r.command(command('copy_branches',p))
        else:r.branch_snapshot(snap['snapshot_id'],{**payload,'offset':{'x':99,'y':99}})
    if bad!='cross_container':assert r.canvas()==before

from tests.test_knowledge_files import file_client, uploaded, url

def test_snapshot_pins_file_version_at_copy_not_paste(snapshot_db,file_client):
    client,_,_,r,_=file_client;root=create(r);cid=root['container_id'];a=create(r,cid,root['id']);bid=uploaded(client,a['id'],'复制时附件')['binding_id']
    source=next(n for n in r.canvas()['nodes'] if n['id']==a['id']);snap,_=capture(r,[source])
    uploaded(client,a['id'],'粘贴前更新的附件',binding_id=bid,expected_revision='1')
    before={n['id'] for n in r.canvas()['nodes']};paste(r,snap,cid)
    copied=next(n for n in r.canvas()['nodes'] if n['id'] not in before)
    binding=client.get(f"/api/knowledge/objects/{copied['id']}/files").json()['data'][0]
    assert client.get(url(copied['id'],binding['id'])).content=='复制时附件'.encode()

def test_snapshot_permissions_are_rechecked_after_capture(snapshot_db):
    make,actors=snapshot_db;r=make();root=create(r);cid=root['container_id'];a=create(r,cid,root['id'])
    r.command(command('set_member',{'container_id':cid,'user_id':actors[1],'role':'editor','status':'active'}))
    other=make(1);source=next(n for n in other.canvas()['nodes'] if n['id']==a['id']);snap,_=capture(other,[source])
    create(other,cid,root['id'],'editor operation')
    r.command(command('set_member',{'container_id':cid,'user_id':actors[1],'role':'viewer','status':'active'}))
    with pytest.raises(Exception,match='KB_ACCESS_DENIED'):paste(other,snap,cid)
    history=other.canvas_history()['undo'];assert not history['can_apply'] and '编辑权限' in history['reason']
    with pytest.raises(Exception,match='permission denied'):
        with other.cursor() as c:c.execute('SELECT * FROM public.kb_branch_snapshots')

def test_snapshot_http_response_contains_no_body_and_history_limit_is_real(snapshot_db,file_client):
    client,_,_,r,_=file_client;root=create(r);source=next(n for n in r.canvas()['nodes'] if n['id']==root['id'])
    payload=BranchSnapshotRequest.model_validate({**data(r,[source]),'snapshot_id':str(uuid4())}).model_dump(mode='json')
    response=client.post('/api/knowledge/branch-snapshots',json=payload);assert response.status_code==200
    assert all('body' not in n and 'files' not in n for n in response.json()['data']['nodes'])
    make,_=snapshot_db;actor=make(2,1);own=create(actor)
    for revision in range(1,103):actor.command(command('update_title',{'object_id':own['id'],'expected_revision':revision,'title':f'edit {revision}'}))
    with psycopg.connect(**make.admin_args) as c:
        c.execute('SET ROLE everydayai_owner');count=c.execute('SELECT count(*) FROM public.kb_canvas_history WHERE actor_id=%s AND org_id=%s',(actor.scope.actor_user_id,actor.scope.org_id)).fetchone()[0];assert count==100

@pytest.mark.parametrize('change',['source_changed','incomplete_branch'])
def test_capture_rejects_stale_or_incomplete_selection_atomically(snapshot_db,change):
    make,_=snapshot_db;r=make();root=create(r);a=create(r,root['container_id'],root['id']);b=create(r,root['container_id'],a['id'])
    items=[next(n for n in r.canvas()['nodes'] if n['id']==x['id']) for x in [a,b]]
    payload=BranchSnapshotRequest.model_validate({**data(r,items),'snapshot_id':str(uuid4())}).model_dump(mode='json')
    if change=='source_changed':r.command(command('update_title',{'object_id':a['id'],'expected_revision':1,'title':'他人更新'}))
    else:payload['objects']=payload['objects'][:1]
    before=r.canvas();history=r.canvas_history()
    with pytest.raises(Exception,match='KB_VERSION_CONFLICT'):r.branch_snapshot(payload['snapshot_id'],payload)
    assert r.canvas()==before and r.canvas_history()==history
