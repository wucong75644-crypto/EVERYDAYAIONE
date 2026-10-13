"""Real transactional retry identity, stale delivery and recovery regressions."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import psycopg
import pytest

from tests.test_chat_image_lifecycle_postgres import (
    isolated_db, facts, accept, claim, worker_rpc, publish, replay_sql,
)


def failed(facts):
    receipt=accept(facts)
    worker_rpc(facts,'fail_chat_image_before_dispatch',receipt['task_id'],'fixture','queued',None)
    publish(facts,receipt['task_id'])
    return receipt


@pytest.mark.parametrize('same_request', [True,False])
def test_failed_retry_reuses_slot_and_collapses_concurrent_clicks(facts,same_request):
    original=failed(facts)
    request=str(uuid4())
    with ThreadPoolExecutor(4) as pool:
        receipts=list(pool.map(lambda _:replay_sql(facts,original['task_id'],request if same_request else str(uuid4())),range(4)))
    assert sum(r['outcome']=='accepted' for r in receipts)==1
    assert len({r['task_id'] for r in receipts})==1
    assert {r['message_id'] for r in receipts}=={original['message_id']}
    latest=receipts[0]['task_id']
    with psycopg.connect(facts['dsn']) as c:
        message=c.execute('SELECT status,generation_params,content,created_at FROM messages WHERE id=%s',(original['message_id'],)).fetchone()
        assert message[0]=='pending' and message[1]['task_id']==latest
        assert 'failed' not in message[2]
        assert c.execute('SELECT count(*) FROM messages WHERE conversation_id=%s',(facts['snapshot']['origin']['conversation_id'],)).fetchone()[0]==1
        assert c.execute("SELECT request_params->'_media_retry_budget_v1'->>'requests' FROM tasks WHERE id=%s",(facts['parent'],)).fetchone()[0]=='1'
        assert c.execute('SELECT count(*) FROM credit_transactions WHERE user_id=%s',(facts['user'],)).fetchone()[0]==0
        assert c.execute('SELECT assistant_message_id,placeholder_message_id FROM tasks WHERE id=%s',(latest,)).fetchone()==(UUID(original['message_id']),original['message_id'])
    assert replay_sql(facts,original['task_id'],request)['task_id']==latest


def test_repeat_failure_stays_in_same_slot_and_old_publication_cannot_overwrite(facts):
    original=failed(facts)
    first=replay_sql(facts,original['task_id'],str(uuid4()))
    worker_rpc(facts,'fail_chat_image_before_dispatch',first['task_id'],'fixture','queued',None)
    publish(facts,first['task_id'])
    second=replay_sql(facts,first['task_id'],str(uuid4()))
    assert second['message_id']==first['message_id']==original['message_id']
    assert second['task_id']!=first['task_id']
    assert publish(facts,original['task_id'])['outcome']=='superseded'
    assert publish(facts,first['task_id'])['outcome']=='superseded'
    with psycopg.connect(facts['dsn']) as c:
        row=c.execute('SELECT status,generation_params FROM messages WHERE id=%s',(original['message_id'],)).fetchone()
        assert row[0]=='pending' and row[1]['task_id']==second['task_id']
    with pytest.raises(psycopg.Error,match='RETRY_SUPERSEDED'):
        replay_sql(facts,original['task_id'],str(uuid4()))


def test_completed_image_regeneration_keeps_old_image(facts):
    original=failed(facts)
    with psycopg.connect(facts['dsn']) as c:
        c.execute("UPDATE tasks SET status='completed' WHERE id=%s",(original['task_id'],))
        c.execute("UPDATE messages SET status='completed',content='[{\"type\":\"image\",\"url\":\"original.png\"}]' WHERE id=%s",(original['message_id'],))
    next_image=replay_sql(facts,original['task_id'],str(uuid4()))
    assert next_image['message_id']!=original['message_id']
    with psycopg.connect(facts['dsn']) as c:
        assert 'original.png' in c.execute('SELECT content FROM messages WHERE id=%s',(original['message_id'],)).fetchone()[0]
        assert c.execute('SELECT count(*) FROM messages WHERE conversation_id=%s',(facts['snapshot']['origin']['conversation_id'],)).fetchone()[0]==2


async def test_old_delivery_is_acknowledged_without_sending_new_slot_as_old_task(monkeypatch):
    from services.handlers.chat_image_lifecycle import ChatImageLifecycle
    service=object.__new__(ChatImageLifecycle)
    service.rpc=AsyncMock()
    latest={'id':'message','content':'[]','generation_params':{'origin':'chat_image','task_id':'new'}}
    query=Mock();query.table.return_value=query;query.select.return_value=query;query.eq.return_value=query;query.single.return_value=query
    query.execute.return_value=SimpleNamespace(data=latest)
    service.scoped=Mock(return_value=query)
    limiter=SimpleNamespace(release=AsyncMock())
    monkeypatch.setattr('api.deps.get_task_limit_service',AsyncMock(return_value=limiter))
    send=AsyncMock();monkeypatch.setattr('services.websocket_manager.ws_manager.send_to_task_or_user',send)
    task={'id':'old','user_id':'user','conversation_id':'conv','org_id':None,'assistant_message_id':'message',
        'request_params':{'_media_request_v1':{'origin':{}}}}
    await service.deliver(task)
    send.assert_not_awaited()
    service.rpc.assert_awaited_once_with(task,'ack_chat_image_delivery')
    limiter.release.assert_awaited_once()


def test_pending_query_only_restores_current_attempt():
    from api.routes.task import _current_image_attempts
    query=Mock();query.table.return_value=query;query.select.return_value=query;query.in_.return_value=query
    query.execute.return_value=SimpleNamespace(data=[{'id':'slot','generation_params':{'task_id':'new'}}])
    tasks=[{'id':t,'assistant_message_id':'slot','request_params':{'_media_request_v1':{ 'origin':{} }}} for t in ('old','new')]
    assert [t['id'] for t in _current_image_attempts(query,tasks)]==['new']


def test_failed_retry_refunds_old_attempt_and_charges_success_exactly_once(facts):
    from psycopg.types.json import Jsonb
    def settle(task, status):
        assert claim(facts,task)['outcome']=='claimed'
        with psycopg.connect(facts['dsn']) as c:
            token=c.execute("SELECT request_params->'_media_lifecycle_v1'->>'claim_token' FROM tasks WHERE id=%s",(task,)).fetchone()[0]
        assert worker_rpc(facts,'mark_chat_image_dispatch',task,token)['outcome']=='dispatch'
        worker_rpc(facts,'bind_chat_image_submission',task,token,str(uuid4()))
        worker_rpc(facts,'record_chat_image_provider_result',task,Jsonb({'status':status,'image_urls':['fixture-output']}))
        return publish(facts,task,status='completed' if status=='success' else 'failed')
    original=accept(facts)
    settle(original['task_id'],'failed')
    with ThreadPoolExecutor(4) as pool:
        receipts=list(pool.map(lambda _:replay_sql(facts,original['task_id'],str(uuid4())),range(4)))
    latest=receipts[0]['task_id']
    assert len({r['task_id'] for r in receipts})==1
    assert receipts[0]['message_id']==original['message_id']
    settle(latest,'success')
    assert publish(facts,original['task_id'])['outcome']=='superseded'
    assert publish(facts,latest,status='completed')['outcome']=='replay'
    with psycopg.connect(facts['dsn']) as c:
        cost=facts['snapshot']['estimated_credits']
        assert c.execute('SELECT credits FROM users WHERE id=%s',(facts['user'],)).fetchone()[0]==100-cost
        assert sorted(c.execute('SELECT status,amount FROM credit_transactions WHERE user_id=%s',(facts['user'],)).fetchall())==[('confirmed',cost),('refunded',cost)]
        message=c.execute('SELECT status,generation_params FROM messages WHERE id=%s',(original['message_id'],)).fetchone()
        assert message[0]=='completed' and message[1]['task_id']==latest
