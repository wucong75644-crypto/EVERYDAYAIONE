"""One durable nightly batch per tenant: incremental catch-up, then history verification.

Both Redis workers and the serial fallback call this entry. Redis is only a wake-up
queue; PostgreSQL owns the batch, checkpoints and cross-mode execution lock.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from core.config import get_settings
from core.org_scoped_db import OrgScopedDB
from services.kuaimai.erp_sync_nightly_store import NightlyStore, org_key
from services.kuaimai.erp_sync_service import ErpSyncService, PageLimitError
from services.kuaimai.erp_sync_row_builders import _build_order_rows, _build_aftersale_rows
from services.kuaimai.erp_sync_utils import _fmt_dt
from utils.time_context import now_cn


# Ordering matters: current dimensions before stock and documents; refunds after orders.
INCREMENTAL_TYPES = (
    'shop','warehouse','tag','category','logistics_company',
    'product','supplier','stock','purchase','receipt','shelf','purchase_return',
    'allocate','allocate_in','allocate_out','other_in','other_out','inventory_sheet',
    'unshelve','process_order','section_record','goods_section','order','aftersale','platform_map',
)
FULL_TYPES = {'shop','warehouse','tag','category','logistics_company','platform_map'}


def due_business_date(now, hour):
    """Before today's trigger, only yesterday's unfinished batch may resume."""
    if now.hour < hour:
        return None
    return now.date() - timedelta(days=1)


def as_cn(value):
    from zoneinfo import ZoneInfo
    tz = ZoneInfo('Asia/Shanghai')
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return value.replace(tzinfo=tz) if value.tzinfo is None else value.astimezone(tz)


def windows(start, end):
    while start < end:
        stop = min(start + timedelta(days=1), end)
        yield start, stop
        start = stop


class NightlySync:
    def __init__(self, db, org_id, client, extend_fn=None, store=None):
        self.db, self.org_id, self.client = db, org_id, client
        self.settings = get_settings()
        self.store = store or NightlyStore(db, org_id)
        self.extend_fn = extend_fn
        self.svc = ErpSyncService(OrgScopedDB(db, org_id), org_id=org_id,
                                  client=client, lock_extend_fn=extend_fn)
        self.svc.strict_sync = True
        self.guard = None
        self.svc._lock_extend_fn = self._heartbeat

    async def run(self):
        # A session advisory lock also protects Redis-down fallback and process restarts.
        async with self.store.pool.connection() as guard:
            await guard.set_autocommit(True)
            key = 'erp-nightly:' + org_key(self.org_id)
            cur = await guard.execute('SELECT pg_try_advisory_lock(hashtextextended(%s,0)) AS locked', (key,))
            if not (await cur.fetchone())['locked']:
                return
            try:
                batch = await self.store.pending()
                if batch is None:
                    day = due_business_date(now_cn(), self.settings.erp_sync_nightly_hour)
                    if day is None:
                        return
                    batch = await self.store.ensure_batch(day, now_cn().replace(microsecond=0))
                if batch['status'] == 'complete':
                    return
                retry_at = batch.get('next_retry_at')
                if retry_at and as_cn(retry_at) > now_cn():
                    return
                self.guard = guard
                await self._run_batch(batch)
                return True
            finally:
                self.guard = None
                await guard.execute('SELECT pg_advisory_unlock(hashtextextended(%s,0))', (key,))

    async def _run_batch(self, batch):
        self.batch = batch
        self.progress = dict(batch.get('progress') or {})
        attempts = batch['attempts'] + 1
        await self.store.update(batch['id'], status='running', attempts=attempts,
                                next_retry_at=None, last_error=None)
        self.svc.document_sink = lambda rows: self.store.stage(batch['id'], rows)
        try:
            upper = as_cn(batch['source_as_of'])
            if not self.progress.get('documents_published'):
                for kind in INCREMENTAL_TYPES:
                    await self._incremental(kind, upper)
                if not self.progress.get('stock_full'):
                    from services.kuaimai.erp_sync_master_handlers import sync_stock_full
                    await sync_stock_full(self.svc)
                    await self._checkpoint('stock_full')
                await self._history(upper)
                await self._open_aftersales(upper)
                await self._link_refunds()
                await self._heartbeat()
                await self.store.publish(batch['id'], self.progress, connection=self.guard)
                await self._checkpoint('documents_published')
            if not self.progress.get('maintenance'):
                from services.kuaimai.erp_sync_executor import ErpSyncExecutor
                await ErpSyncExecutor(OrgScopedDB(self.db,self.org_id)).run_daily_maintenance(
                    org_id=self.org_id,client=self.client)
                await self._checkpoint('maintenance')
            await self.store.complete(batch['id'])
        except BaseException as exc:
            # Cancellation leaves persisted progress and staged documents intact too.
            delay = min(self.settings.erp_sync_retry_seconds * 2 ** min(attempts - 1, 6), 3600)
            await self.store.update(batch['id'], status='failed', last_error=str(exc)[:1000],
                                    next_retry_at=now_cn() + timedelta(seconds=delay))
            raise

    async def _heartbeat(self):
        if self.guard is not None:
            await self.guard.execute("SELECT 1")  # A lost DB session also loses the batch lock.
        if self.extend_fn:
            await self.extend_fn()

    async def _checkpoint(self, key, value=True):
        self.progress[key] = value
        await self.store.update(self.batch['id'], progress=self.progress)
        await self._heartbeat()

    async def _incremental(self, kind, upper):
        key = 'incremental:' + kind
        if self.progress.get(key) is True:
            return
        state = await self.svc._get_sync_state(kind)
        if state is None:
            await self.svc._init_sync_state(kind)
            state = await self.svc._get_sync_state(kind)
        if state is None:
            raise RuntimeError('Cannot read sync state for ' + kind)
        if isinstance(self.progress.get(key), str):
            start = as_cn(self.progress[key])
        elif state.get('is_initial_done') and state.get('last_sync_time'):
            start = as_cn(state['last_sync_time']) - timedelta(hours=self.settings.erp_sync_overlap_hours)
        else:
            days = self.svc.INITIAL_DAYS_OVERRIDE.get(kind, self.settings.erp_sync_initial_days)
            start = upper - timedelta(days=days)
        if kind in FULL_TYPES:
            start = upper - timedelta(days=1)
        for begin, end in windows(start, upper):
            await self._incremental_window(kind, begin, end)
            await self._checkpoint(key, end.isoformat())
        self.progress.setdefault('watermarks', {})[kind] = upper.replace(tzinfo=None).isoformat()
        await self._checkpoint(key)

    async def _incremental_window(self, kind, begin, end):
        try:
            await self.svc._sync_window(kind, begin, end)
        except PageLimitError:
            if (end-begin).total_seconds() <= 1:
                raise
            midpoint = begin + timedelta(seconds=int((end-begin).total_seconds() // 2))
            await self._incremental_window(kind, begin, midpoint)
            await self._incremental_window(kind, midpoint, end)

    async def _history(self, upper):
        midnight = upper.replace(hour=0, minute=0, second=0, microsecond=0)
        # Keep the plan in the batch, so a retry after midnight never changes its scope.
        plan = self.progress.get('history_plan')
        if plan is None:
            async with self.store.pool.connection() as conn:
                cur = await conn.execute(
                    "SELECT progress->>'history_cursor' AS cursor FROM erp_sync_batches "
                    "WHERE org_key=%s AND status='complete' ORDER BY business_date DESC LIMIT 1",
                    (org_key(self.org_id),))
                previous = await cur.fetchone()
            floor = midnight - timedelta(days=self.settings.erp_sync_initial_days)
            cursor = as_cn(previous['cursor']) if previous and previous.get('cursor') else floor
            cursor = max(cursor, floor)
            recent_start = midnight - timedelta(days=self.settings.erp_sync_history_recent_days)
            if cursor >= recent_start:
                cursor = floor
            older_end = min(cursor + timedelta(days=self.settings.erp_sync_history_days_per_batch), recent_start)
            plan = [(a.isoformat(), b.isoformat()) for a,b in windows(recent_start,midnight)]
            plan += [(a.isoformat(),b.isoformat()) for a,b in windows(cursor,older_end)]
            self.progress['history_cursor'] = older_end.isoformat()
            await self._checkpoint('history_plan', plan)
        for begin, end in plan:
            start, stop = as_cn(begin), as_cn(end)
            for kind in ('order','aftersale'):
                key = f'history:{kind}:{begin}'
                if self.progress.get(key):
                    continue
                await self._history_window_split(kind,start,stop)
                await self._checkpoint(key)

    async def _history_window_split(self, kind, start, end):
        try:
            await self._history_window(kind,start,end)
        except PageLimitError:
            if (end-start).total_seconds() <= 1:
                raise
            midpoint = start + timedelta(seconds=int((end-start).total_seconds() // 2))
            await self._history_window_split(kind,start,midpoint)
            await self._history_window_split(kind,midpoint,end)

    async def _history_window(self, kind, start, end):
        if kind == 'order':
            # Query both partitions near the archive boundary; stable doc IDs deduplicate staging.
            for partition in (0, 1):
                params = {'queryType':partition,'timeType':'pay_time',
                          'startTime':_fmt_dt(start),'endTime':_fmt_dt(end)}
                async for docs in self.svc.fetch_pages_streaming('erp.trade.outstock.simple.query',params,page_size=200):
                    rows = [r for doc in docs for r in _build_order_rows(doc,self.svc)]
                    await self.svc.upsert_document_items(rows)
        else:
            # Independent of modified: catch old work orders whose amount changed without a new timestamp.
            params = {'asVersion':2,'startApplyTime':_fmt_dt(start),'endApplyTime':_fmt_dt(end)}
            async for docs in self.svc.fetch_pages_streaming('erp.aftersale.list.query',params,page_size=200):
                rows = [r for doc in docs for r in _build_aftersale_rows(doc,self.svc)]
                await self.svc.upsert_document_items(rows)

    async def _open_aftersales(self, upper):
        """Poll known unfinished/recent work orders even if modified never advances."""
        if self.progress.get('open_aftersales'):
            return
        cursor = self.progress.get('open_aftersales_after', '')
        while True:
            async with self.store.pool.connection() as conn:
                cur = await conn.execute(
                    "SELECT DISTINCT doc_id FROM (SELECT doc_id,doc_status,doc_modified_at FROM erp_document_items "
                    "WHERE org_id IS NOT DISTINCT FROM %s AND doc_type='aftersale' UNION ALL "
                    "SELECT doc_id,doc_status,doc_modified_at FROM erp_document_items_archive "
                    "WHERE org_id IS NOT DISTINCT FROM %s AND doc_type='aftersale') facts "
                    "WHERE doc_id>%s AND (doc_status IN ('2','12') OR doc_modified_at>=%s) "
                    "ORDER BY doc_id LIMIT 100",
                    (self.org_id,self.org_id,cursor,(upper-timedelta(days=7)).replace(tzinfo=None)))
                known = await cur.fetchall()
            if not known:
                break
            ids = {row['doc_id'] for row in known}
            docs = await self.svc.fetch_all_pages('erp.aftersale.list.query',
                        {'ids':','.join(sorted(ids)),'asVersion':2},page_size=200)
            if {str(doc.get('id')) for doc in docs} != ids:
                raise RuntimeError('Incomplete known aftersale refresh')
            await self.svc.upsert_document_items([r for d in docs for r in _build_aftersale_rows(d,self.svc)])
            cursor = known[-1]['doc_id']
            await self._checkpoint('open_aftersales_after',cursor)
        await self._checkpoint('open_aftersales')

    async def _link_refunds(self):
        if self.progress.get('refund_links'):
            return
        seen = set()
        after = self.progress.get("refund_links_after", "")
        async for docs in self.store.pages(self.batch['id'], 'aftersale'):
            for doc in docs:
                if doc["doc_id"] <= after:
                    continue
                head = doc['rows_json'][0]
                sid = head.get('order_sid')
                if not sid:
                    raise RuntimeError('Missing original order sid for aftersale ' + doc['doc_id'])
                sid = str(sid)
                if sid in seen:
                    continue
                seen.add(sid)
                # Refresh even if present locally: refunds can change the original order too.
                found = False
                for partition in (0,1):
                    records = await self.svc.fetch_all_pages('erp.trade.outstock.simple.query',
                                                            {'sid':sid,'queryType':partition},page_size=200)
                    records = [r for r in records if str(r.get('sid')) == sid]
                    if records:
                        await self.svc.upsert_document_items([r for d in records for r in _build_order_rows(d,self.svc)])
                        found = True
                        break
                if not found:
                    raise RuntimeError('Cannot refresh original order ' + sid)
                await self._heartbeat()
            await self._checkpoint('refund_links_after', docs[-1]['doc_id'])
        await self._checkpoint('refund_links')
