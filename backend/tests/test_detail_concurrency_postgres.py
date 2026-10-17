"""Real scanners/claims/Redis, with deliberately slow and interrupted supplier IO."""
import asyncio
import os
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import psycopg
import pytest
from redis.asyncio import Redis

from core.config import Settings
from core.local_db import LocalDBClient
from core.redis import RedisClient
from services.handlers.chat_image_lifecycle import ChatImageLifecycle
from services.task_limit_service import TaskLimitService
from services.adapters.base import ImageGenerateResult, TaskStatus
from tests.test_chat_image_lifecycle_postgres import isolated_db
from tests.test_ecommerce_recovery_postgres import ecom_db
from tests.test_detail_page_generation_postgres import page_db, ready_item, scoped


@pytest.fixture
async def runtime(page_db, monkeypatch, tmp_path):
    socket = os.getenv('CHAT_IMAGE_TEST_REDIS_SOCKET')
    if not socket: pytest.skip('Explicit isolated Redis socket required')
    assert socket == '/private/tmp/everydayai-chat-image-redis.sock'
    redis = Redis(unix_socket_path=socket, decode_responses=True)
    monkeypatch.setattr(RedisClient, '_instance', redis)
    limiter = TaskLimitService(redis)
    monkeypatch.setattr('api.deps.get_task_limit_service', AsyncMock(return_value=limiter))
    settings = Settings(_env_file=None, database_url='postgresql://invalid/test', jwt_secret_key='isolated-test-key',
        file_workspace_root=str(tmp_path), chat_image_processing_concurrency=2)
    monkeypatch.setattr('core.config.get_settings', lambda:settings)
    monkeypatch.setattr('services.detail_project_service.get_settings', lambda:settings)
    monkeypatch.setattr('services.detail_page_generation.get_settings', lambda:settings)
    raw = LocalDBClient(psycopg.conninfo.make_conninfo(page_db, user='everydayai'), min_size=1, max_size=8)
    workers, identities = [], []
    def worker():
        item = ChatImageLifecycle(raw, settings)
        workers.append(item)
        return item
    def accept_many(count):
        user, project, run, plan, items = ready_item(page_db, count)
        accepted = []
        for item in items:
            with scoped(page_db, user) as db:
                task = db.execute('SELECT accept_detail_page_image(%s,%s,%s,%s)',
                    (project, plan, item['item_id'], psycopg.types.json.Jsonb(item['snapshot']))).fetchone()[0]['task_id']
            identity = {'id':task, 'user_id':user, 'org_id':None}
            identities.append(identity)
            accepted.append(identity)
        return accepted
    def accept(): return accept_many(1)[0]
    yield NS(worker=worker, accept=accept, accept_many=accept_many, settings=settings, raw=raw, limiter=limiter)
    for item in workers: await item.close()
    for identity in identities:
        await redis.delete(limiter._global_key(identity['user_id']), limiter._conversation_key(identity['user_id'], None))
    await redis.aclose()
    raw.pool.close()


async def eventually(predicate, seconds=3):
    async with asyncio.timeout(seconds):
        while not await predicate(): await asyncio.sleep(.005)


async def test_two_real_scanners_five_projects_slow_supplier_no_duplicates(page_db, runtime, monkeypatch):
    identities = [runtime.accept() for _ in range(5)]
    first, second = runtime.worker(), runtime.worker()
    gate, entered = asyncio.Event(), asyncio.Event()
    calls = 0
    async def generate(**kwargs):
        nonlocal calls
        calls += 1
        index = calls
        if index == 1:
            entered.set()
            await gate.wait()
        return ImageGenerateResult(task_id=f'external-{index}', status=TaskStatus.PENDING)
    monkeypatch.setattr('services.adapters.factory.create_image_adapter',
        lambda *args, **kwargs:NS(generate=generate, close=AsyncMock()))
    await asyncio.gather(first.scan(), second.scan())
    await asyncio.wait_for(entered.wait(), 3)
    assert len(first._work.jobs) <= 2 and len(second._work.jobs) <= 2
    async def others_progress():
        rows = [await first.refresh(identity) for identity in identities]
        return sum(row['request_params']['_media_lifecycle_v1']['phase']=='accepted' for row in rows) >= 1
    await eventually(others_progress)
    # More scans do not dispatch the same active local task or duplicate the provider claim.
    await asyncio.gather(first.scan(), second.scan())
    gate.set()
    for _ in range(12):
        await asyncio.gather(*list(first._work.jobs.values()), *list(second._work.jobs.values()))
        await asyncio.gather(first.scan(), second.scan())
        rows = [await first.refresh(identity) for identity in identities]
        if all(row['request_params']['_media_lifecycle_v1']['phase']=='accepted' for row in rows): break
    assert all(row['request_params']['_media_lifecycle_v1']['phase']=='accepted' for row in rows)
    assert calls == 5
    with psycopg.connect(page_db) as db:
        for identity in identities:
            assert db.execute('SELECT count(*) FROM credit_transactions WHERE task_id=%s', (identity['id'],)).fetchone()[0] == 1


@pytest.mark.parametrize('interruption', ['shutdown', 'timeout'])
async def test_scanner_interruption_after_dispatch_recovers_without_resubmission(page_db, runtime, monkeypatch, interruption):
    identity = runtime.accept()
    worker = runtime.worker()
    sent = asyncio.Event()
    async def generate(**kwargs):
        sent.set()
        await asyncio.Event().wait()
    adapter = NS(generate=AsyncMock(side_effect=generate), close=AsyncMock())
    monkeypatch.setattr('services.adapters.factory.create_image_adapter', lambda *args, **kwargs:adapter)
    if interruption == 'timeout': worker._work.timeout = .05
    async def submission_started():
        await worker.scan()
        return sent.is_set()
    await eventually(submission_started)
    if interruption == 'shutdown':
        await worker.close()
    else:
        await asyncio.gather(*list(worker._work.jobs.values()))
    adapter.close.assert_awaited_once()
    restarted = runtime.worker()
    with psycopg.connect(page_db) as db:
        db.execute("UPDATE tasks SET request_params=jsonb_set(request_params,'{_media_lifecycle_v1,lease_expires_at}',"
            "to_jsonb(now()-interval '1 second')) WHERE id=%s", (identity['id'],))
    async def uncertain():
        await restarted.scan()
        await asyncio.gather(*list(restarted._work.jobs.values()))
        return (await restarted.refresh(identity))['request_params']['_media_lifecycle_v1']['phase']=='uncertain'
    await eventually(uncertain)
    adapter.generate.assert_awaited_once()
    with psycopg.connect(page_db) as db:
        db.execute("UPDATE tasks SET request_params=jsonb_set(request_params,'{_media_lifecycle_v1,uncertain_deadline}',"
            "to_jsonb(now()-interval '1 second')) WHERE id=%s", (identity['id'],))
    async def published():
        await restarted.scan()
        await asyncio.gather(*list(restarted._work.jobs.values()))
        return (await restarted.refresh(identity))['request_params']['_media_lifecycle_v1']['phase']=='published'
    await eventually(published)
    row = await restarted.refresh(identity)
    assert row['status']=='failed' and row['request_params']['_media_lifecycle_v1']['phase']=='published'
    adapter.generate.assert_awaited_once()
    with psycopg.connect(page_db) as db:
        assert db.execute('SELECT credits FROM users WHERE id=%s', (identity['user_id'],)).fetchone()[0]==1000


async def test_combined_group_acceptance_converges_under_concurrent_maintenance(page_db, runtime):
    from uuid import uuid4
    from PIL import Image
    from psycopg.types.json import Jsonb
    from services.detail_page_generation import DetailPageGeneration, PageImageInputResolver, page_owner
    from services.background_jobs import FairBackgroundJobs
    from tests.test_detail_page_generation_postgres import seed, snapshot
    user, project, run, _ = seed(page_db, combined=True)
    service = DetailPageGeneration(runtime.raw, user, None)
    target = service.projects.executor.resolve_safe_path('concurrent-product.png')
    target.parent.mkdir(parents=True, exist_ok=True)
    Image.new('RGB', (16, 16), 'blue').save(target)
    refs = PageImageInputResolver(page_owner(service.db, user, None, project)).bind([
        {'workspace_path':'concurrent-product.png', 'category':'product'}])
    with psycopg.connect(page_db) as db:
        plan_ids = [str(row[0]) for row in db.execute('SELECT id FROM ecom_image_plans WHERE project_id=%s', (project,))]
        for plan_id in plan_ids:
            items = []
            for position in range(1, 8):
                item = str(uuid4())
                frozen = snapshot(user, project, run, plan_id, item)
                items.append({'item_id':item, 'position':position, 'request_text':'exact',
                    'request_text_sha256':frozen['prompt_sha256'], 'aspect_ratio':'1:1'})
            db.execute("UPDATE ecom_image_plans SET status='ready',items=%s,input_snapshot=%s,model_settings=%s WHERE id=%s",
                (Jsonb(items), Jsonb({'resolved_references':refs}),
                 Jsonb({'image_budget':{'max_requests':15,'max_credits':300}}), plan_id))
    plans = service.db.table('ecom_image_plans').select('*').eq('project_id', project).execute().data
    pools = [FairBackgroundJobs('test-group', 2), FairBackgroundJobs('test-group', 2)]
    try:
        for pool in pools:
            pool.dispatch([(plan['id'], user, project, lambda plan=plan:service.accept(plan)) for plan in plans])
        await asyncio.gather(*(job for pool in pools for job in list(pool.jobs.values())))
        with psycopg.connect(page_db) as db:
            tasks = db.execute("SELECT request_params->'_media_request_v1'->'origin'->'plan_source'->>'item_id' "
                "FROM tasks WHERE request_params->'_media_request_v1'->'origin'->>'project_id'=%s", (project,)).fetchall()
            assert len(tasks)==14 and len(set(tasks))==14
            assert db.execute('SELECT credits FROM users WHERE id=%s', (user,)).fetchone()[0]==1000
    finally:
        for pool in pools: await pool.close()
