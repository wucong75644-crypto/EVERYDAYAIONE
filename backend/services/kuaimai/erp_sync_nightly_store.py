"""Durable nightly checkpoints and atomic document publication (PostgreSQL)."""
from __future__ import annotations

from collections import defaultdict
from contextlib import asynccontextmanager

from psycopg.types.json import Jsonb

from services.kuaimai.erp_sync_persistence import _write_doc_group_txn, collect_affected_keys
from utils.time_context import now_cn


def org_key(org_id):
    return str(org_id) if org_id else '__default__'


class NightlyStore:
    def __init__(self, db, org_id):
        self.db, self.org_id = db, org_id
        self.pool = getattr(db, '_pool', None)
        if self.pool is None:
            raise RuntimeError('Nightly synchronization requires the PostgreSQL pool')
        if isinstance(self.pool.max_size, int) and self.pool.max_size < 2:
            raise RuntimeError('Nightly synchronization needs at least two database connections')

    async def pending(self):
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT * FROM erp_sync_batches WHERE org_key=%s AND status <> 'complete' "
                "ORDER BY business_date LIMIT 1", (org_key(self.org_id),))
            return await cur.fetchone()

    async def ensure_batch(self, business_date, as_of):
        async with self.pool.connection() as conn:
            await conn.execute(
                'INSERT INTO erp_sync_batches(org_id,org_key,business_date,source_as_of) '
                'VALUES (%s,%s,%s,%s) ON CONFLICT(org_key,business_date) DO NOTHING',
                (self.org_id, org_key(self.org_id), business_date, as_of))
            cur = await conn.execute('SELECT * FROM erp_sync_batches WHERE org_key=%s AND business_date=%s',
                                     (org_key(self.org_id), business_date))
            return await cur.fetchone()

    async def update(self, batch_id, **fields):
        allowed = {'progress', 'status', 'attempts', 'next_retry_at', 'last_error'}
        if not fields or not set(fields) <= allowed:
            raise ValueError('Invalid batch fields')
        params = [Jsonb(v) if k == 'progress' else v for k, v in fields.items()]
        async with self.pool.connection() as conn:
            await conn.execute('UPDATE erp_sync_batches SET ' + ','.join(f'{k}=%s' for k in fields)
                               + ' WHERE id=%s AND org_key=%s', (*params, batch_id, org_key(self.org_id)))

    async def complete(self, batch_id):
        async with self.pool.connection() as conn:
            await conn.execute("UPDATE erp_sync_batches SET status='complete',completed_at=now(),last_error=NULL "
                               "WHERE id=%s AND org_key=%s AND progress->>'documents_published'='true' "
                               "AND progress->>'maintenance'='true'", (batch_id,org_key(self.org_id)))

    async def stage(self, batch_id, rows):
        groups = defaultdict(list)
        for row in rows:
            groups[(row['doc_type'], row['doc_id'])].append({**row, 'org_id': self.org_id})
        async with self.pool.connection() as conn:
            async with conn.transaction():
                for (kind, doc_id), doc_rows in groups.items():
                    cur = await conn.execute(
                        'INSERT INTO erp_sync_staged_documents(batch_id,org_id,doc_type,doc_id,rows_json) '
                        'SELECT %s,%s,%s,%s,%s WHERE EXISTS (SELECT 1 FROM erp_sync_batches '
                        'WHERE id=%s AND org_key=%s) ON CONFLICT(batch_id,doc_type,doc_id) '
                        'DO UPDATE SET rows_json=EXCLUDED.rows_json',
                        (batch_id, self.org_id, kind, doc_id, Jsonb(doc_rows), batch_id, org_key(self.org_id)))
                    if cur.rowcount != 1:
                        raise ValueError("Batch tenant mismatch")
        return len(rows)

    async def pages(self, batch_id, kind=None, connection=None):
        last = ('', '')
        while True:
            async def read(conn):
                cur = await conn.execute(
                    'SELECT doc_type,doc_id,rows_json FROM erp_sync_staged_documents '
                    'WHERE batch_id=%s AND org_id IS NOT DISTINCT FROM %s '
                    'AND (doc_type,doc_id)>(%s,%s) '
                    + ('AND doc_type=%s ' if kind else '') + 'ORDER BY doc_type,doc_id LIMIT 100',
                    (batch_id, self.org_id, *last, *((kind,) if kind else ())))
                return await cur.fetchall()
            if connection is not None:
                docs = await read(connection)
            else:
                async with self.pool.connection() as conn:
                    docs = await read(conn)
            if not docs:
                return
            yield docs
            last = (docs[-1]['doc_type'], docs[-1]['doc_id'])

    @asynccontextmanager
    async def _connection(self, existing=None):
        if existing is not None:
            yield existing
        else:
            async with self.pool.connection() as conn:
                yield conn

    async def publish(self, batch_id, progress, connection=None):
        """Facts, legacy aggregates and success watermarks commit or roll back together."""
        affected = set()
        async with self._connection(connection) as conn:
            async with conn.transaction():
                cur = await conn.execute(
                    'SELECT status FROM erp_sync_batches WHERE id=%s AND org_key=%s FOR UPDATE',
                    (batch_id, org_key(self.org_id)))
                batch = await cur.fetchone()
                if batch is None:
                    raise ValueError('Batch tenant mismatch')
                if batch['status'] == 'complete':
                    return
                # Publication is serialized even if Redis disappears or a stale queue task survives.
                await conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                                   ('erp-publish:' + org_key(self.org_id),))
                async for docs in self.pages(batch_id, connection=conn):
                    for doc in docs:
                        kind, doc_id, rows = doc['doc_type'], doc['doc_id'], doc['rows_json']
                        cur = await conn.execute(
                            'SELECT outer_id,doc_created_at FROM erp_document_items '
                            'WHERE org_id IS NOT DISTINCT FROM %s AND doc_type=%s AND doc_id=%s '
                            'UNION ALL SELECT outer_id,doc_created_at FROM erp_document_items_archive '
                            'WHERE org_id IS NOT DISTINCT FROM %s AND doc_type=%s AND doc_id=%s',
                            (self.org_id,kind,doc_id,self.org_id,kind,doc_id))
                        affected.update(collect_affected_keys(await cur.fetchall()))
                        # Promote refreshed history to hot; never leave a duplicate in cold storage.
                        await conn.execute('DELETE FROM erp_document_items_archive '
                                           'WHERE org_id IS NOT DISTINCT FROM %s AND doc_type=%s AND doc_id=%s',
                                           (self.org_id,kind,doc_id))
                        await _write_doc_group_txn(conn, kind, doc_id, rows)
                        affected.update(collect_affected_keys(rows))
                async for docs in self.pages(batch_id, 'aftersale', connection=conn):
                    for doc in docs:
                        await self._refund_header(conn, doc['rows_json'][0])
                # Re-resolve payment dates for previous refunds when their original order is corrected.
                await conn.execute(
                    "UPDATE erp_registered_refunds r SET original_pay_time=o.pay_time, updated_at=now() "
                    "FROM (SELECT DISTINCT ON (doc_id) doc_id,pay_time FROM erp_document_items "
                    "WHERE org_id IS NOT DISTINCT FROM %s AND doc_type='order') o "
                    "WHERE r.org_key=%s AND r.order_sid=o.doc_id AND r.original_pay_time IS DISTINCT FROM o.pay_time",
                    (self.org_id, org_key(self.org_id)))
                for outer, day in sorted(affected):
                    await conn.execute('SELECT erp_aggregate_daily_stats(%s,%s::date,%s::uuid)',
                                       (outer, day, self.org_id))
                for kind, watermark in progress.get('watermarks', {}).items():
                    await conn.execute(
                        "INSERT INTO erp_sync_state(sync_type,org_id,last_sync_time,is_initial_done,status,last_run_at) "
                        "VALUES(%s,%s,%s,true,'idle',%s) ON CONFLICT((COALESCE(org_id,'00000000-0000-0000-0000-000000000000'::uuid)),sync_type) DO UPDATE SET "
                        "last_sync_time=EXCLUDED.last_sync_time,is_initial_done=true,status='idle',"
                        "last_run_at=EXCLUDED.last_run_at,error_count=0,last_error=NULL",
                        (kind,self.org_id,watermark,now_cn().replace(tzinfo=None)))
                await conn.execute("UPDATE erp_sync_batches SET progress=progress || '{\"documents_published\":true}'::jsonb "
                                   'WHERE id=%s AND org_key=%s', (batch_id, org_key(self.org_id)))
                await conn.execute('DELETE FROM erp_sync_staged_documents WHERE batch_id=%s', (batch_id,))

    async def _refund_header(self, conn, row):
        sid = row.get('order_sid')
        # Query on this transaction also sees an order published earlier in this batch.
        cur = await conn.execute(
            "SELECT pay_time FROM erp_document_items WHERE org_id IS NOT DISTINCT FROM %s "
            "AND doc_type='order' AND doc_id=%s UNION ALL "
            "SELECT pay_time FROM erp_document_items_archive WHERE org_id IS NOT DISTINCT FROM %s "
            "AND doc_type='order' AND doc_id=%s LIMIT 1", (self.org_id,sid,self.org_id,sid))
        order = await cur.fetchone()
        if not order or not order.get('pay_time'):
            raise RuntimeError(f"Unresolved original payment date for aftersale {row['doc_id']}")
        await conn.execute(
            'INSERT INTO erp_registered_refunds(org_key,org_id,work_order_id,order_sid,platform_refund_id,'
            'original_pay_time,refund_money,platform_refund_money,work_status,online_status,source_modified_at) '
            'VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(org_key,work_order_id) DO UPDATE SET '
            'order_sid=EXCLUDED.order_sid,platform_refund_id=EXCLUDED.platform_refund_id,'
            'original_pay_time=EXCLUDED.original_pay_time,refund_money=EXCLUDED.refund_money,'
            'platform_refund_money=EXCLUDED.platform_refund_money,work_status=EXCLUDED.work_status,'
            'online_status=EXCLUDED.online_status,source_modified_at=EXCLUDED.source_modified_at,updated_at=now()',
            (org_key(self.org_id),self.org_id,row['doc_id'],sid,row.get('platform_refund_id'),order['pay_time'],
             row.get('refund_money'),row.get('raw_refund_money'),str(row.get('doc_status')),
             str(row.get('online_status')),row.get('doc_modified_at')))
