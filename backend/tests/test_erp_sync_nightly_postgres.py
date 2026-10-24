"""Opt-in PostgreSQL tests. Only an explicitly supplied /tmp Unix socket is accepted.

ERP_SYNC_TEST_DSN='host=/tmp/... port=... dbname=postgres' pytest this_file
Creates a disposable schema; never reads application DATABASE_URL or live ERP.
"""
import os
import re
from datetime import datetime, date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from psycopg import AsyncConnection
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from services.kuaimai.erp_sync_nightly_store import NightlyStore

DSN = os.environ.get('ERP_SYNC_TEST_DSN','')
pytestmark = pytest.mark.skipif(not DSN, reason='Explicit disposable PostgreSQL required')
ROOT = Path(__file__).parents[1]


@pytest_asyncio.fixture
async def database():
    config = conninfo_to_dict(DSN)
    assert config.get('host','').startswith('/tmp/kuaimai-nightly-tests')
    schema = 'nightly_test_' + uuid4().hex
    admin = await AsyncConnection.connect(DSN,autocommit=True,row_factory=dict_row)
    try:
        await admin.execute('CREATE SCHEMA '+schema)
        await admin.execute('SET search_path TO '+schema+',public')
        if not await (await admin.execute("SELECT 1 FROM pg_roles WHERE rolname='everydayai'")).fetchone():
            await admin.execute('CREATE ROLE everydayai')
        base=(ROOT/'migrations/029_erp_local_index_system.sql').read_text()
        for table in ('erp_document_items','erp_document_items_archive','erp_product_daily_stats','erp_sync_state'):
            ddl=re.search(r'CREATE TABLE IF NOT EXISTS '+table+r' \(.*?\n\);',base,re.S).group()
            if table=='erp_sync_state':
                ddl=ddl.replace('VARCHAR(20) UNIQUE NOT NULL','VARCHAR(20) NOT NULL')
            await admin.execute(ddl)
            await admin.execute(f'ALTER TABLE {table} ADD COLUMN org_id UUID')
        for table in ('erp_document_items','erp_document_items_archive'):
            await admin.execute(f'CREATE UNIQUE INDEX ON {table}(doc_type,doc_id,item_index,org_id)')
        await admin.execute("CREATE UNIQUE INDEX ON erp_sync_state(COALESCE(org_id,'00000000-0000-0000-0000-000000000000'::uuid),sync_type)")
        # Use production DDL for all expanded order/aftersale fields.
        extra=(ROOT/'migrations/081_expand_order_aftersale_fields.sql').read_text()
        for ddl in re.findall(r'ALTER TABLE erp_document_items(?:_archive)? ADD COLUMN IF NOT EXISTS .*?;',extra,re.S):
            await admin.execute(ddl)
        await admin.execute((ROOT/'migrations/107_daily_stats_add_dimensions.sql').read_text())
        await admin.execute((ROOT/'migrations/307_erp_nightly_sync.sql').read_text())
        pool=AsyncConnectionPool(DSN,kwargs={'row_factory':dict_row,'options':f'-c search_path={schema},public'},min_size=1,max_size=4,open=False)
        await pool.open();await pool.wait()
        try:
            yield SimpleNamespace(_pool=pool),admin
        finally:
            await pool.close()
    finally:
        await admin.execute('SET search_path TO public')
        await admin.execute('DROP SCHEMA '+schema+' CASCADE')
        await admin.close()


def order(amount=100, pay='2026-09-24 10:00:00'):
    return {'doc_type':'order','doc_id':'order1','item_index':0,'outer_id':'SKU',
            'doc_created_at':'2026-09-24 09:00:00','pay_time':pay,'amount':amount,
            'platform':'pdd','shop_name':'shop'}


def refund(amount=30):
    return {'doc_type':'aftersale','doc_id':'refund1','item_index':0,'outer_id':'SKU',
            'doc_created_at':'2026-10-23 00:06:00','order_sid':'order1','refund_money':amount,
            'raw_refund_money':amount,'doc_status':'9','online_status':'7','platform':'pdd'}


@pytest.mark.asyncio
@pytest.mark.parametrize('tenant',[None,'tenant'])
async def test_atomic_publication_and_historical_refund_correction(database,tenant):
    db,conn=database
    oid=str(uuid4()) if tenant else None
    store=NightlyStore(db,oid)
    batch=await store.ensure_batch(date(2026,10,23),datetime.fromisoformat('2026-10-24T02:00:00+08:00'))
    # Refund first in lexical order: original order must still resolve in the publication transaction.
    await store.stage(batch['id'],[order(),refund(),{**refund(),'item_index':1}])
    assert not await (await conn.execute('SELECT * FROM erp_document_items')).fetchall()
    progress={'watermarks':{'order':'2026-10-24 02:00:00'}}
    await store.publish(batch['id'],progress)
    facts=await (await conn.execute('SELECT * FROM erp_registered_refunds')).fetchall()
    assert len(facts)==1 and facts[0]['refund_money']==Decimal('30')
    assert str(facts[0]['original_pay_time'].date())=='2026-09-24'
    assert await (await conn.execute('SELECT is_initial_done FROM erp_sync_state')).fetchone()=={'is_initial_done':True}
    # Same work order is updated, not counted again; old payment date is replaced too.
    batch2=await store.ensure_batch(date(2026,10,24),datetime.fromisoformat('2026-10-25T02:00:00+08:00'))
    await store.stage(batch2['id'],[order(pay='2026-09-25 10:00:00'),refund(50)])
    await store.publish(batch2['id'],progress)
    day=await (await conn.execute('SELECT * FROM erp_registered_refund_daily')).fetchall()
    assert len(day)==1 and str(day[0]['payment_date'])=='2026-09-25'
    assert day[0]['system_registered_refund']==Decimal('50')
    assert (await (await conn.execute('SELECT count(*) AS n FROM erp_document_items WHERE doc_type=\'aftersale\'')).fetchone())['n']==1


@pytest.mark.asyncio
async def test_failed_publication_rolls_back_data_watermark_and_keeps_staging(database):
    db,conn=database;store=NightlyStore(db,None)
    batch=await store.ensure_batch(date(2026,10,23),datetime.now())
    await store.stage(batch['id'],[order()])
    # Failure after document replacement, before watermark commit.
    await conn.execute("CREATE OR REPLACE FUNCTION erp_aggregate_daily_stats(p_outer_id VARCHAR,p_stat_date DATE,p_org_id UUID DEFAULT NULL) RETURNS VOID LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'test aggregate failed'; END $$")
    with pytest.raises(Exception,match='test aggregate failed'):
        await store.publish(batch['id'],{'watermarks':{'order':'2026-10-24 02:00:00'}})
    assert not await (await conn.execute('SELECT * FROM erp_document_items')).fetchall()
    assert not await (await conn.execute('SELECT * FROM erp_sync_state')).fetchall()
    assert len(await (await conn.execute('SELECT * FROM erp_sync_staged_documents')).fetchall())==1


@pytest.mark.asyncio
async def test_archive_refresh_does_not_duplicate_and_retains_other_archived_facts(database):
    db,conn=database;store=NightlyStore(db,None)
    await conn.execute("INSERT INTO erp_document_items_archive(doc_type,doc_id,item_index,outer_id,amount,doc_created_at) VALUES('order','order1',0,'SKU',100,'2026-09-24'),('order','order2',0,'SKU',20,'2026-09-24')")
    batch=await store.ensure_batch(date(2026,10,23),datetime.now())
    await store.stage(batch['id'],[order(80)])
    await store.publish(batch['id'],{})
    rows=await (await conn.execute("SELECT order_count,order_amount FROM erp_product_daily_stats WHERE stat_date='2026-09-24'")).fetchall()
    assert sum(x['order_count'] for x in rows)==2
    assert sum(x['order_amount'] for x in rows)==Decimal('100')
    assert (await (await conn.execute("SELECT count(*) AS n FROM erp_document_items_archive WHERE doc_id='order1'")).fetchone())['n']==0


@pytest.mark.asyncio
async def test_staging_and_publish_enforce_tenant(database):
    db,conn=database
    own=NightlyStore(db,str(uuid4()));other=NightlyStore(db,str(uuid4()))
    batch=await own.ensure_batch(date(2026,10,23),datetime.now())
    with pytest.raises(ValueError,match='tenant'):
        await other.stage(batch['id'],[order()])
    with pytest.raises(ValueError,match='tenant'):
        await other.publish(batch['id'],{})
    assert not await (await conn.execute('SELECT * FROM erp_document_items')).fetchall()


@pytest.mark.asyncio
async def test_restart_resumes_checkpoint_and_complete_batch_does_not_rerun(database):
    from unittest.mock import AsyncMock, patch
    from services.kuaimai.erp_sync_nightly import NightlySync
    from zoneinfo import ZoneInfo
    db,conn=database
    now=datetime(2026,10,24,2,tzinfo=ZoneInfo('Asia/Shanghai'))
    calls=[]
    def runner(fail=False):
        job=NightlySync(db,None,SimpleNamespace())
        job.svc._get_sync_state=AsyncMock(return_value={'is_initial_done':True,'last_sync_time':now.isoformat()})
        async def sync(kind,start,end):
            calls.append(kind)
            if kind=='aftersale' and fail:
                raise TimeoutError('simulated page timeout')
            await job.svc.upsert_document_items([order()] if kind=='order' else [refund()])
        job.svc._sync_window=sync
        job._history=AsyncMock();job._link_refunds=AsyncMock();job._open_aftersales=AsyncMock()
        return job
    with patch('services.kuaimai.erp_sync_nightly.now_cn',return_value=now), \
         patch('services.kuaimai.erp_sync_nightly.INCREMENTAL_TYPES',('order','aftersale')), \
         patch('services.kuaimai.erp_sync_master_handlers.sync_stock_full',new_callable=AsyncMock), \
         patch('services.kuaimai.erp_sync_executor.ErpSyncExecutor.run_daily_maintenance',new_callable=AsyncMock):
        with pytest.raises(TimeoutError):
            await runner(fail=True).run()
        assert calls==['order','aftersale']
        batch=await (await conn.execute('SELECT * FROM erp_sync_batches')).fetchone()
        assert batch['status']=='failed' and batch['progress']['incremental:order'] is True
        assert not await (await conn.execute('SELECT * FROM erp_document_items')).fetchall()
        await conn.execute('UPDATE erp_sync_batches SET next_retry_at=NULL')
        await runner().run()
        assert calls==['order','aftersale','aftersale']
        batch=await (await conn.execute('SELECT * FROM erp_sync_batches')).fetchone()
        assert batch['status']=='complete'
        await runner().run()
        assert calls==['order','aftersale','aftersale']


@pytest.mark.asyncio
async def test_archive_is_atomic_and_preserves_tenant(database):
    from services.kuaimai.erp_sync_executor import ErpSyncExecutor
    db,conn=database;oid=uuid4()
    await conn.execute("INSERT INTO erp_document_items(doc_type,doc_id,item_index,doc_created_at,org_id) VALUES('order','own',0,'2020-01-01',NULL),('order','other',0,'2020-01-01',%s)",(oid,))
    count=await ErpSyncExecutor(db)._archive_transactional(db._pool,'2026-01-01',None)
    assert count==1
    assert [x['doc_id'] for x in await (await conn.execute('SELECT doc_id FROM erp_document_items')).fetchall()]==['other']
    assert [x['doc_id'] for x in await (await conn.execute('SELECT doc_id FROM erp_document_items_archive')).fetchall()]==['own']


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get('ERP_SYNC_RUN_VOLUME_TEST')!='1', reason='Opt-in local volume test')
async def test_ten_thousand_staged_rows_publish_without_duplicate_orders(database):
    db,conn=database;store=NightlyStore(db,None)
    batch=await store.ensure_batch(date(2026,10,23),datetime.now())
    for start in range(0,5000,250):
        rows=[]
        for i in range(start,start+250):
            for item in (0,1):
                rows.append({**order(1),'doc_id':f'volume{i:05d}','item_index':item,'outer_id':'SKU'})
        await store.stage(batch['id'],rows)
    await store.publish(batch['id'],{})
    result=await (await conn.execute("SELECT count(*) AS rows,count(DISTINCT doc_id) AS orders,sum(amount) AS amount FROM erp_document_items")).fetchone()
    assert result=={'rows':10000,'orders':5000,'amount':Decimal('10000')}
    assert (await (await conn.execute("SELECT sum(order_count) AS n,sum(order_amount) AS amount FROM erp_product_daily_stats")).fetchone())=={'n':5000,'amount':Decimal('10000')}
