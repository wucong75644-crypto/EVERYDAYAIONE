"""Identity-preserving selection drag / cut-paste with real atomic history."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import pytest
import psycopg
from tests.test_knowledge_workspace_v2 import local_db, command
from tests.test_knowledge_branch_drag import create
from tests.test_knowledge_canvas_undo import toggle, visible

@pytest.fixture(scope='module')
def group_db(local_db):
    make,actors=local_db
    with psycopg.connect(**make.admin_args) as c:
        for name in ['307_knowledge_branch_drag.sql','308_knowledge_canvas_undo.sql','309_knowledge_branch_batch.sql','310_knowledge_group_move.sql']:
            c.execute((Path(__file__).resolve().parents[1]/'migrations'/name).read_text())
    return make,actors

def payload(r,items,target=None,placement='free'):
    cid=items[0]['container_id'];scene=r.canvas();ids={i['id'] for i in items}
    while True:
        updated=ids|{n['id'] for n in scene['nodes'] if n['parent_id'] in ids}
        if ids==updated:break
        ids=updated
    return {'container_id':cid,'expected_container_revision':next(c['revision'] for c in scene['containers'] if c['id']==cid),
            'objects':[{'object_id':o['id'],'expected_revision':o['revision']} for o in items],
            'target_id':target['id'] if target else None,'placement':placement,
            'nodes':[{'node_id':n['node_id'],'expected_revision':n['layout_revision'],'position':{'x':n['x']+70,'y':n['y']+90}} for n in scene['nodes'] if n['id'] in ids]}

@pytest.mark.parametrize('placement',['free','child','before','after','root'])
def test_multi_move_roundtrips_all_identity_hierarchy_order_and_offsets(group_db,placement):
    make,_=group_db;r=make();root=create(r);cid=root['container_id'];a=create(r,cid,root['id'],'A');grand=create(r,cid,a['id'],'Grand');b=create(r,cid,root['id'],'B');target=create(r,cid,root['id'],'Target')
    r.command(command('connect',{'source_object_id':grand['id'],'target_object_id':target['id'],'relation_type':'related'}))
    before=visible(r,cid);cmd=command('move_branches',payload(r,[a,grand,b],target if placement in ['child','before','after'] else None,placement));r.command(cmd)
    current=r.canvas();group=[n for n in current['nodes'] if n['id'] in [a['id'],grand['id'],b['id']]]
    assert len(group)==3 and all(n['x']==170 and n['y']==190 for n in group)
    assert r.object(grand['id'])['parent_id']==a['id']
    expected_parent=target['id'] if placement=='child' else None if placement=='root' else root['id']
    assert r.object(a['id'])['parent_id']==expected_parent and r.object(b['id'])['parent_id']==expected_parent
    siblings=sorted([n for n in current['nodes'] if n['parent_id']==expected_parent and n['container_id']==cid],key=lambda n:n['sort_order'])
    titles=[n['title'] for n in siblings]
    if placement=='before':assert titles==['A','B','Target']
    if placement=='after':assert titles==['Target','A','B']
    after=visible(r,cid);assert r.command(cmd)['status']=='replayed' and visible(r,cid)==after
    toggle(r);assert visible(r,cid)==before;toggle(r,'redo');assert visible(r,cid)==after
    assert r.version(grand['id'],1)

@pytest.mark.parametrize('bad',['cycle','selected_target','cross','stale','partial','duplicate','other_layout','permission'])
def test_group_failures_are_atomic(group_db,bad):
    make,actors=group_db;r=make();root=create(r);cid=root['container_id'];a=create(r,cid,root['id']);grand=create(r,cid,a['id']);b=create(r,cid,root['id']);target=create(r,cid,root['id'])
    p=payload(r,[a,b],target,'child');actor=r
    if bad=='cycle':p['target_id']=grand['id']
    if bad=='selected_target':p['target_id']=b['id']
    if bad=='cross':p['target_id']=create(r)['id']
    if bad=='stale':p['expected_container_revision']-=1
    if bad=='partial':p['nodes']=p['nodes'][:1]
    if bad=='duplicate':p['nodes'].append(p['nodes'][0])
    if bad=='other_layout':p['nodes'][0]['expected_revision']=99
    if bad=='permission':actor=make(1)
    before=r.canvas()
    with pytest.raises(Exception,match='KB_'):actor.command(command('move_branches',p))
    assert r.canvas()==before and r.object(a['id'])['revision']==1

def test_concurrent_group_moves_only_one_applies(group_db):
    make,_=group_db;r=make();root=create(r);a=create(r,root['container_id'],root['id']);b=create(r,root['container_id'],root['id']);p=payload(r,[a,b])
    def go(_):
        try:make().command(command('move_branches',p));return 'ok'
        except Exception as e:assert 'KB_VERSION_CONFLICT' in str(e);return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as executor:assert sorted(executor.map(go,range(2)))==['conflict','ok']

from tests.test_knowledge_chat_v3 import v3_db, start, ref
from tests.test_knowledge_files import file_client, uploaded, url

def test_group_cut_preserves_file_binding_and_old_snapshot(group_db,v3_db,file_client):
    client,ctx,_,r,make=file_client
    root=create(r);cid=root['container_id'];a=create(r,cid,root['id'],'A');b=create(r,cid,root['id'],'B');target=create(r,cid,root['id'],'Target')
    binding=uploaded(client,a['id'],'移动后仍是固定附件')['binding_id']
    run,_,_=start(make,r,[ref(root)]);handle=run.introduction()['roots'][0]['handle']
    before=run.execute('outline',{'root_handle':handle})
    r.command(command('move_branches',payload(r,[a,b],target,'child')))
    assert client.get(url(a['id'],binding)).content=='移动后仍是固定附件'.encode()
    assert run.execute('outline',{'root_handle':handle})==before
    assert r.object(a['id'])['parent_id']==target['id']
    toggle(r);assert r.object(a['id'])['parent_id']==root['id']
    assert client.get(url(a['id'],binding)).content=='移动后仍是固定附件'.encode()
