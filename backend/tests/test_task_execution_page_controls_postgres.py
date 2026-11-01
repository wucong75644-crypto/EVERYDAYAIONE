"""Web message controls across closed Actor windows; all effects stay original."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock
from uuid import uuid4
import httpx,psycopg,pytest
from psycopg.types.json import Jsonb
from tests.test_task_execution_controls_postgres import (controls_db,continuation_db,children_db,operation_db,task_db,
    composition_db,ecom_db,isolated_db,finished,enqueue,bundle_for,facts,connect)
ROOT=Path(__file__).parents[1]


@pytest.fixture(scope='module')
def page_db(controls_db):
    with psycopg.connect(controls_db) as c:c.execute((ROOT/'migrations/362_agent_task_public_progress.sql').read_text())
    yield controls_db
    with psycopg.connect(controls_db) as c:c.execute((ROOT/'migrations/rollback/362_agent_task_public_progress_rollback.sql').read_text())


@pytest.mark.asyncio
@pytest.mark.parametrize('already_queued',[False,True])
@pytest.mark.parametrize('final_action',['complete','cancel'])
async def test_real_http_pause_resume_keeps_source_output_and_reuses_native_continuation(page_db,monkeypatch,already_queued,final_action):
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from api.deps import get_org_context,get_scoped_db
    from api.routes.task import router
    from core.local_db import LocalDBClient
    from core.db_scope import ScopedDatabaseClient,DatabaseScope,DatabaseAccessKind
    from core.exceptions import AppException
    f,state,child,event=await finished(page_db,monkeypatch,binding_version=3)
    source=str(uuid4());content=[{'type':'text','text':'原规划完整原文'}, {'type':'task_progress','root_task_id':f['task']}]
    with psycopg.connect(page_db) as c:
        c.execute("INSERT INTO messages(id,conversation_id,role,content,status,context_revision,turn_id) VALUES(%s,%s,'assistant',%s,'completed',2,%s)",(source,f['conv'],Jsonb(content),f['turn']))
        c.execute('UPDATE tasks SET assistant_message_id=%s WHERE id=%s',(source,f['task']))
        c.execute('UPDATE conversations SET context_revision=2 WHERE id=%s',(f['conv'],))
    queued=enqueue(f,state,event) if already_queued else None
    original=facts(f)
    db=LocalDBClient(psycopg.conninfo.make_conninfo(f['dsn'],user='everydayai'),min_size=1,max_size=1)
    scoped=ScopedDatabaseClient(db,DatabaseScope(f['user'],None,DatabaseAccessKind.RUNTIME))
    app=FastAPI();app.include_router(router)
    app.dependency_overrides[get_org_context]=lambda:SimpleNamespace(user_id=f['user'],org_id=None)
    app.dependency_overrides[get_scoped_db]=lambda:scoped
    @app.exception_handler(AppException)
    async def error(request,e):return JSONResponse({'code':e.code},status_code=e.status_code)
    wakeup=AsyncMock();monkeypatch.setattr('services.conversation_worker.RedisConversationWakeup',lambda:SimpleNamespace(publish=wakeup))
    monkeypatch.setattr('api.routes.task.release_task_slot',AsyncMock())
    monkeypatch.setattr('services.websocket_manager.ws_manager.cancel_task',Mock())
    monkeypatch.setattr('services.websocket_manager.ws_manager.publish_cancelled_gate',AsyncMock())
    monkeypatch.setattr('services.websocket_manager.ws_manager.clear_cancelled_gate',AsyncMock())
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://local-test') as client:
            before=await client.get(f"/tasks/{f['task']}/execution",params={'message_id':source})
            assert before.status_code==200,before.text
            paused=await client.post('/tasks/pause-by-message/'+source)
            assert paused.status_code==200 and paused.json()['paused_count']==1,paused.text
            view=await client.get(f"/tasks/{f['task']}/execution",params={'message_id':source})
            assert view.json()['status']=='paused'
            resumed=await client.post('/tasks/resume-by-message/'+source)
            assert resumed.status_code==200 and resumed.json()['resumed_count']==1,resumed.text
            receipt=resumed.json()['tasks'][0]
            assert receipt['outcome']=='enqueued' and receipt['logical_task_id']==f['task']
            assert facts(f)['artifacts']==original['artifacts'] and facts(f)['operations']==original['operations']
            with connect(f) as c:
                source_row=c.execute('SELECT status,content::jsonb FROM messages WHERE id=%s',(source,)).fetchone()
                assert source_row==('completed',content)
                assert c.execute("SELECT count(*) FROM tasks WHERE type='image' AND conversation_id=%s",(f['conv'],)).fetchone()[0]==1
            if final_action=='cancel':
                cancelled=await client.post('/tasks/cancel-by-message/'+source)
                assert cancelled.status_code==200 and facts(f)['status']=='cancelled',cancelled.text
                with connect(f) as c:
                    assert c.execute('SELECT status,content::jsonb FROM messages WHERE id=%s',(source,)).fetchone()==('completed',content)
                    assert c.execute('SELECT status FROM tasks WHERE id=%s',(child['task_id'],)).fetchone()[0]=='completed'
                final=await client.get(f"/tasks/{f['task']}/execution",params={'message_id':source})
                assert final.json()['status']=='cancelled'
                return
            current=type(state).model_validate(facts(f))
            queued=queued or enqueue(f,current,event)
            task,claim,payload,candidate=bundle_for(f,queued['task_id'])
            assert candidate.status=='completed' and candidate.original_goal==state.original_goal
            with connect(f) as c:
                result=c.execute('SELECT apply_agent_image_continuation(%s,%s,%s,%s)',(claim.task_id,claim.execution_token,current.revision,Jsonb(payload))).fetchone()[0]
                assert result['outcome']=='saved'
            final=await client.get(f"/tasks/{f['task']}/execution",params={'message_id':source})
            assert final.status_code==200 and final.json()['status']=='completed',final.text
            assert final.json()['completed_requirements']==final.json()['total_requirements']
            assert (await client.get(f"/tasks/{f['task']}/execution",params={'message_id':f['message']})).status_code==404
        wakeup.assert_awaited_once()
    finally:db.pool.close()
