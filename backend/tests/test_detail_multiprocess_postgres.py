"""Fresh OS processes race the real scanner, webhook, Redis and credit ledger."""
import asyncio
import os
from pathlib import Path

import psycopg
import pytest

from tests.test_detail_concurrency_postgres import runtime
from tests.test_chat_image_lifecycle_postgres import isolated_db
from tests.test_ecommerce_recovery_postgres import ecom_db
from tests.test_detail_page_generation_postgres import page_db


@pytest.fixture(scope='module', autouse=True)
def latest_publication(page_db):
    # Page migration 283 replaces publication; restore the latest deployed
    # contract so process races cover current settlement/late-result guards.
    with psycopg.connect(page_db) as db:
        db.execute((Path(__file__).parents[1]/'migrations/299_chat_image_retry_in_place.sql').read_text())


async def run_process_pair(page_db, runtime, identities, roles, order):
    import multiprocessing
    from tests.detail_process_worker import run_worker_process
    ctx = multiprocessing.get_context('spawn')
    barrier, entered, action_done, callback_done = ctx.Barrier(2), ctx.Event(), ctx.Event(), ctx.Event()
    calls, reports = ctx.Value('i', 0), ctx.Queue()
    dsn = psycopg.conninfo.make_conninfo(page_db, user='everydayai')
    processes = [ctx.Process(target=run_worker_process, args=(dsn,
        os.environ['CHAT_IMAGE_TEST_REDIS_SOCKET'], runtime.settings.file_workspace_root, identities,
        role, order, barrier, entered, action_done, callback_done, calls, reports)) for role in roles]
    try:
        for process in processes: process.start()
        await asyncio.gather(*(asyncio.to_thread(process.join, 25) for process in processes))
        assert all(not process.is_alive() for process in processes), 'Child worker did not finish'
        result = [reports.get(timeout=2) for _ in processes]
        assert all(item['ok'] for item in result), result
        assert all(process.exitcode==0 for process in processes)
        assert len({item['pid'] for item in result})==2
        assert all(item['pid']!=os.getpid() for item in result)
        return calls.value
    finally:
        for process in processes:
            if process.is_alive(): process.terminate()
        for process in processes:
            if process.pid is not None: await asyncio.to_thread(process.join, 5)
            process.close()
        reports.close()
        reports.join_thread()


async def test_spawned_processes_scan_seven_images_without_callbacks_and_charge_once(page_db, runtime):
    identities = runtime.accept_many(7)
    assert await run_process_pair(page_db, runtime, identities, ['scan','scan'], 'race') == 7
    with psycopg.connect(page_db) as db:
        for identity in identities:
            row = db.execute('SELECT credits_locked,external_task_id FROM tasks WHERE id=%s', (identity['id'],)).fetchone()
            assert row[1].startswith('spawn-')
            assert db.execute('SELECT count(*) FROM credit_transactions WHERE task_id=%s', (identity['id'],)).fetchone()[0]==1
        balance = db.execute('SELECT credits FROM users WHERE id=%s', (identities[0]['user_id'],)).fetchone()[0]
        cost = db.execute('SELECT sum(amount) FROM credit_transactions WHERE user_id=%s', (identities[0]['user_id'],)).fetchone()[0]
        assert balance==1000-cost
        assert db.execute('SELECT count(*) FROM credits_history WHERE user_id=%s', (identities[0]['user_id'],)).fetchone()[0]==0


@pytest.mark.parametrize('phase,order', [
    ('accepted','race'), ('accepted','recovery_first'), ('accepted','callback_first'),
    ('uncertain','race'), ('uncertain','recovery_first'), ('uncertain','callback_first'),
    ('settling','processing_timeout'),
])
async def test_spawned_timeout_and_real_webhook_settle_once(page_db, runtime, phase, order):
    from uuid import uuid4
    from PIL import Image
    from services.file_executor import FileExecutor
    identity = runtime.accept()
    worker = runtime.worker()
    token, external = str(uuid4()), str(uuid4())
    claim = await worker.rpc(identity, 'claim_chat_image_submission', p_claim_token=token, p_lease_seconds=60)
    assert claim['outcome']=='claimed'
    await worker.rpc(identity, 'mark_chat_image_dispatch', p_claim_token=token)
    await worker.rpc(identity, 'bind_chat_image_submission', p_claim_token=token, p_external_task_id=external)
    task = await worker.refresh(identity)
    amount = task['credits_locked']
    if phase=='uncertain':
        await worker.rpc(identity,'mark_chat_image_uncertain',p_claim_token=token,p_timeout_seconds=60)
        with psycopg.connect(page_db) as db:
            db.execute("UPDATE tasks SET request_params=jsonb_set(request_params,'{_media_lifecycle_v1,uncertain_deadline}',"
                "to_jsonb(now()-interval '1 second')) WHERE id=%s",(identity['id'],))
    elif phase=='accepted':
        with psycopg.connect(page_db) as db:
            db.execute("UPDATE tasks SET request_params=jsonb_set(request_params,'{_media_lifecycle_v1,accepted_at}',"
                "to_jsonb(now()-interval '1 day')) WHERE id=%s",(identity['id'],))
    else:
        await worker.rpc(identity,'record_chat_image_provider_result',p_result={
            'status':'success','image_urls':['https://provider.invalid/result.png']})
    files = FileExecutor(runtime.settings.file_workspace_root, identity['user_id'], None)
    path = files.resolve_safe_path(f"generated/{identity['id']}.png")
    path.parent.mkdir(parents=True,exist_ok=True)
    Image.new('RGB',(16,16),'blue').save(path)
    assert await run_process_pair(page_db, runtime, [identity], ['recovery','callback'], order)==0
    final = await worker.refresh(identity)
    state = final['request_params']['_media_lifecycle_v1']
    assert state['phase']=='published' and state['delivery_pending'] is False
    with psycopg.connect(page_db) as db:
        ledger = db.execute('SELECT amount,status FROM credit_transactions WHERE task_id=%s',(identity['id'],)).fetchall()
        refunds = db.execute('SELECT count(*),coalesce(sum(change_amount),0) FROM credits_history WHERE user_id=%s',
            (identity['user_id'],)).fetchone()
        balance = db.execute('SELECT credits FROM users WHERE id=%s',(identity['user_id'],)).fetchone()[0]
    assert len(ledger)==1 and ledger[0][0]==amount
    if phase=='uncertain' and order=='recovery_first': assert final['status']=='failed'
    elif phase!='uncertain' or order=='callback_first': assert final['status']=='completed'
    if final['status']=='completed':
        assert ledger[0][1]=='confirmed' and refunds==(0,0)
        assert final['credits_used']==amount and balance==1000-amount
    else:
        assert final['status']=='failed' and ledger[0][1]=='refunded'
        assert refunds==(1,amount) and final['credits_used']==0 and balance==1000
