"""Real RLS tests in an isolated ephemeral loopback cluster; no application env."""
from pathlib import Path
import shutil
import os
import socket
import subprocess
from uuid import uuid4
import pytest
import psycopg
from psycopg_pool import ConnectionPool
from core.db_scope import DatabaseScope, DatabaseAccessKind
from services.knowledge_workspace.repository import Repository
from pydantic import TypeAdapter
from services.knowledge_workspace.contracts import Command

def command(operation, payload):
    return TypeAdapter(Command).validate_python({'command_id':uuid4(),'operation':operation,'payload':payload})

@pytest.fixture(scope='module')
def local_db(tmp_path_factory):
    pg = shutil.which('initdb') or next((str(p) for p in [Path('/opt/homebrew/opt/postgresql@17/bin/initdb'), Path('/opt/homebrew/opt/postgresql@16/bin/initdb')] if p.is_file()), None)
    if not pg or os.geteuid()==0: pytest.skip('requires local PostgreSQL binaries and non-root account')
    base=tmp_path_factory.mktemp('kb-v2'); data=base/'data'; binaries=Path(pg).parent
    subprocess.run([pg,'-D',str(data),'-U','kb_test_admin','-A','trust','--no-locale','--encoding=UTF8'],check=True,capture_output=True)
    with socket.socket() as probe:
        probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
    subprocess.run([str(binaries/'pg_ctl'),'-D',str(data),'-l',str(base/'pg.log'),'-o',f"-h 127.0.0.1 -p {port} -k ''",'-w','start'],check=True,capture_output=True)
    args=dict(host='127.0.0.1',port=port,user='kb_test_admin',dbname='postgres')
    try:
        with psycopg.connect(**args) as c:
            c.execute('''CREATE ROLE everydayai NOLOGIN; CREATE ROLE everydayai_owner NOLOGIN; CREATE ROLE everydayai_runtime NOLOGIN;
              GRANT USAGE,CREATE ON SCHEMA public TO everydayai,everydayai_owner; SET ROLE everydayai;
              CREATE TABLE users(id uuid PRIMARY KEY,nickname text,status text DEFAULT 'active');
              CREATE TABLE organizations(id uuid PRIMARY KEY,owner_id uuid);
              CREATE TABLE org_members(org_id uuid,user_id uuid,role text,status text DEFAULT 'active');
              GRANT SELECT ON users,organizations,org_members TO everydayai_owner; RESET ROLE;
              CREATE FUNCTION reach_active_member(p_org uuid,p_admin boolean DEFAULT false) RETURNS boolean
              LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
               SELECT EXISTS(SELECT 1 FROM public.org_members WHERE org_id=p_org AND status='active'
                AND user_id=NULLIF(current_setting('app.actor_user_id',true),'')::uuid
                AND (NOT p_admin OR role IN ('owner','admin'))) $$;''')
            c.execute((Path(__file__).resolve().parents[1]/'migrations/302_knowledge_workspace_v1.sql').read_text())
            c.execute('SET LOCAL ROLE everydayai')
            for table in ['conversations','messages','tasks','image_generations','conversation_attachment_refs']:
                c.execute(f'CREATE TABLE {table}(id uuid PRIMARY KEY)')
            c.execute((Path(__file__).resolve().parents[1]/'migrations/145_user_assets.sql').read_text())
            c.execute((Path(__file__).resolve().parents[1]/'migrations/146_admin_user_assets_query.sql').read_text())
            c.execute((Path(__file__).resolve().parents[1]/'migrations/236_restore_legacy_admin_user_assets.sql').read_text())
            c.execute('RESET ROLE')
            c.execute((Path(__file__).resolve().parents[1]/'migrations/303_knowledge_files_v2.sql').read_text())
            c.execute((Path(__file__).resolve().parents[1]/'migrations/304_knowledge_asset_visibility.sql').read_text())
            actors=[str(uuid4()) for _ in range(3)]; orgs=[str(uuid4()) for _ in range(2)]
            for actor in actors:c.execute("INSERT INTO users(id,nickname) VALUES(%s,'测试')",(actor,))
            for org in orgs:
                c.execute('INSERT INTO organizations VALUES(%s,%s)',(org,actors[0]))
                for i,actor in enumerate(actors):c.execute('INSERT INTO org_members(org_id,user_id,role) VALUES(%s,%s,%s)',(org,actor,'owner' if i==0 else 'member'))
        with ConnectionPool(kwargs={**args,'options':'-c role=everydayai_runtime'},min_size=1,max_size=3) as pool:
            def repo(actor=0,org=0):return Repository(pool,DatabaseScope(actors[actor],orgs[org],DatabaseAccessKind.RUNTIME))
            repo.admin_args=args
            yield repo,actors
    finally:
        subprocess.run([str(binaries/'pg_ctl'),'-D',str(data),'-m','immediate','-w','stop'],check=True,capture_output=True)

def document(r):
    oid=r.command(command('create_project',{'title':'V2 文档','position':{'x':0,'y':0}}))['object_id']
    r.command(command('save_document',{'object_id':oid,'expected_revision':1,'body':'中文正文中的验收关键词'}))
    return r.object(oid)

def test_search_current_body_and_immutable_history(local_db):
    make,_=local_db;r=make();obj=document(r);cid=obj['container_id'];oid=obj['id']
    assert r.search(cid,'验收关键词')['items'][0]['id']==obj['id']
    r.command(command('save_document',{'object_id':oid,'expected_revision':2,'body':'100%_ Complete 新正文'}))
    assert r.search(cid,'验收关键词')['items']==[]
    assert r.search(cid,'%_')['items'] and r.search(cid,'complete')['items']
    assert not r.search(cid,"' OR true --")['items']
    assert [v['revision'] for v in r.versions(oid)['items']]==[3,2,1]
    assert r.version(oid,2)['body']=='中文正文中的验收关键词'
    assert r.version(oid,999) is None

def test_current_acl_applies_to_search_and_all_old_versions(local_db):
    make,actors=local_db;r=make();viewer=make(1);obj=document(r);cid=obj['container_id'];oid=obj['id']
    def grant(status):r.command(command('set_member',{'container_id':cid,'user_id':actors[1],'role':'viewer','status':status}))
    assert viewer.search(cid,'关键词') is None
    assert viewer.versions(oid) is None and viewer.version(oid,1) is None
    grant('active')
    assert viewer.search(cid,'关键词')['items'] and viewer.versions(oid)['items'] and viewer.version(oid,1)
    assert make(1,1).search(cid,'关键词') is None and make(1,1).version(oid,1) is None
    grant('removed')
    assert viewer.search(cid,'关键词') is None
    assert viewer.versions(oid) is None and viewer.version(oid,1) is None

def test_cursor_pagination(local_db):
    make,_=local_db;r=make();obj=document(r);oid=obj['id'];cid=obj['container_id']
    for rev in range(2,54):r.command(command('save_document',{'object_id':oid,'expected_revision':rev,'body':f'版本正文 {rev}'}))
    first=r.versions(oid);second=r.versions(oid,first['next_cursor'])
    assert [v['revision'] for v in first['items']+second['items']]==list(range(54,0,-1))
    assert second['next_cursor'] is None
    for n in range(51):r.command(command('create_object',{'container_id':cid,'kind':'document','title':f'分页 {n}','body':'分页关键词','position':{'x':0,'y':0}}))
    first=r.search(cid,'分页关键词');second=r.search(cid,'分页关键词',first['next_cursor'])
    assert len(first['items'])==50 and len(second['items'])==1
    assert not set(row['id'] for row in first['items']) & set(row['id'] for row in second['items'])
    assert second['next_cursor'] is None
