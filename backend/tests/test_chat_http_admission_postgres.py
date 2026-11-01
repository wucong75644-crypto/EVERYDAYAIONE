"""Real HTTP/JWT/tenant/idempotent admission and anchored PG history; no model IO."""
import asyncio
import json
from pathlib import Path
from time import perf_counter
from uuid import uuid4
from unittest.mock import AsyncMock

import httpx
import psycopg
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from tests.test_chat_latency_chain_postgres import (
    latency_db, private_redis, continuation_db, children_db, operation_db,
    task_db, composition_db, ecom_db, isolated_db,
)


@pytest.fixture(scope='module')
def admission_db(latency_db):
    with psycopg.connect(latency_db) as c:
        c.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
        c.execute('''ALTER TABLE conversations ADD COLUMN IF NOT EXISTS title text DEFAULT '原会话',
          ADD COLUMN IF NOT EXISTS created_at timestamptz DEFAULT now(),
          ADD COLUMN IF NOT EXISTS updated_at timestamptz DEFAULT now(),
          ADD COLUMN IF NOT EXISTS context_summary text,
          ADD COLUMN IF NOT EXISTS context_summary_revision bigint DEFAULT 0,
          ADD COLUMN IF NOT EXISTS context_summary_through_revision bigint DEFAULT 0''')
        c.execute('ALTER TABLE messages ADD COLUMN IF NOT EXISTS client_request_id text, ADD COLUMN IF NOT EXISTS knowledge_refs jsonb DEFAULT \'[]\'')
        c.execute((Path(__file__).parents[1]/'migrations/119_message_generation_idempotency.sql').read_text())
        c.execute('GRANT SELECT,INSERT,UPDATE ON message_generation_requests TO everydayai')
        c.execute((Path(__file__).parents[1]/'migrations/127_actor_tenant_rpc_contract.sql').read_text())
        c.execute('GRANT EXECUTE ON FUNCTION claim_message_generation_request(uuid,uuid,uuid,varchar,char,varchar,uuid), enqueue_generation_turn(jsonb,uuid,uuid,text,jsonb,uuid) TO everydayai')
    return latency_db


@pytest.mark.asyncio
async def test_http_auth_tenant_admission_idempotency_and_real_anchored_history(admission_db, private_redis, monkeypatch, tmp_path):
    from api.routes.message import router
    from core.config import get_settings
    from core.database import get_db
    from core.local_db import LocalDBClient
    from core.exceptions import AppException
    from core.security import create_access_token
    from redis.asyncio import Redis
    from services.agent.observability.chat_lifecycle import capture_chat_lifecycle
    from services.handlers.context_snapshot import ContextAnchor, build_context_snapshot

    settings=get_settings().model_copy(update={'conversation_actor_web_enabled':True,
        'skill_catalog_enabled':False,'skill_runtime_enabled':False,
        'knowledge_derivation_enabled':False,'knowledge_default_scope_enabled':False})
    monkeypatch.setattr('core.config.get_settings',lambda:settings)
    monkeypatch.setattr('core.config.settings',settings)
    redis=Redis.from_url(private_redis,decode_responses=True)
    for _ in range(100):
        try:
            await redis.ping();break
        except Exception:await asyncio.sleep(.01)
    else:pytest.fail('private Redis unavailable')
    monkeypatch.setattr('core.redis.RedisClient.get_client',AsyncMock(return_value=redis))
    actor,other,conv,old,old_turn=[str(uuid4()) for _ in range(5)]
    with psycopg.connect(admission_db) as c:
        c.execute('INSERT INTO users(id,credits) VALUES(%s,100),(%s,100)',(actor,other))
        c.execute("INSERT INTO conversations(id,user_id,scope_type,scope_id,source,context_revision) VALUES(%s,%s,'user',%s,'web',1)",(conv,actor,actor))
        c.execute("INSERT INTO messages(id,conversation_id,role,content,status,turn_id,context_revision,message_kind) VALUES(%s,%s,'assistant',%s,'completed',%s,1,'conversation')",(old,conv,json.dumps([{'type':'text','text':'必须保留的已有成果'}]),old_turn))
    dsn=psycopg.conninfo.make_conninfo(admission_db,user='everydayai',options=f'-c app.actor_user_id={actor} -c app.access_kind=runtime')
    from core.db_scope import ScopedDatabaseClient, DatabaseScope, DatabaseAccessKind
    db=ScopedDatabaseClient(LocalDBClient(dsn,min_size=1,max_size=3),DatabaseScope(actor,None,DatabaseAccessKind.RUNTIME))
    app=FastAPI();app.include_router(router,prefix='/api')
    app.dependency_overrides[get_db]=lambda:db
    @app.exception_handler(AppException)
    async def failure(_request,error):return JSONResponse(status_code=error.status_code,content={'code':error.code})
    trace=[];timings={}
    body={'generation_type':'chat','model':'qwen3.8-flash','content':[{'type':'text','text':'你是谁？'}],
        'client_request_id':str(uuid4()),'client_task_id':str(uuid4()),'assistant_message_id':str(uuid4())}
    url=f'/api/conversations/{conv}/messages/generate'
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1') as client:
            denied=await client.post(url,json=body);assert denied.status_code==401
            headers={'Authorization':'Bearer '+create_access_token({'sub':actor})}
            wrong=await client.post(url,json=body,headers={**headers,'X-Org-Id':str(uuid4())});assert wrong.status_code==403
            with capture_chat_lifecycle(trace):
                start=perf_counter();first=await client.post(url,json=body,headers=headers);timings['http_accept_ms']=round((perf_counter()-start)*1000,3)
                assert first.status_code==200,first.text
                start=perf_counter();repeat=await client.post(url,json=body,headers=headers);timings['http_replay_ms']=round((perf_counter()-start)*1000,3)
                assert repeat.status_code==200 and repeat.json()==first.json(),repeat.text
            with psycopg.connect(admission_db) as c:
                tasks=c.execute('SELECT * FROM tasks WHERE conversation_id=%s',(conv,)).fetchall();assert len(tasks)==1
                names=[d.name for d in c.execute('SELECT * FROM tasks LIMIT 0').description]
                task=dict(zip(names,tasks[0]))
                assert c.execute('SELECT count(*) FROM messages WHERE conversation_id=%s',(conv,)).fetchone()[0]==3
                assert c.execute('SELECT credits FROM users WHERE id=%s',(actor,)).fetchone()[0]==100
            anchor=ContextAnchor(str(task['id']),conv,str(task['turn_id']),str(task['input_message_id']),task['base_context_revision'],str(task['context_through_message_id']) if task['context_through_message_id'] else None,None)
            start=perf_counter();snapshot=await build_context_snapshot(db,anchor,'你是谁？');timings['real_history_snapshot_ms']=round((perf_counter()-start)*1000,3)
            assert '必须保留的已有成果' in json.dumps(snapshot.history_messages,ensure_ascii=False)
            assert '你是谁？' not in json.dumps(snapshot.history_messages,ensure_ascii=False)
            assert snapshot.anchor.input_message_id==first.json()['user_message']['id']
            assert len([r for r in trace if r['phase']=='enqueue.persisted'])==1
            destination=Path(__import__('os').getenv('CHAT_HTTP_EVIDENCE_DIR',tmp_path));destination.mkdir(parents=True,exist_ok=True)
            (destination/'http-admission.json').write_text(json.dumps({'scope':'local actual router/JWT/tenant/limit/idempotency/PG enqueue/history; model not called','timings':timings,'trace':trace},ensure_ascii=False,indent=2))
    finally:
        db.close();await redis.aclose()
