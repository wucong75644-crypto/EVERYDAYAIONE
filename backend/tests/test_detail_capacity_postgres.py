"""Ten products / 150 images: real spawned workers, Redis quota and credit ledger."""
from uuid import uuid4
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

import psycopg
from psycopg.types.json import Jsonb
import pytest

from core.db_scope import DatabaseScope, DatabaseAccessKind, ScopedDatabaseClient
from services.adapters.base import ImageGenerateResult, TaskStatus
from services.task_completion_service import TaskCompletionService
from tests.test_detail_concurrency_postgres import runtime
from tests.test_detail_multiprocess_postgres import run_process_pair
from tests.test_chat_image_lifecycle_postgres import isolated_db
from tests.test_ecommerce_recovery_postgres import ecom_db
from tests.test_detail_page_generation_postgres import page_db, scoped, snapshot
from tests.test_detail_page_delivery_recovery import delivery_db
from tests.test_detail_project_tasks import multi_db
from tests.test_detail_generation_history import history_db


@pytest.fixture(scope='module',autouse=True)
def latest_capacity_schema(history_db):
    with psycopg.connect(history_db) as db:
        db.execute((Path(__file__).parents[1]/'migrations/299_chat_image_retry_in_place.sql').read_text())


def seed_product(dsn, user, count=15):
    project,run,plan = [str(uuid4()) for _ in range(3)]
    with psycopg.connect(dsn) as db:
        db.execute("INSERT INTO detail_projects(id,user_id,content_type,image_count,prompt_model) VALUES(%s,%s,'main_image',%s,'kimi-k3')",
            (project,user,count))
    entry = {'id':plan,'project_id':project,'generation_run_id':run,'user_id':user,'org_id':None,
        'invocation_key':'main_images','input_digest':'a'*64,'image_count':count,
        'input_snapshot':{'resolved_references':[]},'target_size':{'aspect_ratio':'1:1','resolution':'1K'},
        'prompt_versions':{},'model_settings':{'model':'kimi-k3'}}
    with scoped(dsn,user) as db:
        db.execute('SELECT start_detail_page_run(%s,1,%s,%s)',(project,run,Jsonb([entry])))
    items,snapshots = [],[]
    for position in range(count):
        item = str(uuid4())
        frozen = snapshot(user,project,run,plan,item)
        items.append({'item_id':item,'position':position+1,'request_text':'exact',
            'request_text_sha256':frozen['prompt_sha256'],'aspect_ratio':'1:1'})
        snapshots.append((item,frozen))
    with psycopg.connect(dsn) as db:
        db.execute("UPDATE ecom_image_plans SET status='ready',items=%s WHERE id=%s",(Jsonb(items),plan))
    identities = []
    for item,frozen in snapshots:
        with scoped(dsn,user) as db:
            task = db.execute('SELECT accept_detail_page_image(%s,%s,%s,%s)',
                (project,plan,item,Jsonb(frozen))).fetchone()[0]['task_id']
        identities.append({'id':task,'user_id':user,'org_id':None})
    return project,identities


@pytest.mark.parametrize("release_outage", [False, True])
async def test_ten_products_150_images_two_processes_and_full_quota_recovery(page_db,runtime,monkeypatch,release_outage):
    user = str(uuid4())
    with psycopg.connect(page_db) as db: db.execute('INSERT INTO users(id,credits) VALUES(%s,10000)',(user,))
    projects,identities = [],[]
    for _ in range(10):
        project,tasks = seed_product(page_db,user)
        projects.append(project);identities.extend(tasks)
    limiter = runtime.limiter
    try:
        assert len(projects)==10 and len(identities)==150
        assert await run_process_pair(page_db,runtime,identities,['scan','scan'],'capacity')==150
        worker = runtime.worker()
        rows = [await worker.refresh(identity) for identity in identities]
        assert all(row['request_params']['_media_lifecycle_v1']['phase']=='accepted' for row in rows)
        assert await limiter.redis.scard(limiter._global_key(user))==150
        with psycopg.connect(page_db) as db:
            cost = db.execute('SELECT sum(amount) FROM credit_transactions WHERE user_id=%s',(user,)).fetchone()[0]
            assert db.execute('SELECT count(*) FROM credit_transactions WHERE user_id=%s',(user,)).fetchone()[0]==150
            assert db.execute('SELECT credits FROM users WHERE id=%s',(user,)).fetchone()[0]==10000-cost
        # The 151st waits before provider IO and before any credit transaction.
        _,extra = seed_product(page_db,user,1)
        adapter = NS(generate=AsyncMock(return_value=ImageGenerateResult(task_id=str(uuid4()),status=TaskStatus.PENDING)),close=AsyncMock())
        monkeypatch.setattr('services.adapters.factory.create_image_adapter',lambda *a,**kw:adapter)
        await worker.advance(await worker.refresh(extra[0]))
        adapter.generate.assert_not_awaited()
        with psycopg.connect(page_db) as db:
            assert db.execute('SELECT count(*) FROM credit_transactions WHERE task_id=%s',(extra[0]['id'],)).fetchone()[0]==0
        # A definite failure/duplicate result frees exactly one slot and refunds once.
        first = rows[0]
        failure = ImageGenerateResult(task_id=first['external_task_id'],status=TaskStatus.FAILED,fail_msg='isolated failure')
        callback = TaskCompletionService(ScopedDatabaseClient(runtime.raw,DatabaseScope(user,None,DatabaseAccessKind.WORKER)))
        if release_outage:
            with patch.object(limiter.redis, 'pipeline', side_effect=ConnectionError('isolated release outage')):
                with pytest.raises(ConnectionError):
                    await callback.process_result(first['external_task_id'],failure)
            terminal = await worker.refresh(first)
            assert terminal['request_params']['_media_lifecycle_v1']['delivery_pending'] is True
            assert await limiter.redis.scard(limiter._global_key(user))==150
            await worker.advance(await worker.refresh(extra[0]))
            adapter.generate.assert_not_awaited()
            # Restarted recovery retries only release/ack, never provider IO or the refund.
            restarted = runtime.worker()
            await restarted.advance(terminal)
        else:
            assert await callback.process_result(first['external_task_id'],failure)
        assert await callback.process_result(first['external_task_id'],failure)
        assert await limiter.redis.scard(limiter._global_key(user))==149
        await worker.advance(await worker.refresh(extra[0]))
        adapter.generate.assert_awaited_once()
        await worker.advance(await worker.refresh(extra[0]))
        adapter.generate.assert_awaited_once()
        assert await limiter.redis.scard(limiter._global_key(user))==150
        with psycopg.connect(page_db) as db:
            ledger = db.execute('SELECT status,count(*),sum(amount) FROM credit_transactions WHERE user_id=%s GROUP BY status',(user,)).fetchall()
            states = {status:(count,amount) for status,count,amount in ledger}
            assert states['refunded']==(1,first['credits_locked'])
            assert states['pending'][0]==150
            assert db.execute('SELECT count(*),sum(change_amount) FROM credits_history WHERE user_id=%s',(user,)).fetchone()==(1,first['credits_locked'])
            assert db.execute('SELECT credits FROM users WHERE id=%s',(user,)).fetchone()[0]==10000-states['pending'][1]
    finally:
        await limiter.redis.delete(limiter._global_key(user),limiter._conversation_key(user,None))
