"""Connection setup failures never become ambiguous paid submissions."""

import httpx
import psycopg
import pytest

from services.adapters.base import ImageGenerateResult, TaskStatus
from tests.test_chat_image_lifecycle_postgres import isolated_db
from tests.test_ecommerce_recovery_postgres import ecom_db
from tests.test_detail_page_generation_postgres import page_db
from tests.test_detail_page_delivery_recovery import delivery_db
from tests.test_detail_concurrency_postgres import runtime


@pytest.fixture(scope='module', autouse=True)
def schema(delivery_db):
    return delivery_db


@pytest.mark.parametrize('error_type,uncertain', [
    (httpx.ConnectError, False), (httpx.ConnectTimeout, False), (httpx.PoolTimeout, False),
    (httpx.ReadTimeout, True), (httpx.WriteError, True), (httpx.ReadError, True),
])
async def test_single_submission_distinguishes_connection_from_response_loss(runtime, monkeypatch, page_db, error_type, uncertain):
    identity = runtime.accept()
    worker = runtime.worker()
    # Real adapter/client codec; only the external HTTP boundary is replaced.
    from services.adapters.kie.image_adapter import KieImageAdapter
    from services.adapters.kie.client import KieClient
    task = await worker.refresh(identity)
    adapter = KieImageAdapter(KieClient(api_key='isolated-test-key'), model=task['request_params']['_media_request_v1']['model'])
    calls = 0
    async def fail(request):
        nonlocal calls
        calls += 1
        raise error_type('isolated network boundary failure', request=request)
    adapter.client._client = httpx.AsyncClient(base_url=adapter.client.BASE_URL, transport=httpx.MockTransport(fail))
    monkeypatch.setattr('services.adapters.factory.create_image_adapter', lambda *a, **kw: adapter)
    await worker.submit(await worker.refresh(identity))
    row = await worker.refresh(identity)
    assert row['request_params']['_media_lifecycle_v1']['phase'] == ('uncertain' if uncertain else 'published')
    assert not row['external_task_id']
    assert calls == 1
    with psycopg.connect(page_db) as db:
        assert db.execute('SELECT count(*) FROM credit_transactions WHERE task_id=%s', (identity['id'],)).fetchone()[0] == 1
        assert db.execute('SELECT status FROM credit_transactions WHERE task_id=%s', (identity['id'],)).fetchone()[0] == ('pending' if uncertain else 'refunded')
        assert db.execute('SELECT count(*) FROM credits_history WHERE user_id=%s', (identity['user_id'],)).fetchone()[0] == (0 if uncertain else 1)
    if not uncertain:
        # A restarted scanner and duplicate results never recreate the request or refund twice.
        restarted = runtime.worker()
        await restarted.advance(row)
        await restarted.record_provider_result(row, ImageGenerateResult(task_id='unused', status=TaskStatus.FAILED))
        with psycopg.connect(page_db) as db:
            assert db.execute('SELECT credits FROM users WHERE id=%s', (identity['user_id'],)).fetchone()[0] == 1000
            assert db.execute('SELECT count(*),sum(change_amount) FROM credits_history WHERE user_id=%s', (identity['user_id'],)).fetchone() == (1, row['credits_locked'])
        assert not await runtime.limiter.redis.sismember(runtime.limiter._global_key(identity['user_id']), identity['id'])
        assert calls == 1
