"""Nightly scheduling, durable recovery and delayed-refund regressions; no live ERP."""
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from services.kuaimai.erp_sync_nightly import NightlySync, due_business_date, windows
from services.kuaimai.erp_sync_service import ErpSyncService, PageLimitError
from services.kuaimai.erp_sync_row_builders import _build_aftersale_rows, _build_order_rows

CN = ZoneInfo('Asia/Shanghai')
NOW = datetime(2026,10,24,2,tzinfo=CN)


def service():
    return ErpSyncService(MagicMock(), client=SimpleNamespace(request_with_retry=AsyncMock()))


@pytest.mark.parametrize('hour,expected', [(1,None),(2,'2026-10-23'),(23,'2026-10-23')])
def test_nightly_boundary(hour, expected):
    value = due_business_date(NOW.replace(hour=hour),2)
    assert (str(value) if value else None) == expected


def test_catchup_windows_cover_three_day_gap():
    start = NOW-timedelta(days=3,hours=2)
    parts = list(windows(start,NOW))
    assert parts[0][0] == start and parts[-1][1] == NOW
    assert all(a[1] == b[0] for a,b in zip(parts,parts[1:]))
    assert all((b-a).total_seconds() <= 86400 for a,b in parts)


@pytest.mark.asyncio
async def test_incomplete_pagination_raises():
    svc = service()
    svc._client.request_with_retry.return_value = {'total':5,'list':[{'id':1}]}
    with pytest.raises(RuntimeError,match='Incomplete'):
        await svc.fetch_all_pages('test',{},page_size=2)


@pytest.mark.asyncio
async def test_has_next_overrides_short_page():
    svc = service()
    svc._client.request_with_retry.side_effect = [
        {'hasNext':True,'list':[{'id':1}]}, {'hasNext':False,'list':[{'id':2}]}]
    assert len(await svc.fetch_all_pages('test',{},page_size=200)) == 2


@pytest.mark.asyncio
async def test_missing_total_archive_is_not_zero():
    svc = service()
    svc._client.request_with_retry.return_value = {'list':[{'id':1}]}
    assert await svc.fetch_all_pages('test',{'queryType':1},page_size=200) == [{'id':1}]


@pytest.mark.asyncio
async def test_repeated_page_and_hard_cap_fail_closed():
    svc = service()
    svc._client.request_with_retry.return_value = {'list':[{'id':1}]}
    with pytest.raises(RuntimeError,match='Repeated'):
        await svc.fetch_all_pages('test',{},page_size=1)
    svc.MAX_PAGES = 1
    with pytest.raises(PageLimitError):
        await svc.fetch_all_pages('test',{},page_size=1)


@pytest.mark.asyncio
async def test_page_two_failure_never_returns_partial_success():
    svc = service()
    svc._client.request_with_retry.side_effect = [{'list':[{'id':1}]},TimeoutError('page2')]
    with pytest.raises(TimeoutError):
        await svc.fetch_all_pages('test',{},page_size=1)


def test_sid_and_source_time_mapping_without_platform_modified():
    svc = service()
    after = _build_aftersale_rows({'id':100,'sid':200,'refundMoney':8.8},svc)[0]
    assert after['order_sid'] == '200'
    order = _build_order_rows({'sid':200,'updTime':1792684800000,'orders':[{}]},svc)[0]
    assert order['source_updated_at'] == '2026-10-23 00:00:00'
    assert order['doc_modified_at'] is None


def test_empty_order_keeps_header_for_removed_last_item():
    assert _build_order_rows({'sid':200,'orders':[]},service())[0]['doc_id'] == '200'


@pytest.mark.asyncio
async def test_failure_keeps_checkpoint_and_backoff_without_publishing():
    store = SimpleNamespace(update=AsyncMock(),stage=AsyncMock(),publish=AsyncMock(),complete=AsyncMock())
    runner = NightlySync(MagicMock(),None,MagicMock(),store=store)
    runner._incremental = AsyncMock(side_effect=TimeoutError('page2'))
    batch = {'id':'batch','attempts':0,'source_as_of':NOW,'progress':{'incremental:order':'2026-10-22T02:00:00+08:00'}}
    with patch('services.kuaimai.erp_sync_nightly.now_cn',return_value=NOW):
        with pytest.raises(TimeoutError):
            await runner._run_batch(batch)
    assert runner.progress == batch['progress']
    store.publish.assert_not_awaited()
    store.complete.assert_not_awaited()
    fields = store.update.call_args.kwargs
    assert fields['status'] == 'failed' and fields['next_retry_at'] > NOW


@pytest.mark.asyncio
async def test_successful_incremental_step_not_repeated_after_restart():
    runner = NightlySync(MagicMock(),None,MagicMock(),store=MagicMock())
    runner.progress = {'incremental:order':True}
    runner.svc._get_sync_state = AsyncMock()
    await runner._incremental('order',NOW)
    runner.svc._get_sync_state.assert_not_awaited()


@pytest.mark.asyncio
async def test_resume_from_completed_window_not_initial_or_yesterday():
    store = SimpleNamespace(update=AsyncMock())
    runner = NightlySync(MagicMock(),None,MagicMock(),store=store)
    runner.batch = {'id':'batch'}
    cursor = NOW-timedelta(days=2)
    runner.progress = {'incremental:order':cursor.isoformat()}
    runner.svc._get_sync_state = AsyncMock(return_value={'is_initial_done':False})
    runner._incremental_window = AsyncMock()
    await runner._incremental('order',NOW)
    assert runner._incremental_window.call_args_list[0].args[1] == cursor
    assert len(runner._incremental_window.call_args_list) == 2
    assert runner.progress['incremental:order'] is True


@pytest.mark.asyncio
async def test_history_reloads_existing_refund_amount_without_modified_filter():
    runner = NightlySync(MagicMock(),None,MagicMock(),store=MagicMock())
    runner.svc.upsert_document_items = AsyncMock()
    async def pages(method,params,**kwargs):
        assert 'startApplyTime' in params and 'startModified' not in params
        yield [{'id':1,'sid':2,'refundMoney':30,'modified':None}]
    runner.svc.fetch_pages_streaming = pages
    await runner._history_window('aftersale',NOW-timedelta(days=8),NOW-timedelta(days=7))
    rows = runner.svc.upsert_document_items.call_args.args[0]
    assert rows[0]['doc_id'] == '1' and rows[0]['refund_money'] == 30


@pytest.mark.asyncio
async def test_page_limit_splits_window_instead_of_skipping():
    runner = NightlySync(MagicMock(),None,MagicMock(),store=MagicMock())
    runner.svc._sync_window = AsyncMock(side_effect=[PageLimitError(),0,0])
    start = NOW-timedelta(days=1)
    await runner._incremental_window('order',start,NOW)
    calls=runner.svc._sync_window.call_args_list
    assert calls[1].args[1] == start and calls[2].args[2] == NOW
    assert calls[1].args[2] == calls[2].args[1]


@pytest.mark.asyncio
async def test_refund_link_refreshes_archived_original_order():
    store = SimpleNamespace(pages=None,update=AsyncMock())
    async def docs(*args):
        yield [{'doc_id':'10','rows_json':[{'order_sid':'20'}]}]
    store.pages = docs
    runner=NightlySync(MagicMock(),None,MagicMock(),store=store)
    runner.batch={'id':'batch'}; runner.progress={}
    runner.svc.fetch_all_pages=AsyncMock(side_effect=[[],[{'sid':20,'payTime':1790208000000,'orders':[{}]}]])
    runner.svc.upsert_document_items=AsyncMock()
    await runner._link_refunds()
    assert runner.svc.fetch_all_pages.call_args_list[1].args[1] == {'sid':'20','queryType':1}
    assert runner.progress['refund_links']


@pytest.mark.asyncio
async def test_original_order_missing_retains_incomplete_batch():
    store = SimpleNamespace(pages=None,update=AsyncMock())
    async def docs(*args):
        yield [{'doc_id':'10','rows_json':[{'order_sid':'20'}]}]
    store.pages=docs
    runner=NightlySync(MagicMock(),None,MagicMock(),store=store)
    runner.batch={'id':'batch'};runner.progress={}
    runner.svc.fetch_all_pages=AsyncMock(return_value=[])
    with pytest.raises(RuntimeError,match='Cannot refresh'):
        await runner._link_refunds()
    assert not runner.progress.get('refund_links')


@pytest.mark.asyncio
async def test_retired_queue_tasks_do_not_execute_business_calls():
    from services.kuaimai.erp_sync_worker_pool import ErpSyncWorkerPool
    pool=ErpSyncWorkerPool(MagicMock(),MagicMock(),MagicMock(),set())
    pool._execute_task=AsyncMock();pool._check_org_concurrency=AsyncMock()
    for kind in ['order','stock','platform_map','daily_maintenance','order_reconcile']:
        await pool._process_task(0,'org:'+kind)
    pool._execute_task.assert_not_awaited()
    pool._check_org_concurrency.assert_not_awaited()


@pytest.mark.asyncio
async def test_manual_trigger_only_wakes_coordinator_not_sync_service():
    from services.kuaimai.erp_local_sync_trigger import trigger_erp_sync
    with patch('core.redis.RedisClient.enqueue_task',new_callable=AsyncMock) as enqueue, \
         patch.object(ErpSyncService,'sync',new_callable=AsyncMock) as sync:
        text=await trigger_erp_sync(MagicMock(),'order','org1')
    assert enqueue.call_args.args[1]=='org1:nightly'
    assert '不代表同步已完成' in text
    sync.assert_not_awaited()


@pytest.mark.asyncio
async def test_same_number_of_rows_with_duplicate_document_ids_is_incomplete():
    svc=service()
    svc._client.request_with_retry.side_effect=[{'total':2,'list':[{'sid':1}]},{'list':[{'sid':1}]}]
    with pytest.raises(RuntimeError,match='repeated document'):
        await svc.fetch_all_pages('erp.trade.outstock.simple.query',{},page_size=1)
