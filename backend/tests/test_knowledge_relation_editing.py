"""Association lifecycle against a fresh isolated loopback PostgreSQL database."""
from pathlib import Path
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
import psycopg
import pytest
from pydantic import ValidationError
from tests.test_knowledge_workspace_v2 import local_db, command
from tests.test_knowledge_branch_drag import create
from tests.test_knowledge_canvas_undo import toggle

@pytest.fixture(scope='module')
def relation_db(local_db):
    make, actors = local_db
    with psycopg.connect(**make.admin_args) as c:
        for n in range(307, 314):
            c.execute(next((Path(__file__).resolve().parents[1]/'migrations').glob(f'{n}_*.sql')).read_text())
    return make, actors

def setup(r):
    root = create(r)
    a = create(r, root['container_id'], root['id'], 'A')
    b = create(r, root['container_id'], root['id'], 'B')
    c = create(r, root['container_id'], root['id'], 'C')
    r.command(command('connect', {'source_object_id':a['id'], 'target_object_id':b['id'], 'relation_type':'dependency'}))
    edge = next(e for e in r.canvas()['relations'] if e['source_object_id']==a['id'])
    return root, a, b, c, edge

def payload(r, edge, **extra):
    container = next(c for c in r.canvas()['containers'] if c['id']==edge['container_id'])
    return {'relation_id':edge['id'], 'container_id':edge['container_id'], 'expected_container_revision':container['revision'], **extra}

def relations(r, cid):
    return [e for e in r.canvas()['relations'] if e['container_id']==cid]

@pytest.mark.parametrize('endpoint',['source','target'])
def test_reconnect_undo_redo_keeps_id_type_hierarchy_and_receipt(relation_db, endpoint):
    make,_=relation_db;r=make();root,a,b,c,edge=setup(r);cid=root['container_id']
    before=r.canvas();old=r.canvas_history()['undo']
    p=payload(r,edge,source_object_id=c['id'] if endpoint=='source' else a['id'],target_object_id=c['id'] if endpoint=='target' else b['id'])
    cmd=command('reconnect_relation',p);r.command(cmd)
    changed=relations(r,cid)[0]
    assert changed['id']==edge['id'] and changed['relation_type']=='dependency'
    assert changed['source_object_id']==p['source_object_id'] and changed['target_object_id']==p['target_object_id']
    assert r.canvas()['nodes']==before['nodes'] and r.canvas_history()['undo']['operation']=='reconnect_relation'
    assert r.canvas_history()['undo']['history_id']!=old['history_id']
    after=r.canvas();assert r.command(cmd)['status']=='replayed' and r.canvas()==after
    toggle(r);assert relations(r,cid)==[edge]
    toggle(r,'redo');assert relations(r,cid)==[changed]
    assert r.object(c['id'])['parent_id']==root['id']

def test_delete_undo_redo_does_not_delete_nodes_or_versions(relation_db):
    make,_=relation_db;r=make();root,a,b,c,edge=setup(r);cid=root['container_id'];before=r.canvas()['nodes']
    cmd=command('delete_relation',payload(r,edge));r.command(cmd)
    assert relations(r,cid)==[] and r.canvas()['nodes']==before
    assert r.version(a['id'],1) and r.version(b['id'],1)
    toggle(r);assert relations(r,cid)==[edge]
    toggle(r,'redo');assert relations(r,cid)==[]
    assert r.command(cmd)['status']=='replayed'

@pytest.mark.parametrize('bad',['self','cross_container','foreign_relation','missing','stale','hidden','other_actor','other_org','duplicate'])
def test_invalid_edits_are_atomic(relation_db,bad):
    make,_=relation_db;r=make();root,a,b,c,edge=setup(r)
    extra={'source_object_id':a['id'],'target_object_id':c['id']}
    if bad=='cross_container': extra['target_object_id']=create(r)['id']
    if bad=='hidden':
        hidden=create(r,root['container_id'],root['id']);toggle(r);extra['target_object_id']=hidden['id']
    if bad=='duplicate':
        r.command(command('connect', {**extra,'relation_type':'dependency'}))
    p=payload(r,edge,**extra);actor=r
    if bad=='self':p['target_object_id']=a['id']
    elif bad=='foreign_relation':p['container_id']=create(r)['container_id'];p['expected_container_revision']=1
    elif bad=='missing':p['relation_id']=uuid4()
    elif bad=='stale':p['expected_container_revision']-=1
    elif bad=='other_actor':actor=make(1)
    elif bad=='other_org':actor=make(0,1)
    before=r.canvas();history=r.canvas_history()
    with pytest.raises(Exception, match='KB_'):actor.command(command('reconnect_relation',p))
    assert r.canvas()==before and r.canvas_history()==history

@pytest.mark.parametrize('operation',['delete_relation','reconnect_relation'])
def test_permissions_rechecked_for_edits_and_replays(relation_db, operation):
    make,actors=relation_db;r=make();root,a,b,c,edge=setup(r);cid=root['container_id']
    r.command(command('set_member',{'container_id':cid,'user_id':actors[1],'role':'editor','status':'active'}))
    other=make(1);p=payload(other,edge,**({'source_object_id':a['id'],'target_object_id':c['id']} if operation=='reconnect_relation' else {}))
    cmd=command(operation,p);other.command(cmd)
    r.command(command('set_member',{'container_id':cid,'user_id':actors[1],'role':'viewer','status':'active'}))
    before=r.canvas()
    with pytest.raises(Exception,match='KB_ACCESS_DENIED'):other.command(cmd)
    with pytest.raises(Exception,match='KB_ACCESS_DENIED'):other.command(command(operation,p))
    assert r.canvas()==before

def test_noop_reconnect_keeps_redo_and_does_not_increment_revision(relation_db):
    make,_=relation_db;r=make();root,a,b,c,edge=setup(r)
    create(r,root['container_id'],root['id']);toggle(r)
    redo=r.canvas_history()['redo'];before=r.canvas()
    r.command(command('reconnect_relation',payload(r,edge,source_object_id=a['id'],target_object_id=b['id'])))
    assert r.canvas()==before and r.canvas_history()['redo']==redo
    # The older connect path also must not consume redo when it finds the same edge.
    r.command(command('connect',{'source_object_id':a['id'],'target_object_id':b['id'],'relation_type':'dependency'}))
    assert r.canvas_history()['redo']['history_id']==redo['history_id'] and r.canvas_history()['redo']['can_apply']

def test_concurrent_edits_with_same_container_revision_apply_once(relation_db):
    make,_=relation_db;r=make();root,a,b,c,edge=setup(r)
    p=payload(r,edge,source_object_id=a['id'],target_object_id=c['id'])
    def edit(_):
        try:make().command(command('reconnect_relation',p));return 'ok'
        except Exception as e:assert 'KB_VERSION_CONFLICT' in str(e);return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:assert sorted(pool.map(edit,range(2)))==['conflict','ok']

def test_runtime_cannot_call_relation_writer_directly(relation_db):
    make,_=relation_db;r=make()
    with pytest.raises(Exception,match='permission denied'):
        with r.pool.connection() as c:c.execute("SELECT public.kb_apply_relation_command(%s,'delete_relation','{}')",(uuid4(),))

@pytest.mark.parametrize('operation,payload_data',[
    ('delete_relation',{'relation_id':uuid4(),'container_id':uuid4(),'expected_container_revision':0}),
    ('reconnect_relation',{'relation_id':uuid4(),'container_id':uuid4(),'expected_container_revision':1,'source_object_id':uuid4()}),
    ('delete_relation',{'relation_id':uuid4(),'container_id':uuid4(),'expected_container_revision':1,'actor_id':uuid4()}),
])
def test_strict_relation_boundary(operation,payload_data):
    with pytest.raises(ValidationError):command(operation,payload_data)

from tests.test_knowledge_files import file_client

def test_http_relation_commands_and_conflicts_are_sanitized(relation_db,file_client):
    client,ctx,_,r,make=file_client
    root,a,b,c,edge=setup(r)
    p=payload(r,edge,source_object_id=a['id'],target_object_id=a['id'])
    response=client.post('/api/knowledge/commands',json=command('reconnect_relation',p).model_dump(mode='json'))
    assert response.status_code==422 and response.json()['error']['code']=='INVALID_RELATION'
    p['target_object_id']=c['id']
    response=client.post('/api/knowledge/commands',json=command('reconnect_relation',p).model_dump(mode='json'))
    assert response.status_code==200,response.text
    stale=client.post('/api/knowledge/commands',json=command('delete_relation',{'container_id':p['container_id'],'expected_container_revision':p['expected_container_revision'],'relation_id':edge['id']}).model_dump(mode='json'))
    assert stale.status_code==409 and stale.json()['error']['code']=='VERSION_CONFLICT'
    _,actors=relation_db;ctx.user_id=actors[1]
    denied=client.post('/api/knowledge/commands',json=command('delete_relation',payload(r,edge)).model_dump(mode='json'))
    assert denied.status_code==403 and 'public.' not in denied.text and str(root['container_id']) not in denied.text
