"""Scheduling regressions: slow/failing work must not block unrelated projects."""
import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest

from services.background_jobs import FairBackgroundJobs
from services.handlers.chat_image_lifecycle import ChatImageLifecycle
from services.handlers.chat_image_request import REQUEST_KEY
from services.detail_page_generation import DetailPageWorker
from services.background_task_worker import BackgroundTaskWorker


async def drain(pool):
    await asyncio.gather(*list(pool.jobs.values()))
    await asyncio.sleep(0)


def candidate(key, operation, owner='owner', project='project'):
    return key, owner, project, operation


async def test_slow_job_does_not_block_dispatch_capacity_dedup_and_recovery():
    pool = FairBackgroundJobs('test', 2)
    slow, other_started = asyncio.Event(), asyncio.Event()
    calls = []
    async def first():
        calls.append('slow')
        await slow.wait()
    async def other():
        calls.append('other')
        other_started.set()
    candidates = [candidate('slow', first), candidate('other', other), candidate('third', other)]
    try:
        pool.dispatch(candidates)
        pool.dispatch(candidates)
        assert len(pool.jobs) == 2
        await asyncio.wait_for(other_started.wait(), 1)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert calls == ['slow', 'other']
        pool.dispatch([candidates[0], candidates[2]])
        await asyncio.sleep(0)
        assert calls == ['slow', 'other', 'other']
        slow.set()
        await drain(pool)
        assert not pool.jobs
    finally:
        await pool.close()


async def test_round_robin_owners_and_projects_survives_single_slot_scans():
    pool = FairBackgroundJobs('test', 1)
    seen = []
    def work(key, owner, project):
        async def run(): seen.append(key)
        return candidate(key, run, owner, project)
    waiting = [work('A1', 'A', 'P1'), work('A2', 'A', 'P1'), work('A3', 'A', 'P2'),
               work('B1', 'B', 'P3'), work('B2', 'B', 'P3')]
    for _ in range(5):
        pool.dispatch(waiting)
        await drain(pool)
        waiting = [item for item in waiting if item[0] not in seen]
    assert seen == ['A1', 'B1', 'A3', 'B2', 'A2']
    await pool.close()


async def test_timeout_failure_and_close_release_local_capacity():
    pool = FairBackgroundJobs('test', 1, timeout=.02)
    cancelled = asyncio.Event()
    async def hangs():
        try: await asyncio.Event().wait()
        finally: cancelled.set()
    pool.dispatch([candidate('hang', hangs)])
    await asyncio.wait_for(drain(pool), 1)
    assert cancelled.is_set() and not pool.jobs
    async def fails(): raise RuntimeError('simulated')
    pool.dispatch([candidate('fail', fails)])
    await drain(pool)
    assert not pool.jobs
    pool.dispatch([candidate('closing', hangs)])
    await asyncio.sleep(0)
    await pool.close()
    assert not pool.jobs
    pool.dispatch([candidate('closed', hangs)])
    assert not pool.jobs


class Query:
    def __init__(self, execute): self._execute = execute
    def select(self, *args): return self
    def eq(self, *args): return self
    def update(self, *args): return self
    def execute(self): return NS(data=self._execute())


async def test_image_scan_is_nonblocking_and_isolates_broken_org(monkeypatch):
    import services.handlers.chat_image_lifecycle as module
    task = {'id':'healthy', 'user_id':'user', 'created_at':'2026-10-16', 'request_params':{
        REQUEST_KEY:{'origin':{'project_id':'project'}}}}
    db = NS(table=lambda _: Query(lambda:[{'id':'broken'}, {'id':'healthy-org'}]))
    def scoped(_db, scope):
        def execute():
            if scope.org_id == 'broken': raise RuntimeError('database read failed')
            return [task] if scope.org_id == 'healthy-org' else []
        return NS(rpc=lambda *_:Query(execute))
    monkeypatch.setattr(module, 'ScopedDatabaseClient', scoped)
    monkeypatch.setattr(module, 'DatabaseScope', lambda user, org, kind:NS(org_id=org))
    lifecycle = ChatImageLifecycle(db, NS(chat_image_processing_concurrency=2, chat_image_processing_timeout_seconds=5))
    gate, entered = asyncio.Event(), asyncio.Event()
    lifecycle.refresh = AsyncMock(return_value=task)
    async def advance(_):
        entered.set()
        await gate.wait()
    lifecycle.advance = AsyncMock(side_effect=advance)
    try:
        await asyncio.wait_for(lifecycle.scan(), 1)
        await asyncio.wait_for(entered.wait(), 1)
        await asyncio.wait_for(lifecycle.scan(), 1)
        assert lifecycle.advance.await_count == 1
        gate.set()
        await drain(lifecycle._work)
    finally: await lifecycle.close()


async def test_independent_loops_keep_planning_running_when_image_scan_hangs():
    worker = BackgroundTaskWorker.__new__(BackgroundTaskWorker)
    worker.is_running = True
    entered, progressed = asyncio.Event(), asyncio.Event()
    async def hangs():
        entered.set()
        await asyncio.Event().wait()
    async def progresses(): progressed.set()
    worker._chat_images = NS(scan=hangs)
    worker._detail_pages = NS(scan=progresses)
    tasks = [asyncio.create_task(worker._chat_image_loop()), asyncio.create_task(worker._detail_page_loop())]
    try:
        await asyncio.wait_for(entered.wait(), 1)
        await asyncio.wait_for(progressed.wait(), 1)
    finally:
        worker.is_running = False
        for task in tasks: task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def test_detail_scan_isolates_org_project_and_slow_acceptance(monkeypatch):
    import services.detail_page_generation as module
    settings = NS(detail_page_planning_concurrency=2, detail_page_maintenance_concurrency=2,
        detail_page_maintenance_timeout_seconds=5)
    monkeypatch.setattr(module, 'get_settings', lambda:settings)
    projects = [{'id':key, 'user_id':key, 'run_state':{'run_id':'run'}} for key in ['bad-project','slow','good']]
    def scoped(db, scope):
        def execute():
            if scope.org_id is None: raise RuntimeError('broken org')
            return projects
        return NS(rpc=lambda *_:Query(execute))
    monkeypatch.setattr(module, 'ScopedDatabaseClient', scoped)
    monkeypatch.setattr(module, 'DatabaseScope', lambda user, org, kind:NS(org_id=org))
    db = NS(table=lambda _:Query(lambda:[{'id':'org'}]))
    gate, slow_started, good_started, planned = [asyncio.Event() for _ in range(4)]
    rotated = []
    def service(_db, user, org):
        def table(name):
            def execute():
                if name == 'ecom_image_plans':
                    if user == 'bad-project': raise RuntimeError('bad project')
                    return [{'id':user, 'generation_run_id':'run','status':'planning' if user=='good' else 'ready'}]
                if name == 'detail_projects': rotated.append(user)
                return []
            return Query(execute)
        async def accept(plan):
            slow_started.set()
            await gate.wait()
        return NS(db=NS(table=table), user_id=user, org_id=org, accept=accept)
    monkeypatch.setattr(module, 'DetailPageGeneration', service)
    monkeypatch.setattr(module, 'DetailDeliveryRecovery', lambda _:NS(advance=AsyncMock()))
    worker = DetailPageWorker(db)
    async def run(*_): planned.set()
    worker.run = run
    def aggregate(service, *_):
        if service.user_id=='good': good_started.set()
    worker.aggregate = aggregate
    try:
        await asyncio.wait_for(worker.scan(), 1)
        await asyncio.wait_for(slow_started.wait(), 1)
        await asyncio.wait_for(planned.wait(), 1)
        await asyncio.wait_for(good_started.wait(), 1)
        assert set(rotated) == {'bad-project','slow','good'}
        gate.set()
        await drain(worker._maintenance)
    finally: await worker.close()


async def test_shutdown_cancels_both_scans_and_closes_both_pools():
    worker = BackgroundTaskWorker.__new__(BackgroundTaskWorker)
    worker.is_running = True
    started = asyncio.Event()
    async def hangs():
        started.set()
        await asyncio.Event().wait()
    worker._chat_image_loop_task = asyncio.create_task(hangs())
    worker._detail_page_loop_task = asyncio.create_task(hangs())
    worker._social_publish_loop_task = None
    worker._social_ops = NS(stop=AsyncMock())
    worker._chat_images = NS(close=AsyncMock())
    worker._detail_pages = NS(close=AsyncMock())
    await started.wait()
    await worker.stop()
    worker._chat_images.close.assert_awaited_once()
    worker._detail_pages.close.assert_awaited_once()
    assert worker._chat_image_loop_task is worker._detail_page_loop_task is None


async def test_main_and_detail_groups_of_same_project_do_not_block_each_other(monkeypatch):
    import services.detail_page_generation as module
    monkeypatch.setattr(module, 'get_settings', lambda:NS(detail_page_planning_concurrency=2,
        detail_page_maintenance_concurrency=2, detail_page_maintenance_timeout_seconds=5))
    monkeypatch.setattr(module, 'DatabaseScope', lambda user, org, kind:NS(org_id=org))
    project = {'id':'combined', 'user_id':'owner', 'run_state':{'run_id':'run'}}
    plans = [{'id':kind,'generation_run_id':'run','status':'ready'} for kind in ['main_images','detail_page']]
    db = NS(table=lambda _:Query(lambda:[]))
    monkeypatch.setattr(module, 'ScopedDatabaseClient', lambda *args:NS(rpc=lambda *_:Query(lambda:[project])))
    main_started, detail_started, gate = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def accept(plan):
        if plan['id']=='main_images':
            main_started.set()
            await gate.wait()
        else: detail_started.set()
    def table(name): return Query(lambda:plans if name=='ecom_image_plans' else [])
    monkeypatch.setattr(module, 'DetailPageGeneration', lambda *args:NS(db=NS(table=table),accept=accept))
    monkeypatch.setattr(module, 'DetailDeliveryRecovery', lambda _:NS(advance=AsyncMock()))
    worker = DetailPageWorker(db)
    worker.aggregate = Mock()
    try:
        await worker.scan()
        await asyncio.wait_for(main_started.wait(), 1)
        await asyncio.wait_for(detail_started.wait(), 1)
        assert len(worker._maintenance.jobs) <= 2
        gate.set()
        await drain(worker._maintenance)
        assert worker.aggregate.call_count == 2
    finally: await worker.close()


@pytest.mark.parametrize('concurrency,owners,projects,items', [(1,1,1,7),(4,4,1,2),(4,1,5,2)])
async def test_repeated_candidates_rotate_inside_every_project(concurrency, owners, projects, items):
    pool = FairBackgroundJobs('rounds', concurrency)
    seen = []
    def work(owner, project, item):
        key = (owner, project, item)
        async def run(): seen.append(key)
        return candidate(key, run, owner, project)
    # Accepted/no-op items intentionally stay eligible on every subsequent scan.
    candidates = [work(owner, project, item) for owner in range(owners)
                  for project in range(projects) for item in range(items)]
    try:
        for _ in range(len(candidates) * 2):
            pool.dispatch(candidates)
            await drain(pool)
            if len(set(seen)) == len(candidates): break
        assert set(seen) == {item[0] for item in candidates}
        assert not pool.jobs
    finally: await pool.close()


async def test_image_multi_round_scan_submits_all_seven_before_any_callback(monkeypatch):
    from datetime import datetime, timezone
    from services.handlers.chat_image_request import LIFECYCLE_KEY
    import services.handlers.chat_image_lifecycle as module
    tasks = [{'id':str(index), 'created_at':f'2026-10-16T00:00:0{index}Z', 'user_id':'owner',
        'request_params':{REQUEST_KEY:{'origin':{'project_id':'product','destination':'detail_project'}},LIFECYCLE_KEY:{'phase':'queued'}}}
        for index in range(7)]
    db = NS(table=lambda _:Query(lambda:[]), rpc=lambda _name, params:Query(
        lambda:tasks if params['p_after_id'] is None else []))
    monkeypatch.setattr(module, 'ScopedDatabaseClient', lambda db, scope:db)
    worker = ChatImageLifecycle(db, NS(chat_image_processing_concurrency=4,
        chat_image_processing_timeout_seconds=5,chat_image_queue_timeout_seconds=600))
    worker.refresh = AsyncMock(side_effect=lambda task:task)
    async def submit(task):
        task['request_params'][LIFECYCLE_KEY] = {'phase':'accepted', 'accepted_at':datetime.now(timezone.utc).isoformat()}
    worker.submit = AsyncMock(side_effect=submit)
    try:
        for _ in range(10):
            await worker.scan()
            await drain(worker._work)
        assert worker.submit.await_count == 7
        assert all(task['request_params'][LIFECYCLE_KEY]['phase']=='accepted' for task in tasks)
    finally: await worker.close()


async def test_detail_multi_round_scan_reaches_both_groups_for_four_owners(monkeypatch):
    import services.detail_page_generation as module
    monkeypatch.setattr(module, 'get_settings', lambda:NS(detail_page_planning_concurrency=4,
        detail_page_maintenance_concurrency=4, detail_page_maintenance_timeout_seconds=5))
    monkeypatch.setattr(module, 'DatabaseScope', lambda user, org, kind:NS(org_id=org))
    projects = [{'id':f'project-{i}', 'user_id':f'user-{i}', 'run_state':{'run_id':'run'}} for i in range(4)]
    db = NS(table=lambda _:Query(lambda:[]))
    monkeypatch.setattr(module, 'ScopedDatabaseClient', lambda *_:NS(rpc=lambda *_:Query(lambda:projects)))
    seen = set()
    def service(_db, user, org):
        plans = [{'id':f'{user}-{kind}','generation_run_id':'run','status':'ready'} for kind in ['main','detail']]
        def table(name): return Query(lambda:plans if name=='ecom_image_plans' else [])
        async def accept(plan): seen.add(plan['id'])
        return NS(db=NS(table=table),accept=accept)
    monkeypatch.setattr(module, 'DetailPageGeneration', service)
    monkeypatch.setattr(module, 'DetailDeliveryRecovery', lambda _:NS(advance=AsyncMock()))
    worker = DetailPageWorker(db)
    worker.aggregate = Mock()
    try:
        for _ in range(5):
            await worker.scan()
            await drain(worker._maintenance)
        assert seen == {f'user-{i}-{kind}' for i in range(4) for kind in ['main','detail']}
    finally: await worker.close()


@pytest.mark.parametrize('level', ['items','projects','owners'])
async def test_rotation_survives_paginated_discovery(level):
    pool = FairBackgroundJobs('pages', 2)
    seen = set()
    def work(key):
        async def run(): seen.add(key)
        return candidate(key, run, owner=key if level=='owners' else 'owner',
            project=key if level=='projects' else 'project')
    pages = [[work(key) for key in range(start,start+7)] for start in [0,7]]
    try:
        for _ in range(8):
            for page in pages:
                pool.dispatch(page)
                await drain(pool)
        assert seen == set(range(14))
    finally: await pool.close()


async def test_retired_turn_history_is_pruned_but_active_jobs_are_kept():
    import time
    pool = FairBackgroundJobs('history', 1)
    gate = asyncio.Event()
    async def run(): await gate.wait()
    pool.dispatch([candidate('active', run)])
    pool._item_turn['retired'] = (1, time.monotonic()-4000)
    pool._item_turn['active'] = (1, time.monotonic()-4000)
    pool._next_prune = 0
    try:
        pool.dispatch([])
        assert 'retired' not in pool._item_turn
        assert 'active' in pool._item_turn
    finally:
        gate.set()
        await pool.close()


async def test_default_capacity_dispatches_twenty_groups_for_ten_products(monkeypatch):
    from core.config import Settings
    import services.detail_page_generation as module
    settings = Settings(_env_file=None,database_url='postgresql://invalid/test',jwt_secret_key='isolated-test-key')
    assert settings.detail_page_planning_concurrency==20
    assert settings.detail_page_maintenance_concurrency==20
    assert settings.chat_image_processing_concurrency==20
    assert settings.detail_page_image_concurrency==150
    assert settings.chat_image_max_requests==15
    monkeypatch.setattr(module,'get_settings',lambda:settings)
    monkeypatch.setattr(module,'DatabaseScope',lambda user,org,kind:NS(org_id=org))
    projects = [{'id':f'p-{i}','user_id':'same-user','run_state':{'run_id':f'run-{i}'}} for i in range(10)]
    db = NS(table=lambda _:Query(lambda:[]))
    monkeypatch.setattr(module,'ScopedDatabaseClient',lambda *_:NS(rpc=lambda *_:Query(lambda:projects)))
    class ProjectQuery(Query):
        def eq(self,_key,value): self.project=value; return self
        def execute(self):
            return NS(data=[{'id':f'{self.project}-{kind}','generation_run_id':f"run-{self.project.split('-')[1]}",
                'status':'planning'} for kind in ['main','detail']])
    def service(*_):
        def table(name): return ProjectQuery(lambda:[]) if name=='ecom_image_plans' else Query(lambda:[])
        return NS(db=NS(table=table))
    monkeypatch.setattr(module,'DetailPageGeneration',service)
    monkeypatch.setattr(module,'DetailDeliveryRecovery',lambda _:NS(advance=AsyncMock()))
    worker = DetailPageWorker(db)
    worker.aggregate = Mock()
    entered,gate,seen = asyncio.Event(),asyncio.Event(),set()
    async def run(_service,plan):
        seen.add(plan['id'])
        if len(seen)==20: entered.set()
        await gate.wait()
    worker.run=run
    try:
        await worker.scan()
        await asyncio.wait_for(entered.wait(),1)
        assert len(worker.jobs)==20 and len(seen)==20
    finally:
        gate.set()
        await worker.close()
