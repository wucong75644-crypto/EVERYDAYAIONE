"""Production-shaped TEXT input -> actual context/builder -> Gateway -> delivery.

No _build_llm_messages replacement. Only external memory/provider/transport and
closed-history cache are fixtures. Knowledge reads use native runtime-role RLS.
"""
import asyncio
import json
import threading
from dataclasses import replace
from types import MethodType
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import psycopg
import pytest

from core.config import get_settings
from core.local_db import LocalDBClient
from schemas.message import TextPart
from services.adapters.types import StreamChunk
from services.handlers.context_snapshot import ContextAnchor
from services.handlers.chat_context_mixin import ChatContextMixin
from services.handlers.chat.stream_runner import run_legacy_chat_stream
from tests.test_chat_gateway_retry_integration import environment, make_adapter, web_request, websocket
from tests.test_knowledge_chat_v3 import current_snapshot_db, v3_db, local_db, document, start


@pytest.fixture(scope='module')
def pipeline_db(current_snapshot_db):
    make, actors = current_snapshot_db
    with psycopg.connect(**make.admin_args) as c:
        c.execute("ALTER TABLE messages ADD COLUMN IF NOT EXISTS content text DEFAULT '[]', ADD COLUMN IF NOT EXISTS turn_id uuid")
        c.execute("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS context_summary text, ADD COLUMN IF NOT EXISTS summary_revision bigint DEFAULT 0, ADD COLUMN IF NOT EXISTS source text DEFAULT 'web'")
        c.execute('''CREATE TABLE IF NOT EXISTS task_attachment_refs(
            attachment_id uuid,attachment_set_id uuid,task_id uuid,input_message_id uuid,turn_id uuid,org_id uuid)''')
        c.execute('GRANT SELECT ON conversations,messages,task_attachment_refs TO everydayai_runtime')
    return make, actors


@pytest.mark.asyncio
@pytest.mark.parametrize('question', ['你好', '帮我换个说法', '我想随便聊两句'])
@pytest.mark.parametrize('preparation', ['real', 'slow', 'failed'])
async def test_native_text_input_and_parallel_preparation_never_force_audit(
        environment,pipeline_db,monkeypatch,question,preparation):
    make,_=pipeline_db;r=make();r.initialize();document(r)
    _,mid,cid=start(make,r,[]);turn=str(uuid4())
    parts=[TextPart(text=question)]
    with psycopg.connect(**make.admin_args) as c:
        c.execute('UPDATE messages SET content=%s,turn_id=%s WHERE id=%s',
            (json.dumps([p.model_dump(mode='json') for p in parts],ensure_ascii=False),turn,mid))
    settings=get_settings().model_copy(update={'knowledge_workspace_enabled':True,
        'knowledge_derivation_enabled':True,'knowledge_default_scope_enabled':True,
        'knowledge_workspace_user_ids':'','knowledge_workspace_org_ids':''})
    monkeypatch.setattr('core.config.get_settings',lambda:settings)
    monkeypatch.setattr('services.knowledge_workspace.runtime.repository',lambda *_:r)
    monkeypatch.setattr('services.handlers.conversation_cache.get_closed_messages',AsyncMock(return_value=[]))
    monkeypatch.setattr('services.prompt_builder.session_memory_cache.get_session_memory',AsyncMock(return_value=(None,'')))
    audit=AsyncMock(side_effect=AssertionError('ordinary chat cannot require independent knowledge audit'))
    monkeypatch.setattr('services.knowledge_workspace.grounding.request',audit)
    monkeypatch.setattr('services.knowledge_workspace.answer_plan.collect',lambda *_:pytest.fail('pre-model retrieval'))
    release_worker=threading.Event();worker_started=threading.Event()
    if preparation!='real':
        def collect(*_):
            worker_started.set()
            if preparation=='failed':raise RuntimeError('local retrieval unavailable')
            assert release_worker.wait(3)
            return {}
        monkeypatch.setattr('services.knowledge_workspace.candidates.collect',collect)
    db=LocalDBClient(psycopg.conninfo.make_conninfo(**make.admin_args,
        options=f'-c role=everydayai_runtime -c app.actor_user_id={r.scope.actor_user_id} -c app.org_id={r.scope.org_id} -c app.access_kind=runtime'),
        min_size=1,max_size=4)
    h=environment.handler;h.db=db;h.org_id=r.scope.org_id
    h._build_llm_messages=MethodType(ChatContextMixin._build_llm_messages,h)
    ready=asyncio.Event();finish=asyncio.Event()
    async def stream(**kwargs):
        yield StreamChunk(content='第一段',prompt_tokens=3)
        ready.set();await finish.wait()
        yield StreamChunk(content='第二段',completion_tokens=2)
    adapter=make_adapter();adapter.stream_chat=Mock(side_effect=stream)
    factory=environment.configure(adapter)
    request=replace(web_request(),content=parts,user_id=r.scope.actor_user_id,conversation_id=cid,
        context_anchor=ContextAnchor(str(uuid4()),cid,turn,mid,0,None,r.scope.org_id))
    ws=websocket();job=asyncio.create_task(run_legacy_chat_stream(handler=h,request=request,websocket=ws))
    try:
        await asyncio.wait_for(ready.wait(),5)
        events=[call.args[2] for call in ws.send_to_task_or_user.await_args_list]
        assert any(e.get('type')=='message_chunk' for e in events)
        assert h._knowledge_input_facts.question==question
        assert h._knowledge_run is None and h._knowledge_answer_guard is None
        if preparation=='slow':
            assert worker_started.wait(.5) and not release_worker.is_set()
        audit.assert_not_awaited()
    finally:
        finish.set();release_worker.set()
        await asyncio.wait_for(job,5)
        db.close()
    h.on_error.assert_not_awaited();h.on_complete.assert_awaited_once()
    h._deduct_directly.assert_called_once()
    assert factory.call_count==1
    audit.assert_not_awaited()
    with psycopg.connect(**make.admin_args) as c:
        assert c.execute('SELECT count(*) FROM kb_read_snapshots WHERE input_message_id=%s',(mid,)).fetchone()[0]==0


@pytest.mark.asyncio
async def test_candidates_are_read_only_versioned_and_consumed_through_original_read_protocol(pipeline_db):
    from services.knowledge_workspace.candidates import CandidatePreparation
    from services.knowledge_workspace.chat import KnowledgeRun
    from tests.test_knowledge_chat_v3 import ref
    from tests.test_knowledge_workspace_v2 import command
    from core.exceptions import AppException
    make,_=pipeline_db;r=make();r.initialize()
    oid=r.command(command('create_project',{'title':'候选验证-'+str(uuid4()),'position':{'x':0,'y':0}}))['object_id']
    r.command(command('save_document',{'object_id':oid,'expected_revision':1,'body':'中文正文中的验收关键词'}))
    obj=r.object(oid)
    _,mid,cid=start(make,r,[]);turn=str(uuid4())
    query=obj['title']+' 验收关键词'
    with psycopg.connect(**make.admin_args) as c:
        c.execute('UPDATE messages SET content=%s,turn_id=%s WHERE id=%s',
            (json.dumps([{'type':'text','text':query}],ensure_ascii=False),turn,mid))
        before=c.execute('SELECT count(*) FROM kb_read_receipts').fetchone()[0]
    anchor=ContextAnchor(str(uuid4()),cid,turn,mid,0,None,r.scope.org_id)
    candidates=CandidatePreparation(r,anchor)
    try:
        await asyncio.wait_for(candidates.task,3)
        assert candidates.state=='ready'
        assert (str(obj['id']),int(obj['revision'])) in candidates.bodies
        with psycopg.connect(**make.admin_args) as c:
            assert c.execute('SELECT count(*) FROM kb_read_snapshots WHERE input_message_id=%s',(mid,)).fetchone()[0]==0
            assert c.execute('SELECT count(*) FROM kb_read_receipts').fetchone()[0]==before
        actual=KnowledgeRun.start(r,conversation_id=cid,input_message_id=mid,default_scope=True,question=query)
        assert await candidates.attach(actual,mid)
        assert not await candidates.attach(actual,str(uuid4()))
        foreign=KnowledgeRun(make(1),actual.id,actual.snapshot)
        assert not await candidates.attach(foreign,mid)
        item=next(i for i in actual.snapshot['items'] if i['id']==str(obj['id']) and i['type']=='object')
        root=item['root_handle']
        result=actual.execute('read',{'root_handle':root,'item_handles':[item['handle']]})
        assert result['items'][0]['body']=='中文正文中的验收关键词'
        assert actual._candidate_hits > 0
        # A new snapshot revision must miss the old body cache and read new data.
        r.command(command('save_document',{'object_id':obj['id'],'expected_revision':2,'body':'修改后的新正文'}))
        fresh,new_mid,new_cid=start(make,r,[ref(obj)])
        fresh._candidate_bodies=dict(candidates.bodies)
        new_item=next(i for i in fresh.snapshot['items'] if i['id']==str(obj['id']) and i['type']=='object')
        with r.cursor() as c:assert fresh._text(c,new_item)=='修改后的新正文'
        # A cache is not an authorization grant, including a guessed old handle.
        foreign=KnowledgeRun(make(1,1),actual.id,actual.snapshot)
        foreign._candidate_bodies=dict(candidates.bodies)
        with pytest.raises(AppException):
            with foreign.repo.cursor() as c:foreign._text(c,item)
    finally:
        await candidates.close()


@pytest.mark.asyncio
async def test_cancelled_preparation_does_not_free_slots_while_threads_are_running(monkeypatch):
    from services.knowledge_workspace.candidates import CandidatePreparation
    from types import SimpleNamespace
    started=threading.Event();release=threading.Event();finished=threading.Event();lock=threading.Lock()
    counts={'started':0,'active':0}
    def collect(*_):
        with lock:
            counts['started']+=1;counts['active']+=1
            if counts['started']==4:started.set()
        try:
            assert release.wait(3)
            return {}
        finally:
            with lock:
                counts['active']-=1
                if not counts['active']:finished.set()
    monkeypatch.setattr('services.knowledge_workspace.candidates.collect',collect)
    anchor=SimpleNamespace(input_message_id='input')
    jobs=[CandidatePreparation(object(),anchor) for _ in range(8)]
    try:
        assert await asyncio.to_thread(started.wait,2)
        await jobs[0].close()
        extra=CandidatePreparation(object(),anchor);jobs.append(extra)
        await extra.task
        assert extra.state=='busy' and counts['started']==4
    finally:
        release.set()
        for job in jobs:await job.close()
        await asyncio.gather(*(job.task for job in jobs),return_exceptions=True)
        assert await asyncio.to_thread(finished.wait,2)
    assert counts['active']==0 and counts['started']==4


@pytest.mark.asyncio
async def test_old_automatic_navigation_restores_without_forcing_audit_but_real_read_binds(pipeline_db,monkeypatch):
    from types import SimpleNamespace
    from services.knowledge_workspace.chat import KnowledgeRun
    from services.knowledge_workspace.runtime import prepare_knowledge_run
    from services.knowledge_workspace.answer_policy import bind_answer_policy
    make,_=pipeline_db;r=make();r.initialize()
    from tests.test_knowledge_workspace_v2 import command
    oid=r.command(command('create_project',{'title':'旧导航-'+str(uuid4()),'position':{'x':0,'y':0}}))['object_id']
    obj=r.object(oid)
    _,mid,cid=start(make,r,[])
    old=KnowledgeRun.start(r,conversation_id=cid,input_message_id=mid,default_scope=True,question=obj['title'])
    settings=get_settings().model_copy(update={'knowledge_derivation_enabled':True,'knowledge_default_scope_enabled':True})
    monkeypatch.setattr('core.config.get_settings',lambda:settings)
    monkeypatch.setattr('api.routes.knowledge.enabled',lambda _:True)
    monkeypatch.setattr('services.knowledge_workspace.runtime.repository',lambda *_:r)
    handler=SimpleNamespace(org_id=r.scope.org_id,db=object())
    anchor=ContextAnchor(str(uuid4()),cid,str(uuid4()),mid,0,None,r.scope.org_id)
    await prepare_knowledge_run(handler,anchor,r.scope.actor_user_id,cid,question='任意新表达')
    assert handler._knowledge_run.id==old.id
    assert bind_answer_policy(SimpleNamespace(),handler).mode=='stream'
    item=next(i for i in old.snapshot['items'] if i['id']==str(obj['id']) and i['type']=='object')
    old.execute('read',{'root_handle':item['root_handle'],'item_handles':[item['handle']]})
    await prepare_knowledge_run(handler,anchor,r.scope.actor_user_id,cid,question='任意新表达')
    assert bind_answer_policy(SimpleNamespace(),handler).mode=='audit_scope'
