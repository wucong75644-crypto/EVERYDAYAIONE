"""Recoverable personal-task consolidation; no journal writes in the answer path."""
import asyncio
import re
from contextlib import suppress
from loguru import logger

from core.config import get_settings
from core.db_scope import DatabaseScope, DatabaseAccessKind
from services.background_jobs import FairBackgroundJobs
from .ai_journal import JournalStore
from .ai_journal_extract import generate, worthwhile, validated_entries, message_text, existing_context, _client
from .repository import Repository


async def process(store, event_id, generator=generate):
    event = await asyncio.to_thread(store.claim, event_id)
    if not event:
        return
    try:
        if not worthwhile(event):
            await asyncio.to_thread(store.finish, event, 'skipped')
            return
        await asyncio.to_thread(store.revalidate, event)
        existing = await asyncio.to_thread(store.entries, None, 30, message_text(event.get('input')))
        value = await generator(event, existing)
        entries = validated_entries(value, event)
        readable = {row['entry_key'] for row in existing_context(existing) if not row['protected']}
        existing_keys = {row['entry_key'] for row in existing}
        entries = [entry for entry in entries if entry['key'] not in existing_keys or entry['key'] in readable]
        def core(body):
            return re.sub(r'\n\n\[来源会话\]\(/chat/[^)]+\)$', '', body).strip()
        # Another source link is not new knowledge; keep the existing document
        # and its original provenance when its actual content is unchanged.
        entries = [entry for entry in entries if not any(
            old['category'] == entry['category'] and core(old['body']) == core(entry['body'])
            for old in existing)]
        if entries:
            await asyncio.to_thread(store.save_batch, event, entries)
        else:
            await asyncio.to_thread(store.finish, event, 'skipped')
    except asyncio.CancelledError:
        # Persisted lease expires; the next worker retries under live authorization.
        raise
    except Exception as error:
        logger.warning('knowledge_journal_process_failed | error_type={}', type(error).__name__)
        await asyncio.to_thread(store.finish, event, 'failed', type(error).__name__)


class JournalWorker:
    def __init__(self, pool):
        self.pool = pool
        self.jobs = FairBackgroundJobs('knowledge-ai-journal', 2, timeout=180)

    def candidates(self):
        repo = Repository(self.pool, DatabaseScope(None, None, DatabaseAccessKind.WORKER))
        with repo.cursor() as c:
            c.execute("SELECT to_regprocedure('public.kb_ai_candidates()') IS NOT NULL AS enabled")
            if not c.fetchone()['enabled']:
                return []
            s = get_settings()
            c.execute("SELECT set_config('app.knowledge_users',%s,true),set_config('app.knowledge_orgs',%s,true)", (','.join(x.strip() for x in s.knowledge_workspace_user_ids.split(',') if x.strip()), ','.join(x.strip() for x in s.knowledge_workspace_org_ids.split(',') if x.strip())))
            c.execute('SELECT public.kb_ai_candidates() AS value')
            return c.fetchone()['value']

    async def execute(self, job):
        from api.routes.knowledge import enabled
        from types import SimpleNamespace
        if not get_settings().knowledge_ai_journal_enabled or not enabled(SimpleNamespace(user_id=str(job['actor_id']), org_id=str(job['org_id']) if job['org_id'] else None)):
            return
        repo = Repository(self.pool, DatabaseScope(str(job['actor_id']), str(job['org_id']) if job['org_id'] else None, DatabaseAccessKind.RUNTIME))
        await process(JournalStore(repo), job['id'])

    async def run(self):
        try:
            while True:
                s = get_settings()
                if s.knowledge_ai_journal_enabled and s.knowledge_workspace_enabled:
                    try:
                        candidates = await asyncio.to_thread(self.candidates)
                        self.jobs.dispatch([(str(j['id']), str(j['actor_id']), str(j['org_id']),
                                             lambda j=j: self.execute(j)) for j in candidates])
                    except Exception as error:
                        logger.warning('knowledge_journal_scan_failed | error_type={}', type(error).__name__)
                await asyncio.sleep(10)
        finally:
            await self.jobs.close()
            await _client.close()

    async def stop(self, task):
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
