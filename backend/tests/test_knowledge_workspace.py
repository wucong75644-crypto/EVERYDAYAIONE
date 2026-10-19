"""V1 request boundary and optional real private PostgreSQL integration.

KNOWLEDGE_V1_INTEGRATION=1 enables only the task-bound loopback clone helper.
No production URL or environment credential is accepted by these tests.
"""
import os
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import pytest
from pydantic import TypeAdapter, ValidationError
from services.knowledge_workspace.contracts import Command

adapter = TypeAdapter(Command)
def command(op, payload, key=None):
    return adapter.validate_python({'command_id': key or uuid4(), 'operation':op,'payload':payload})

@pytest.mark.parametrize('payload', [
    {'kind':'conversation'}, {'kind':'file'}, {'title':' '}, {'actor':'spoof'},
    {'position': {'x': float('inf'), 'y': 0}}, {'position':{'x':1000001,'y':0}},
])
def test_invalid_create_boundary(payload):
    with pytest.raises(ValidationError):
        command('create_object',dict({'container_id':uuid4(),'kind':'topic','title':'主题','position':{'x':0,'y':0}},**payload))

def test_rollout_requires_switch_and_explicit_scope(monkeypatch):
    from types import SimpleNamespace
    from api.routes import knowledge
    settings=SimpleNamespace(knowledge_workspace_enabled=False,knowledge_workspace_user_ids='u',knowledge_workspace_org_ids='o')
    monkeypatch.setattr(knowledge,'get_settings',lambda:settings)
    ctx=SimpleNamespace(user_id='u',org_id='o')
    assert not knowledge.enabled(ctx)
    settings.knowledge_workspace_enabled=True
    assert knowledge.enabled(ctx)
    ctx.user_id='other';ctx.org_id=None
    assert not knowledge.enabled(ctx)
    ctx.org_id='o'
    assert knowledge.enabled(ctx)

@pytest.fixture(scope='module')
def database():
    if os.environ.get('KNOWLEDGE_V1_INTEGRATION')!='1':
        pytest.skip('explicit private development integration only')
    root=Path(__file__).resolve().parents[2];sys.path.insert(0,str(root/'scripts/cloud'))
    from knowledge_v1 import runtime_config, connections, NAME
    from psycopg_pool import ConnectionPool
    import psycopg
    values,_=runtime_config();_,args=connections()
    actors=[str(uuid4()) for _ in range(4)];orgs=[str(uuid4()),str(uuid4())]
    with psycopg.connect(**args,dbname=NAME) as c:
        for uid in actors:c.execute("INSERT INTO users(id,nickname,created_by) VALUES(%s,'V1 integration','system')",(uid,))
        for org in orgs:c.execute("INSERT INTO organizations(id,name,owner_id) VALUES(%s,%s,%s)",(org,'V1 integration '+org,actors[0]))
        for org in orgs:
            for i,uid in enumerate(actors):
                c.execute('INSERT INTO org_members(org_id,user_id,role) VALUES(%s,%s,%s)',(org,uid,'owner' if i==0 else 'member'))
    with ConnectionPool(values['DATABASE_URL'],min_size=1,max_size=6) as pool:
        yield pool, actors, orgs

def repo(database, actor=0, org=0):
    from services.knowledge_workspace.repository import Repository
    from core.db_scope import DatabaseScope, DatabaseAccessKind
    pool,actors,orgs=database
    return Repository(pool,DatabaseScope(actors[actor],orgs[org] if org is not None else None,DatabaseAccessKind.RUNTIME))

def project(r):
    result=r.command(command('create_project',{'title':'测试项目','position':{'x':0,'y':0}}))
    return r.object(result['object_id'])

def create(r, cid, parent=None, title='子分支'):
    result=r.command(command('create_object',{'container_id':cid,'parent_id':parent,'kind':'topic','title':title,'position':{'x':320,'y':100}}))
    return r.object(result['object_id'])

def test_persistence_versions_and_command_replay(database):
    r=repo(database);r.initialize();obj=project(r)
    cmd=command('save_document',{'object_id':obj['id'],'expected_revision':1,'body':'持久化正文'})
    first=r.command(cmd);assert r.command(cmd)['status']=='replayed'
    assert r.object(obj['id'])['revision']==2
    assert repo(database).object(obj['id'])['body']=='持久化正文'
    with r.cursor() as c:
        c.execute('SELECT count(*) AS n FROM kb_events WHERE command_id=%s',(cmd.command_id,));assert c.fetchone()['n']==1
        c.execute('SELECT count(*) AS n FROM kb_object_versions WHERE object_id=%s',(obj['id'],));assert c.fetchone()['n']==2
    changed=command('save_document',{'object_id':obj['id'],'expected_revision':1,'body':'不同正文'},cmd.command_id)
    with pytest.raises(Exception,match='KB_IDEMPOTENCY_PAYLOAD_CONFLICT'):r.command(changed)
    assert first['status']=='applied'

def test_private_org_and_null_org_isolation(database):
    r=repo(database);r.initialize()
    private=next(c for c in r.canvas()['containers'] if c['kind']=='personal')
    obj=create(r,private['id'])
    assert repo(database,1).object(obj['id']) is None
    assert repo(database,0,1).object(obj['id']) is None
    solo=repo(database,0,None);solo.initialize()
    own=next(c for c in solo.canvas()['containers'] if c['kind']=='personal')
    solo_obj=create(solo,own['id'])
    assert repo(database,1,None).object(solo_obj['id']) is None
    assert r.object(solo_obj['id']) is None

def test_project_roles_revocation_and_replay_rechecks_acl(database):
    r=repo(database);obj=project(r);cid=obj['container_id'];uid=database[1][1];other=repo(database,1)
    def grant(role,status='active'):return r.command(command('set_member',{'container_id':cid,'user_id':uid,'role':role,'status':status}))
    assert other.object(obj['id']) is None
    grant('viewer');assert other.object(obj['id'])['can_write'] is False
    with pytest.raises(Exception,match='KB_ACCESS_DENIED'):create(other,cid)
    grant('editor');cmd=command('create_object',{'container_id':cid,'kind':'topic','title':'编辑者内容','position':{'x':0,'y':0}})
    other.command(cmd)
    with pytest.raises(Exception,match='KB_ACCESS_DENIED'):
        other.command(command('set_member',{'container_id':cid,'user_id':database[1][2],'role':'manager'}))
    grant('viewer','removed');assert other.object(obj['id']) is None
    with pytest.raises(Exception,match='KB_NOT_FOUND'):other.command(cmd)

def test_team_readable_but_not_writable_by_default(database):
    r=repo(database);r.initialize();team=next(c for c in r.canvas()['containers'] if c['kind']=='team')
    obj=create(r,team['id']);other=repo(database,2)
    assert other.object(obj['id']) is not None
    with pytest.raises(Exception,match='KB_ACCESS_DENIED'):create(other,team['id'])

def test_cycle_cross_container_parent_and_atomic_rollback(database):
    r=repo(database);a=project(r);b=create(r,a['container_id'],a['id']);foreign=project(r)
    with pytest.raises(Exception,match='KB_TREE_CYCLE'):
        r.command(command('reparent',{'object_id':a['id'],'expected_revision':1,'parent_id':b['id']}))
    with pytest.raises(Exception,match='KB_NOT_FOUND'):create(r,a['container_id'],foreign['id'])
    assert r.object(a['id'])['revision']==1
    assert r.object(a['id'])['parent_id'] is None

def test_concurrent_edit_only_one_wins(database):
    r=repo(database);a=project(r)
    def save(body):
        try:
            repo(database).command(command('save_document',{'object_id':a['id'],'expected_revision':1,'body':body}));return 'saved'
        except Exception as e:
            assert 'KB_VERSION_CONFLICT' in str(e);return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as executor:assert sorted(executor.map(save,['A','B']))==['conflict','saved']
    assert r.object(a['id'])['revision']==2

def test_concurrent_identical_create_only_once(database):
    r=repo(database);p=project(r)
    cmd=command('create_object',{'container_id':p['container_id'],'kind':'topic','title':'只创建一次','position':{'x':0,'y':0}})
    with ThreadPoolExecutor(max_workers=2) as executor:results=list(executor.map(lambda _: repo(database).command(cmd),range(2)))
    assert {x['status'] for x in results}=={'applied','replayed'}
    assert results[0]['object_id']==results[1]['object_id']

def test_layout_batch_rollback_and_raw_writes_denied(database):
    r=repo(database);obj=project(r);node=next(n for n in r.canvas()['nodes'] if n['id']==obj['id'])
    payload={'nodes':[{'node_id':node['node_id'],'expected_revision':1,'position':{'x':123,'y':456}}, {'node_id':uuid4(),'expected_revision':1,'position':{'x':1,'y':2}}]}
    with pytest.raises(Exception,match='KB_VERSION_CONFLICT'):r.command(command('move_nodes',payload))
    assert next(n for n in r.canvas()['nodes'] if n['id']==obj['id'])['x']==0
    with pytest.raises(Exception,match='permission denied'):
        with r.cursor() as c:c.execute('UPDATE kb_objects SET title=%s WHERE id=%s',('绕过',obj['id']))
    assert r.object(obj['id'])['title']=='测试项目'


def test_runtime_role_rls_is_nonrecursive_and_not_a_bypass(database):
    import psycopg
    from knowledge_v1 import connections, NAME
    from core.db_scope import SET_DATABASE_SCOPE_SQL,DatabaseScope,DatabaseAccessKind
    r=repo(database);obj=project(r);_,args=connections()
    with psycopg.connect(**args,dbname=NAME) as c:
        c.execute('SET LOCAL ROLE everydayai_runtime')
        c.execute(SET_DATABASE_SCOPE_SQL,DatabaseScope(database[1][0],database[2][0],DatabaseAccessKind.RUNTIME).settings)
        flags=c.execute('SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user').fetchone()
        assert flags==(False,False)
        assert c.execute('SELECT title FROM kb_objects WHERE id=%s',(obj['id'],)).fetchone()[0]=='测试项目'
        c.execute(SET_DATABASE_SCOPE_SQL,DatabaseScope(database[1][3],database[2][0],DatabaseAccessKind.RUNTIME).settings)
        assert c.execute('SELECT id FROM kb_objects WHERE id=%s',(obj['id'],)).fetchone() is None
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with c.transaction():c.execute('UPDATE kb_objects SET title=%s WHERE id=%s',('绕过',obj['id']))
