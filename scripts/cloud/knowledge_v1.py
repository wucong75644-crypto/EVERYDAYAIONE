#!/usr/bin/env python3
"""Explicit, task-private V1 development clone. Never migrates a production DB.

Prepare the normal empty native bootstrap first. This helper leaves its database
and receipt untouched, then validates a dedicated clone before applying V1 SQL.
"""
import hashlib
import json
import os
from pathlib import Path
import sys
from uuid import uuid4
import psycopg
from psycopg import sql
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
STATE = Path('/workspace/.everydayaione-dev/instances') / hashlib.sha256(str(ROOT.resolve()).encode()).hexdigest()[:16]
NAME = 'cloud_dev_knowledge_v1'
MIGRATION = ROOT / 'backend/migrations/302_knowledge_workspace_v1.sql'
RECEIPT = ROOT / '.cloud-dev/knowledge-v1.json'
sys.path[:0] = [str(ROOT/'backend'), str(ROOT/'scripts/cloud/bootstrap')]

def private_write(path, value):
    path.parent.mkdir(exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, 'w') as f:
        json.dump(value, f)

def connections():
    meta = json.loads((STATE/'local.json').read_text())
    args = dict(host='127.0.0.1', port=meta['pgport'], user='dev_admin', password=meta['password'])
    with psycopg.connect(**args, dbname='postgres') as c:
        assert Path(c.execute('SHOW data_directory').fetchone()[0]).resolve() == (STATE/'pgdata').resolve()
        assert c.execute('SELECT host(inet_server_addr())').fetchone()[0] == '127.0.0.1'
    return meta, args

def prepare():
    from apply import verify
    from catalog import catalog, fingerprint
    meta, args = connections()
    with psycopg.connect(**args, dbname='cloud_dev_app_v1') as c:
        verify(c)
    digest = hashlib.sha256(MIGRATION.read_bytes()).hexdigest()
    with psycopg.connect(**args, dbname='postgres', autocommit=True) as c:
        if not c.execute('SELECT 1 FROM pg_database WHERE datname=%s', (NAME,)).fetchone():
            c.execute(sql.SQL('CREATE DATABASE {} TEMPLATE cloud_dev_app_v1').format(sql.Identifier(NAME)))
    with psycopg.connect(**args, dbname=NAME) as c:
        marker = c.execute("SELECT to_regclass('cloud_development.knowledge_v1_state')").fetchone()[0]
        if marker:
            saved = c.execute('SELECT migration_hash,catalog_hash FROM cloud_development.knowledge_v1_state').fetchone()
            assert saved == (digest, fingerprint(catalog(c))), 'V1 clone schema changed; preserve it and investigate'
        else:
            verify(c)  # Refuse unknown/non-baseline schemas, even on the private cluster.
            c.execute(MIGRATION.read_text())
            c.execute('CREATE TABLE cloud_development.knowledge_v1_state(migration_hash text PRIMARY KEY,catalog_hash text NOT NULL)')
            c.execute('INSERT INTO cloud_development.knowledge_v1_state VALUES(%s,%s)', (digest, fingerprint(catalog(c))))
        if RECEIPT.exists():
            record = json.loads(RECEIPT.read_text())
            assert record['database'] == NAME and record['task'] == str(ROOT)
        else:
            actors = [str(uuid4()) for _ in range(3)]; org = str(uuid4())
            for i, actor in enumerate(actors):
                c.execute("INSERT INTO public.users(id,nickname,created_by) VALUES(%s,%s,'system')", (actor, ['画布体验账号','项目协作账号','隔离验证账号'][i]))
            c.execute('INSERT INTO public.organizations(id,name,owner_id) VALUES(%s,%s,%s)', (org,'知识画布独立开发团队',actors[0]))
            for i, actor in enumerate(actors):
                c.execute('INSERT INTO public.org_members(org_id,user_id,role) VALUES(%s,%s,%s)', (org,actor,'owner' if i==0 else 'member'))
            record = {'database': NAME, 'task': str(ROOT), 'actors': actors, 'org': org}
    private_write(RECEIPT, record)
    print('V1 private development clone prepared; baseline untouched, no production migration')
    return record

def runtime_config():
    meta, _ = connections()
    record = json.loads(RECEIPT.read_text()); assert record['task'] == str(ROOT) and record['database']==NAME
    from catalog import catalog, fingerprint
    _, args = connections()
    with psycopg.connect(**args, dbname=NAME) as connection:
        saved = connection.execute('SELECT migration_hash,catalog_hash FROM cloud_development.knowledge_v1_state').fetchone()
        assert saved == (hashlib.sha256(MIGRATION.read_bytes()).hexdigest(), fingerprint(catalog(connection))), 'V1 development schema drift'
    values = {k:v for k,v in dotenv_values(ROOT/'backend/.env').items() if v is not None}
    from psycopg.conninfo import make_conninfo
    values['DATABASE_URL'] = make_conninfo(host='127.0.0.1',port=meta['pgport'],user='everydayai',password=meta['app_password'],dbname=NAME)
    values.update(KNOWLEDGE_WORKSPACE_ENABLED='true', KNOWLEDGE_WORKSPACE_USER_IDS=','.join(record['actors']),
                  KNOWLEDGE_WORKSPACE_ORG_IDS='', SOCIAL_OPS_ENABLED='false', AGENT_REACH_ENABLED='false',
                  CONVERSATION_ACTOR_WEB_ENABLED='false', CONVERSATION_ACTOR_WORKER_ENABLED='false', WECOM_BOT_ENABLED='false')
    assert values['APP_ENV']=='development' and values['REDIS_HOST']=='127.0.0.1'
    return values, record

def prepare_browser_sessions():
    import httpx
    values,record=runtime_config();os.environ.update(values)
    from core.security import create_access_token
    _,args=connections();org=str(uuid4())
    with psycopg.connect(**args,dbname=NAME) as c:
        c.execute('INSERT INTO public.organizations(id,name,owner_id) VALUES(%s,%s,%s)',(org,'V1 browser '+org,record['actors'][0]))
        for i,actor in enumerate(record['actors']):
            c.execute('INSERT INTO public.org_members(org_id,user_id,role) VALUES(%s,%s,%s)',(org,actor,'owner' if i==0 else 'member'))
    sessions=[]
    for actor in record['actors']:
        token=create_access_token({'sub':actor})
        with httpx.Client(base_url='http://127.0.0.1:8000',headers={'Authorization':'Bearer '+token,'X-Org-Id':org}) as client:
            me=client.get('/api/auth/me');me.raise_for_status()
            assert client.get('/api/knowledge/availability').json()['data']['enabled']
            sessions.append({'token':token,'user':me.json(),'org':org})
    private_write(ROOT/'.cloud-dev/knowledge-browser-sessions.json',sessions)
    print('Prepared synthetic browser sessions in a fresh private organization')

if __name__ == '__main__':
    action=sys.argv[1] if len(sys.argv)>1 else 'prepare'
    if action=='prepare': prepare()
    elif action=='browser-session': prepare_browser_sessions()
    elif action=='start':
        values,_ = runtime_config(); os.environ.update(values)
        os.chdir(ROOT/'backend')
        import socket
        with socket.socket() as probe: probe.bind(('127.0.0.1',8000))
        import uvicorn
        uvicorn.run('main:app',host='127.0.0.1',port=8000,log_level='warning')
    else: raise SystemExit('Usage: knowledge_v1.py prepare|start|browser-session')
