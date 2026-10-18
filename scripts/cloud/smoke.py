#!/usr/bin/env python3
"""Real loopback API/DB/Redis smoke, synthetic identities only, no providers."""
import json
import os
from pathlib import Path
import secrets
import sys
import re
import time
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
import redis
from dotenv import dotenv_values

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'backend'),str(ROOT/'scripts/cloud/bootstrap')]
from apply import verify


def smoke(observe_seconds=20):
    config={k:v for k,v in dotenv_values(ROOT/'backend/.env').items() if v is not None}
    assert config['APP_ENV']=='development' and config['REDIS_HOST']=='127.0.0.1'
    from psycopg.conninfo import conninfo_to_dict
    assert conninfo_to_dict(config['DATABASE_URL'])['host']=='127.0.0.1'
    # This script is intentionally bound to the task-local private bootstrap.
    with psycopg.connect(config['DATABASE_URL']) as c:verify(c)
    os.environ.update(config)
    from core.security import create_access_token
    users=[]
    with psycopg.connect(config['DATABASE_URL']) as c:
        for role in ['super_admin','user','user']:
            uid=str(uuid4());phone='138'+''.join(secrets.choice('0123456789') for _ in range(8))
            c.execute("INSERT INTO users(id,nickname,phone,role,created_by,credits) VALUES(%s,%s,%s,%s,'system',100)",(uid,'Synthetic cloud smoke '+role,phone,role))
            users.append((uid,phone))
        flags=c.execute('SELECT rolsuper,rolcreatedb,rolcreaterole,rolbypassrls FROM pg_roles WHERE rolname=current_user').fetchone()
        assert not any(flags)
        with c.transaction(force_rollback=True):
            c.execute('UPDATE users SET credits=321 WHERE id=%s',(users[1][0],))
            assert c.execute('SELECT credits FROM users WHERE id=%s',(users[1][0],)).fetchone()[0]==321
        assert c.execute('SELECT credits FROM users WHERE id=%s',(users[1][0],)).fetchone()[0]==100
    tokens=[create_access_token({'sub':u[0]}) for u in users]
    checks=[]
    def request(method,path,actor=None,body=None,org=None,expected=200):
        headers={}
        if actor is not None:headers['Authorization']='Bearer '+tokens[actor]
        if org:headers['X-Org-Id']=org
        if body is not None:headers['Content-Type']='application/json'
        req=Request('http://127.0.0.1:8000'+path,method=method,headers=headers,data=json.dumps(body).encode() if body is not None else None)
        try:
            with urlopen(req,timeout=20) as r:status=r.status;data=json.load(r)
        except HTTPError as e:
            status=e.code;data=json.load(e)
        if status!=expected:raise AssertionError(f'{method} {path}: expected {expected}, got {status}; response code={data.get("code") if isinstance(data,dict) else None}')
        checks.append(method+' '+path+':'+str(status));return data
    for path in ['/api/health','/api/health/db']:
        assert request('GET',path)['status']=='ok'
    request('GET','/api/auth/me',1)
    org=request('POST','/api/org',0,{'name':'Synthetic smoke '+uuid4().hex,'owner_phone':users[1][1]})['data']['id']
    request('GET','/api/org',1)
    request('GET',f'/api/org/{org}/members',1)
    conv=request('POST','/api/conversations',1,{'title':'Synthetic isolated conversation'},org)
    cid=conv['id']
    request('GET','/api/conversations',1,org=org)
    request('GET',f'/api/conversations/{cid}',1,org=org)
    request('GET',f'/api/conversations/{cid}',2,org=org,expected=403)
    with psycopg.connect(config['DATABASE_URL']) as c:
        assert c.execute('SELECT count(*) FROM org_member_assignments WHERE org_id=%s AND user_id=%s',(org,users[1][0])).fetchone()[0]==1
        # Exercise the documented messages JSONB/status contract without model API calls.
        c.execute("INSERT INTO messages(conversation_id,org_id,role,content,status) VALUES(%s,%s,'user',%s,'completed')",(cid,org,Jsonb([{'type':'text','text':'Synthetic offline smoke'}])))
    r=redis.Redis(host='127.0.0.1',port=int(config['REDIS_PORT']),password=config['REDIS_PASSWORD'],db=15)
    assert r.ping();key='cloud-smoke:'+uuid4().hex;r.set(key,'synthetic');assert r.get(key)==b'synthetic';r.delete(key)
    # Include scheduled/background cycles, not just HTTP health readiness.
    time.sleep(observe_seconds)
    receipt=json.loads((ROOT/'.cloud-dev/readiness.json').read_text())
    with (ROOT/'.cloud-dev/backend.log').open('rb') as log:
        log.seek(receipt['backend_log_offset']);text=log.read().decode()
    forbidden=r'\bERROR\b|Undefined(?:Column|Table|Function)|does not exist|permission denied|Failed to query orphan tasks'
    if re.search(forbidden,text):raise AssertionError('Current backend startup/background log contains errors; inspect private logs')
    result={'status':'passed','checks':checks,'non_superuser':True,'transaction_rollback':True,'redis_db15':True,'background_observation_seconds':observe_seconds,'background_errors':0,'external_credentials':'not configured; no external provider requests'}
    (ROOT/'.cloud-dev/smoke-report.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))


if __name__=='__main__':smoke()
