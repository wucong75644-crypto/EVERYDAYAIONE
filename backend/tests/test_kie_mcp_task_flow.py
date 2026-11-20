"""Real isolated PostgreSQL proofs for recovery, cross-device visibility and fencing."""
import asyncio
from datetime import datetime, timezone
import json
import os
from uuid import uuid4
from unittest.mock import AsyncMock

import pytest
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from services.kie_mcp_task_store import TaskStore
from services import kie_mcp_task_flow as flow
from services import kie_video_mcp as mcp_service


@pytest.fixture
async def db_store():
    root = os.environ.get('KIE_FLOW_TEST_PG_ROOT')
    if not root:
        pytest.skip('Requires explicitly provisioned isolated PostgreSQL')
    assert root.startswith('/tmp/kie-flow-pg.'), 'Never use application databases in these tests'
    async with AsyncConnectionPool(conninfo=f'host={root} dbname=postgres user=everydayai',
                                   kwargs={'row_factory': dict_row}, open=False) as pool:
        async with pool.connection() as conn:
            await conn.execute('TRUNCATE public.kie_mcp_video_tasks')
        yield TaskStore(pool)


def payload():
    return {'model': 'kling-2.6/text-to-video', 'input': {'prompt': 'A pencil rolls', 'sound': False, 'duration': '5', 'aspect_ratio': '16:9'}}


async def reserve(store, key='request-key'):
    row, _ = await store.reserve(key, key+'-fingerprint', payload(), 'https://example.com')
    return row


def provider_record(row, state='success'):
    return {'taskId': 'vendor-123', 'model': row['payload']['model'], 'state': state,
            'param': json.dumps({**row['payload'], 'input': json.dumps(row['payload']['input'])}),
            'resultJson': json.dumps({'resultUrls': ['https://vendor.example/result.mp4']}),
            'creditsConsumed': 63}


@pytest.mark.asyncio
async def test_concurrent_reservations_only_one_dispatch_and_visible_after_reconnect(db_store):
    results = await asyncio.gather(*(db_store.reserve('same-key','fingerprint',payload(),'https://example.com') for _ in range(12)))
    assert sum(created for _, created in results) == 1
    assert len({str(row['id']) for row, _ in results}) == 1
    other_device = TaskStore(await db_store.pool())
    assert len(await other_device.listing()) == 1
    with pytest.raises(ValueError, match='different request'):
        await other_device.reserve('same-key', 'changed-input', payload())


@pytest.mark.asyncio
async def test_multiworker_claim_and_stale_lease_cannot_publish(db_store):
    row = await reserve(db_store)
    await db_store.bind(row['id'], 'vendor-123')
    claimed = await asyncio.gather(db_store.claim(), db_store.claim())
    first = next(r for r in claimed if r)
    assert sum(r is not None for r in claimed) == 1
    await db_store.query("UPDATE public.kie_mcp_video_tasks SET lease_until=now()-interval '1 second' WHERE id=%s RETURNING id", (row['id'],))
    second = await db_store.claim()
    assert first['lease_token'] != second['lease_token']
    assert await db_store.finish(first, state='fail') is None
    await db_store.finish(second, state='running')
    assert (await db_store.get(str(row['id'])))['state'] == 'running'


@pytest.mark.asyncio
async def test_callback_recovers_lost_post_and_saves_authoritative_result(db_store, monkeypatch):
    monkeypatch.setattr(flow, 'store', db_store)
    row = await reserve(db_store)
    # The POST response was lost; a callback can only provide a candidate, not publish it.
    await db_store.wake(row['id'], 'vendor-123')
    assert (await db_store.get(str(row['id'])))['provider_task_id'] is None
    request = AsyncMock(return_value=provider_record(row))
    archiver = AsyncMock(return_value=['https://our-oss.example/saved.mp4'])
    await flow.advance(await db_store.claim(), request, archiver)
    recovered = await TaskStore(await db_store.pool()).get(str(row['id']))
    assert recovered['provider_task_id'] == 'vendor-123'
    result = flow.task_view(recovered)
    assert result['state'] == 'success'
    assert result['video_urls'] == ['https://our-oss.example/saved.mp4']
    assert result['continue_waiting'] is False
    assert await db_store.claim() is None
    request.assert_awaited_once_with('GET', 'recordInfo', params={'taskId': 'vendor-123'})


@pytest.mark.asyncio
@pytest.mark.parametrize('wrong', ['callback', 'input', 'model'])
async def test_callback_candidate_cannot_bind_unrelated_provider_task(db_store, monkeypatch, wrong):
    monkeypatch.setattr(flow, 'store', db_store)
    row = await reserve(db_store)
    await db_store.wake(row['id'], 'unrelated-task')
    record = provider_record(row)
    record['taskId'] = 'unrelated-task'
    params = dict(row['payload'])
    if wrong == 'callback':
        params['callBackUrl'] = 'https://attacker.example'
    elif wrong == 'input':
        params['input'] = {'prompt': 'another video'}
    else:
        record['model'] = 'another-model'
    record['param'] = json.dumps(params)
    archiver = AsyncMock()
    await flow.advance(await db_store.claim(), AsyncMock(return_value=record), archiver)
    result = await db_store.get(str(row['id']))
    assert result['state'] == 'submission_unknown'
    assert result['provider_task_id'] is None
    assert result['callback_candidate'] is None
    archiver.assert_not_awaited()


@pytest.mark.asyncio
async def test_archive_retry_preserves_generation_and_never_recreates(db_store, monkeypatch):
    monkeypatch.setattr(flow, 'store', db_store)
    row = await reserve(db_store)
    await db_store.bind(row['id'], 'vendor-123')
    request = AsyncMock(return_value=provider_record(row))
    await flow.advance(await db_store.claim(), request, AsyncMock(side_effect=ValueError('storage down')))
    generated = await db_store.get(str(row['id']))
    assert generated['state'] == 'success' and generated['storage_state'] == 'retry'
    assert flow.task_view(generated)['video_urls'] == ['https://vendor.example/result.mp4']
    await db_store.query('UPDATE public.kie_mcp_video_tasks SET next_check_at=now() WHERE id=%s RETURNING id',(row['id'],))
    await flow.advance(await db_store.claim(), request, AsyncMock(return_value=['https://our-oss.example/saved.mp4']))
    assert (await db_store.get(str(row['id'])))['storage_state'] == 'stored'
    assert request.await_count == 1


@pytest.mark.asyncio
async def test_missing_callback_is_recovered_by_provider_poll(db_store, monkeypatch):
    monkeypatch.setattr(flow, 'store', db_store)
    row = await reserve(db_store)
    await db_store.bind(row['id'], 'vendor-123')
    request = AsyncMock(side_effect=[provider_record(row, 'generating'), provider_record(row)])
    await flow.advance(await db_store.claim(), request, AsyncMock())
    assert (await db_store.get('vendor-123'))['state'] == 'running'
    await db_store.query('UPDATE public.kie_mcp_video_tasks SET next_check_at=now() WHERE id=%s RETURNING id',(row['id'],))
    await flow.advance(await db_store.claim(), request, AsyncMock(return_value=['https://our-oss.example/result.mp4']))
    assert (await db_store.get('vendor-123'))['state'] == 'success'


@pytest.mark.asyncio
async def test_cross_device_lists_can_page_and_identity_cannot_change(db_store):
    rows = [await reserve(db_store, 'request-key-'+str(i)) for i in range(3)]
    page = await db_store.listing(limit=2)
    rest = await TaskStore(await db_store.pool()).listing(str(page[-1]['id']), limit=2)
    assert {r['id'] for r in page+rest} == {r['id'] for r in rows}
    await db_store.bind(rows[0]['id'], 'one-provider-id')
    with pytest.raises(ValueError, match='identity conflict'):
        await db_store.bind(rows[0]['id'], 'other-provider-id')


@pytest.mark.asyncio
async def test_wait_returns_completed_result_without_user_prompt(monkeypatch):
    getter = AsyncMock(return_value={'task_id': 'vendor-123', 'state': 'success', 'video_urls': ['https://our.example/video.mp4']})
    monkeypatch.setattr(mcp_service, 'get_video_task', getter)
    result = await mcp_service.wait_video_task('vendor-123', 1)
    assert result['continue_waiting'] is False
    assert result['video_urls']


@pytest.mark.asyncio
async def test_wait_timeout_is_not_provider_failure(monkeypatch):
    getter = AsyncMock(return_value={'task_id': 'vendor-123', 'state': 'waiting', 'video_urls': [], 'failed': False})
    monkeypatch.setattr(mcp_service, 'get_video_task', getter)
    result = await mcp_service.wait_video_task('vendor-123', 1)
    assert result['wait_timed_out'] is True
    assert result['continue_waiting'] is True
    assert result['failed'] is False


@pytest.mark.asyncio
async def test_wait_connection_error_does_not_recreate(monkeypatch):
    monkeypatch.setattr(mcp_service, 'get_video_task', AsyncMock(side_effect=ValueError('temporary outage')))
    result = await mcp_service.wait_video_task('vendor-123', 1)
    assert result['query_error'] == 'TASK_QUERY_UNAVAILABLE'
    assert result['continue_waiting'] is True


@pytest.mark.asyncio
async def test_callback_body_cannot_publish_video_urls(db_store, monkeypatch):
    from fastapi import FastAPI
    import httpx
    from api.routes.kie_mcp_callback import router
    monkeypatch.setattr(flow, 'store', db_store)
    row = await reserve(db_store)
    app = FastAPI()
    app.include_router(router, prefix='/api')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='https://test') as client:
        response = await client.post('/api/kie-mcp-callback/'+str(row['id']), json={
            'data': {'taskId': 'vendor-123', 'state': 'success', 'resultJson': '{"resultUrls":["https://attacker.example/fake.mp4"]}'}})
        assert response.status_code == 202
    saved = await db_store.get(str(row['id']))
    assert saved['state'] == 'submission_unknown'
    assert saved['source_urls'] == []


@pytest.mark.asyncio
async def test_provider_response_must_match_requested_task_id(db_store, monkeypatch):
    monkeypatch.setattr(flow, 'store', db_store)
    row = await reserve(db_store)
    await db_store.bind(row['id'], 'correct-task')
    record = provider_record(row)  # response taskId is vendor-123, not correct-task
    archiver = AsyncMock()
    await flow.advance(await db_store.claim(), AsyncMock(return_value=record), archiver)
    result = await db_store.get('correct-task')
    assert result['state'] == 'waiting'
    assert result['error_code'] == 'PROVIDER_TASK_ID_MISMATCH'
    archiver.assert_not_awaited()


@pytest.mark.asyncio
async def test_end_to_end_lost_response_callback_and_other_device_wait(db_store, monkeypatch):
    from types import SimpleNamespace
    import httpx
    from fastapi import FastAPI
    from api.routes.kie_mcp_callback import router
    monkeypatch.setattr(flow, 'store', db_store)
    monkeypatch.setattr(mcp_service, 'store', db_store)
    monkeypatch.setattr(mcp_service, 'get_settings', lambda: SimpleNamespace(kie_mcp_callback_base_url='https://example.com'))
    cache = SimpleNamespace(get=AsyncMock(return_value=None), set=AsyncMock(return_value=True))
    monkeypatch.setattr(mcp_service.RedisClient, 'get_client', AsyncMock(return_value=cache))
    upstream = AsyncMock(side_effect=ValueError('POST response lost'))
    monkeypatch.setattr(mcp_service, 'kie_request', upstream)
    created = await mcp_service.create_video('e2e-lost-response', 'A pencil rolls')
    assert created['state'] == 'submission_unknown'
    row = await db_store.get(created['local_task_id'])
    app = FastAPI()
    app.include_router(router, prefix='/api')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='https://example.com') as callback:
        assert (await callback.post('/api/kie-mcp-callback/'+created['local_task_id'], json={'data': {'taskId': 'vendor-123'}})).status_code == 202
    upstream.side_effect = None
    upstream.return_value = provider_record(row)
    await flow.advance(await db_store.claim(), archiver=AsyncMock(return_value=['https://our-oss.example/persisted.mp4']))
    # Simulate another client/process reading the same persisted database, not local state.
    monkeypatch.setattr(mcp_service, 'store', TaskStore(await db_store.pool()))
    listed = await mcp_service.list_video_tasks()
    assert listed['tasks'][0]['local_task_id'] == created['local_task_id']
    completed = await mcp_service.wait_video_task(created['local_task_id'], 1)
    assert completed['video_urls'] == ['https://our-oss.example/persisted.mp4']
    assert completed['continue_waiting'] is False
    repeated = await mcp_service.create_video('e2e-lost-response', 'A pencil rolls')
    assert repeated['reused'] is True
    assert [call.args[0] for call in upstream.await_args_list] == ['POST', 'GET']
