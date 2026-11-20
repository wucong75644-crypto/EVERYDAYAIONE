"""Durable personal-account tasks and cross-worker leases; never creates provider jobs."""
import json
from uuid import UUID, uuid4


class TaskStore:
    def __init__(self, pool=None):
        self._pool = pool

    async def pool(self):
        if self._pool is None:
            from core.database import get_async_db
            self._pool = (await get_async_db()).pool
        return self._pool

    async def query(self, sql, params=(), many=False):
        async with (await self.pool()).connection() as conn:
            cursor = await conn.execute(sql, params)
            return await cursor.fetchall() if many else await cursor.fetchone()

    async def reserve(self, request_key, fingerprint, payload, callback_base=None):
        local_id = uuid4()
        payload = dict(payload)
        if callback_base:
            payload["callBackUrl"] = callback_base.rstrip("/")+"/api/kie-mcp-callback/"+str(local_id)
        row = await self.query('''INSERT INTO public.kie_mcp_video_tasks
            (id,request_key,fingerprint,payload) VALUES (%s,%s,%s,%s::jsonb)
            ON CONFLICT(request_key) DO NOTHING RETURNING *''',
            (local_id, request_key, fingerprint, json.dumps(payload)))
        if row:
            return row, True
        row = await self.query('SELECT * FROM public.kie_mcp_video_tasks WHERE request_key=%s', (request_key,))
        if row['fingerprint'] != fingerprint:
            raise ValueError('request_key already belongs to a different request')
        return row, False

    async def get(self, reference):
        return await self.query('''SELECT * FROM public.kie_mcp_video_tasks
            WHERE id::text=%s OR provider_task_id=%s ORDER BY (id::text=%s) DESC LIMIT 1''', (reference, reference, reference))

    async def by_key(self, request_key):
        return await self.query('SELECT * FROM public.kie_mcp_video_tasks WHERE request_key=%s', (request_key,))

    async def bind(self, local_id, provider_id):
        row = await self.query('''UPDATE public.kie_mcp_video_tasks SET provider_task_id=%s,
            state=CASE WHEN state='submission_unknown' THEN 'waiting' ELSE state END,
            updated_at=now(),next_check_at=now()
            WHERE id=%s AND (provider_task_id IS NULL OR provider_task_id=%s) RETURNING *''',
            (provider_id, local_id, provider_id))
        if not row:
            raise ValueError('Provider task identity conflict; do not resubmit')
        return row

    async def wake(self, local_id, candidate):
        return await self.query('''UPDATE public.kie_mcp_video_tasks SET callback_candidate=%s,
            next_check_at=now() WHERE id=%s AND state NOT IN ('success','fail')
            AND (provider_task_id IS NULL OR provider_task_id=%s) RETURNING id''',
            (candidate, local_id, candidate))

    async def claim(self):
        return await self.query('''WITH next AS (
            SELECT id FROM public.kie_mcp_video_tasks WHERE next_check_at<=now()
            AND (lease_until IS NULL OR lease_until<now()) AND (
                (state NOT IN ('success','fail') AND (provider_task_id IS NOT NULL OR callback_candidate IS NOT NULL))
                OR (state='success' AND storage_state<>'stored'))
            ORDER BY next_check_at LIMIT 1 FOR UPDATE SKIP LOCKED)
            UPDATE public.kie_mcp_video_tasks t SET lease_token=%s,lease_until=now()+interval '180 seconds'
            FROM next WHERE t.id=next.id RETURNING t.*''', (uuid4(),))

    async def finish(self, row, **changes):
        allowed = {'provider_task_id','callback_candidate','state','source_urls','archived_urls',
                   'storage_state','credits_consumed','error_code'}
        if not changes.keys() <= allowed:
            raise ValueError('Invalid task update')
        fields, values = [], []
        for key, value in changes.items():
            fields.append(key+'=%s'+('::jsonb' if key.endswith('_urls') else ''))
            values.append(json.dumps(value) if key.endswith('_urls') else value)
        fields += ["lease_token=NULL", "lease_until=NULL", "updated_at=now()",
                   "next_check_at=now()+interval '10 seconds'"]
        return await self.query('UPDATE public.kie_mcp_video_tasks SET '+','.join(fields)+
            ' WHERE id=%s AND lease_token=%s AND (provider_task_id IS NULL OR %s::text IS NULL OR provider_task_id=%s) RETURNING *',
            (*values, row['id'], row['lease_token'], changes.get('provider_task_id'), changes.get('provider_task_id')))

    async def listing(self, before=None, limit=20):
        if before is not None:
            UUID(before)
            return await self.query('''SELECT * FROM public.kie_mcp_video_tasks WHERE
                (created_at,id)<(SELECT created_at,id FROM public.kie_mcp_video_tasks WHERE id=%s)
                ORDER BY created_at DESC,id DESC LIMIT %s''', (before, limit), many=True)
        return await self.query('SELECT * FROM public.kie_mcp_video_tasks ORDER BY created_at DESC,id DESC LIMIT %s',
                                (limit,), many=True)
