"""Real Redis/SQL terminal slot recovery and release failure regressions."""
from collections import deque
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch
from uuid import uuid4
import psycopg
import pytest
from services.adapters.base import ImageGenerateResult, TaskStatus
from tests.test_detail_concurrency_postgres import runtime
from tests.test_chat_image_lifecycle_postgres import isolated_db
from tests.test_ecommerce_recovery_postgres import ecom_db
from tests.test_detail_page_generation_postgres import page_db
from tests.test_detail_page_delivery_recovery import delivery_db

@pytest.fixture(scope='module', autouse=True)
def schema(delivery_db): return delivery_db

async def submitted(runtime, monkeypatch):
    identity = runtime.accept(); worker = runtime.worker()
    adapter = NS(generate=AsyncMock(return_value=ImageGenerateResult(task_id=str(uuid4()),status=TaskStatus.PENDING)),close=AsyncMock())
    monkeypatch.setattr('services.adapters.factory.create_image_adapter',lambda *a,**kw:adapter)
    stale = await worker.refresh(identity)
    await worker.submit(stale)
    return identity,worker,adapter,stale

async def failed(worker,identity):
    await worker.record_provider_result(await worker.refresh(identity),ImageGenerateResult(task_id='external',status=TaskStatus.FAILED,fail_msg='isolated failure'))

async def test_release_failure_keeps_pending_and_restart_does_not_refund_twice(page_db,runtime,monkeypatch):
    identity,worker,adapter,stale = await submitted(runtime,monkeypatch)
    with patch.object(runtime.limiter.redis,'pipeline',side_effect=ConnectionError('isolated release outage')):
        with pytest.raises(ConnectionError): await failed(worker,identity)
    row = await worker.refresh(identity)
    assert row['status']=='failed' and row['request_params']['_media_lifecycle_v1']['delivery_pending'] is True
    key = runtime.limiter._global_key(identity['user_id'])
    assert await runtime.limiter.redis.sismember(key,identity['id'])
    restarted = runtime.worker()
    await restarted.advance(await restarted.refresh(identity))
    assert not await runtime.limiter.redis.sismember(key,identity['id'])
    assert (await restarted.refresh(identity))['request_params']['_media_lifecycle_v1']['delivery_pending'] is False
    with psycopg.connect(page_db) as db:
        assert db.execute('SELECT count(*),sum(change_amount) FROM credits_history WHERE user_id=%s',(identity['user_id'],)).fetchone()==(1,row['credits_locked'])
        assert db.execute('SELECT credits FROM users WHERE id=%s',(identity['user_id'],)).fetchone()[0]==1000
    adapter.generate.assert_awaited_once()

async def test_stale_queued_snapshot_releases_terminal_slot(runtime,monkeypatch):
    identity,worker,adapter,stale = await submitted(runtime,monkeypatch)
    await failed(worker,identity)
    await worker.submit(stale)
    assert not await runtime.limiter.redis.sismember(runtime.limiter._global_key(identity['user_id']),identity['id'])
    assert not await runtime.limiter.redis.sismember(runtime.limiter._conversation_key(identity['user_id'],None),identity['id'])
    adapter.generate.assert_awaited_once()

async def test_stale_queued_snapshot_retains_active_slot(runtime,monkeypatch):
    identity,worker,adapter,stale = await submitted(runtime,monkeypatch)
    await worker.submit(stale)
    assert await runtime.limiter.redis.sismember(runtime.limiter._global_key(identity['user_id']),identity['id'])
    assert (await worker.refresh(identity))['request_params']['_media_lifecycle_v1']['phase']=='accepted'
    adapter.generate.assert_awaited_once()

async def test_process_exit_window_cleanup_is_owner_scoped(runtime,monkeypatch):
    identity,worker,adapter,stale = await submitted(runtime,monkeypatch)
    await failed(worker,identity)
    limiter=runtime.limiter; key=limiter._global_key(identity['user_id'])
    # Re-acquire a terminal slot and lose the process before touching DB state.
    await limiter.acquire_image_slot(identity['user_id'],None,identity['id'],detail_project=True)
    wrong_key=limiter._global_key(str(uuid4())); unrelated=str(uuid4())
    await limiter.redis.sadd(wrong_key,identity['id'])
    await limiter.redis.sadd(key,unrelated,'ordinary-chat-slot')
    restarted=runtime.worker(); restarted._slot_keys=deque([wrong_key,key])
    try:
        await restarted.reconcile_terminal_slots()
        assert await limiter.redis.sismember(wrong_key,identity['id'])
        await restarted.reconcile_terminal_slots()
        assert not await limiter.redis.sismember(key,identity['id'])
        assert not await limiter.redis.sismember(limiter._conversation_key(identity['user_id'],None),identity['id'])
        assert await limiter.redis.sismember(key,unrelated)
        assert await limiter.redis.sismember(key,'ordinary-chat-slot')
        assert (await restarted.refresh(identity))['request_params']['_media_lifecycle_v1']['delivery_pending'] is False
    finally: await limiter.redis.delete(wrong_key)

async def test_cleanup_retains_active_and_retries_failed_batch(runtime,monkeypatch):
    identity,worker,adapter,stale=await submitted(runtime,monkeypatch)
    key=runtime.limiter._global_key(identity['user_id']); worker._slot_keys=deque([key])
    await worker.reconcile_terminal_slots()
    assert await runtime.limiter.redis.sismember(key,identity['id'])
    await failed(worker,identity)
    await runtime.limiter.acquire_image_slot(identity['user_id'],None,identity['id'],detail_project=True)
    worker._slot_keys=deque([key])
    with patch.object(runtime.limiter.redis,'pipeline',side_effect=ConnectionError('isolated cleanup outage')):
        with pytest.raises(ConnectionError): await worker.reconcile_terminal_slots()
    assert worker._slot_members
    await worker.reconcile_terminal_slots()
    assert not await runtime.limiter.redis.sismember(key,identity['id'])

async def test_reconciliation_resumes_bounded_members_across_rounds(runtime,monkeypatch):
    identity,worker,adapter,stale=await submitted(runtime,monkeypatch)
    await failed(worker,identity)
    limiter=runtime.limiter; key=limiter._global_key(identity['user_id'])
    await limiter.acquire_image_slot(identity['user_id'],None,identity['id'],detail_project=True)
    restarted=runtime.worker()
    restarted._slot_key=key
    restarted._slot_members=deque([str(uuid4()) for _ in range(149)]+[identity['id']])
    await restarted.reconcile_terminal_slots()
    assert len(restarted._slot_members)==100
    assert await limiter.redis.sismember(key,identity['id'])
    await restarted.reconcile_terminal_slots()
    assert len(restarted._slot_members)==50
    assert await limiter.redis.sismember(key,identity['id'])
    await restarted.reconcile_terminal_slots()
    assert not await limiter.redis.sismember(key,identity['id'])
    assert restarted._slot_key is None

async def test_legacy_release_remains_best_effort_but_strict_raises(runtime):
    with patch.object(runtime.limiter.redis,'pipeline',side_effect=ConnectionError('isolated release outage')):
        await runtime.limiter.release(str(uuid4()),None,slot_id=str(uuid4()))
        with pytest.raises(ConnectionError):
            await runtime.limiter.release(str(uuid4()),None,slot_id=str(uuid4()),strict=True)

async def test_fresh_scanner_discovers_terminal_slot_without_database_delivery_marker(runtime,monkeypatch):
    identity,worker,adapter,stale=await submitted(runtime,monkeypatch)
    await failed(worker,identity)
    limiter=runtime.limiter; key=limiter._global_key(identity['user_id'])
    await limiter.acquire_image_slot(identity['user_id'],None,identity['id'],detail_project=True)
    restarted=runtime.worker()
    # No seeded in-memory cursors: exercise actual SCAN and SSCAN discovery.
    for _ in range(500):
        await restarted.reconcile_terminal_slots()
        if not await limiter.redis.sismember(key,identity['id']): break
    assert not await limiter.redis.sismember(key,identity['id'])

async def test_reconciliation_db_failure_never_releases_and_retries_batch(runtime,monkeypatch):
    identity,worker,adapter,stale=await submitted(runtime,monkeypatch)
    await failed(worker,identity)
    limiter=runtime.limiter; key=limiter._global_key(identity['user_id'])
    await limiter.acquire_image_slot(identity['user_id'],None,identity['id'],detail_project=True)
    worker._slot_key=key; worker._slot_members=deque([identity['id']])
    with patch('services.handlers.chat_image_lifecycle.ScopedDatabaseClient',side_effect=ConnectionError('isolated DB lookup failure')):
        with pytest.raises(ConnectionError): await worker.reconcile_terminal_slots()
    assert list(worker._slot_members)==[identity['id']]
    assert await limiter.redis.sismember(key,identity['id'])
    await worker.reconcile_terminal_slots()
    assert not await limiter.redis.sismember(key,identity['id'])

async def test_scan_reconciliation_failure_does_not_block_normal_dispatch(runtime,monkeypatch):
    identity=runtime.accept(); worker=runtime.worker()
    adapter=NS(generate=AsyncMock(return_value=ImageGenerateResult(task_id=str(uuid4()),status=TaskStatus.PENDING)),close=AsyncMock())
    monkeypatch.setattr('services.adapters.factory.create_image_adapter',lambda *a,**kw:adapter)
    with patch.object(worker,'reconcile_terminal_slots',AsyncMock(side_effect=ConnectionError('isolated reconciliation outage'))):
        await worker.scan()
    import asyncio
    await asyncio.gather(*list(worker._work.jobs.values()))
    assert (await worker.refresh(identity))['request_params']['_media_lifecycle_v1']['phase']=='accepted'
    adapter.generate.assert_awaited_once()
