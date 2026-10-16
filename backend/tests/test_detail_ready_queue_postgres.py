"""A blocked group must not strand a ready sibling behind busy maintenance slots."""
import asyncio
import time
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
import pytest
from PIL import Image

from core.local_db import LocalDBClient
from core.db_scope import DatabaseScope, DatabaseAccessKind, ScopedDatabaseClient
from services.detail_page_generation import DetailPageGeneration, DetailPageWorker, PageImageInputResolver, page_owner
from services.detail_page_recovery import POLICY
from tests.test_chat_image_lifecycle_postgres import isolated_db
from tests.test_ecommerce_recovery_postgres import ecom_db
from tests.test_detail_page_generation_postgres import page_db, seed, scoped, snapshot
from tests.test_detail_page_delivery_recovery import delivery_db, settle


@pytest.fixture
def ready_sibling(delivery_db):
    user, project, run, _ = seed(delivery_db, combined=True)
    raw = LocalDBClient(psycopg.conninfo.make_conninfo(delivery_db, user='everydayai'), min_size=1, max_size=4)
    service = DetailPageGeneration(raw, user, None)
    path = service.projects.executor.resolve_safe_path('queued-ready-product.png')
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new('RGB', (16, 16), 'blue').save(path)
    refs = PageImageInputResolver(page_owner(service.db, user, None, project)).bind([
        {'workspace_path': 'queued-ready-product.png', 'category': 'product'}])
    with psycopg.connect(delivery_db) as db:
        rows = db.execute('SELECT id,invocation_key FROM ecom_image_plans WHERE project_id=%s', (project,)).fetchall()
        for plan, kind in rows:
            plan = str(plan)
            if kind == 'main_images':
                blocked = plan
                db.execute("UPDATE ecom_image_plans SET status='failed',model_settings=model_settings||%s,recovery_state=%s WHERE id=%s",
                    (Jsonb({'delivery_policy': POLICY}), Jsonb({'last_error': {'code': 'PLANNER_DELIVERY_REPAIR_EXHAUSTED'}}), plan))
            else:
                ready = plan
                items = []
                for position in range(1, 8):
                    item = str(uuid4())
                    frozen = snapshot(user, project, run, plan, item)
                    items.append({'item_id': item, 'position': position, 'request_text': 'exact',
                        'request_text_sha256': frozen['prompt_sha256'], 'aspect_ratio': '1:1'})
                db.execute("UPDATE ecom_image_plans SET status='ready',items=%s,input_snapshot=%s,model_settings=%s WHERE id=%s",
                    (Jsonb(items), Jsonb({'task_type': kind, 'resolved_references': refs}),
                     Jsonb({'delivery_policy': POLICY, 'image_budget': {'max_requests': 15, 'max_credits': 300}}), plan))
    yield service, project, run, blocked, ready
    raw.pool.close()


def scanned(service, project):
    worker_db = ScopedDatabaseClient(service.db._client, DatabaseScope(None, None, DatabaseAccessKind.WORKER))
    rows = worker_db.rpc('scan_detail_page_work', {'p_org_id': None, 'p_limit': 100}).execute().data
    return any(row['id'] == project for row in rows)


async def test_twenty_busy_maintenance_slots_preserve_and_later_accept_ready_sibling(ready_sibling, delivery_db):
    service, project, run, blocked, ready = ready_sibling
    worker = DetailPageWorker(service.db._client)
    assert worker._maintenance.concurrency == 20
    gate, entered = asyncio.Event(), asyncio.Event()
    busy = 0
    async def hold():
        nonlocal busy
        busy += 1
        if busy == 19:
            entered.set()
        await gate.wait()
    # Nineteen ongoing jobs leave one opportunity for the blocked sibling.
    worker._maintenance.dispatch([(f'busy-{i}', ('busy', i), i, hold) for i in range(19)])
    await asyncio.wait_for(entered.wait(), 2)
    # Deterministically schedule the blocked group first, irrespective of DB row order.
    worker._maintenance._item_turn[ready] = (1000, time.monotonic())
    worker._maintenance._owner_turn[(None, service.user_id)] = (-1, time.monotonic())
    try:
        await worker.scan()
        assert len(worker._maintenance.jobs) == 20
        assert ready not in worker._maintenance.jobs and blocked in worker._maintenance.jobs
        await worker._maintenance.jobs[blocked]
        await asyncio.sleep(0)
        assert service.projects.get_by_id(project)['status'] == 'plan_ready'
        assert scanned(service, project)
        assert not service.db.table('tasks').select('id').eq('user_id', service.user_id).execute().data
        gate.set()
        await asyncio.gather(*list(worker._maintenance.jobs.values()))
        # Subsequent actual scans/accept RPCs, not a mocked success, create each image once.
        for _ in range(3):
            await worker.scan()
            await asyncio.gather(*list(worker._maintenance.jobs.values()))
            await asyncio.sleep(0)
        tasks = service.db.table('tasks').select('*').eq('user_id', service.user_id).execute().data
        assert len(tasks) == 7
        assert len({task['request_params']['_media_request_v1']['origin']['plan_source']['item_id'] for task in tasks}) == 7
        assert service.projects.get_by_id(project)['status'] == 'generating'
        with psycopg.connect(delivery_db) as db:
            assert db.execute('SELECT count(*) FROM credit_transactions WHERE user_id=%s', (service.user_id,)).fetchone()[0] == 0
        for task in tasks:
            settle(delivery_db, service.user_id, task['id'], success=True)
        plans = service.db.table('ecom_image_plans').select('*').eq('project_id', project).execute().data
        DetailPageWorker.aggregate(service, service.projects.get_by_id(project), plans)
        assert service.projects.get_by_id(project)['status'] == 'failed'
        assert not scanned(service, project)
    finally:
        gate.set()
        await worker.close()


@pytest.mark.parametrize('accepted', [0, 3, 7])
def test_pending_ready_items_are_distinguished_from_settled_items(accepted):
    # Item coverage is per plan; terminal tasks from another group cannot hide missing items.
    project = {'run_state': {}, 'title': 'product'}
    blocked = {'id': 'blocked', 'status': 'needs_input', 'image_count': 7, 'model_settings': {}}
    ready = {'id': 'ready', 'status': 'ready', 'image_count': 7,
        'items': [{'item_id': str(i)} for i in range(7)], 'model_settings': {}}
    tasks = [{'id': str(i), 'created_at': str(i), 'status': 'completed', 'request_params': {
        '_media_request_v1': {'origin': {'plan_source': {'plan_id': 'ready', 'item_id': str(i)}}}}}
        for i in range(accepted)]
    status = DetailPageWorker._aggregate_updates(project, [blocked, ready], tasks)['status']
    assert status == ('failed' if accepted == 7 else 'generating' if accepted else 'plan_ready')
