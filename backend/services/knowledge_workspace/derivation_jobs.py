"""Bounded worker; persisted leases and dirty fences are in PostgreSQL."""
import asyncio
from contextlib import suppress
from loguru import logger
from core.config import get_settings
from core.db_scope import DatabaseScope, DatabaseAccessKind
from services.background_jobs import FairBackgroundJobs
from .repository import Repository
from .derivation import process, _client


class DerivationWorker:
    def __init__(self, pool):
        self.pool = pool
        self.jobs = FairBackgroundJobs('knowledge-derivation', 2, timeout=280)

    def candidates(self):
        if not get_settings().knowledge_ai_processing_enabled:return []
        repo = Repository(self.pool, DatabaseScope(None,None,DatabaseAccessKind.WORKER))
        with repo.cursor() as c:
            c.execute("SELECT to_regprocedure('public.kb_derivation_candidates()') IS NOT NULL AS enabled")
            if not c.fetchone()['enabled']:
                return []
            settings=get_settings()
            c.execute("SELECT to_regprocedure('public.kb_local_pipeline()') IS NOT NULL AS enabled")
            if not c.fetchone()['enabled'] and not settings.knowledge_derivation_enabled:return []
            c.execute("SELECT set_config('app.knowledge_users',%s,true),set_config('app.knowledge_orgs',%s,true)",(','.join(x.strip() for x in settings.knowledge_workspace_user_ids.split(',') if x.strip()),','.join(x.strip() for x in settings.knowledge_workspace_org_ids.split(',') if x.strip())))
            c.execute('SELECT public.kb_derivation_candidates() AS value')
            candidates = c.fetchone()['value']
            c.execute("SELECT to_regprocedure('public.kb_file_candidates(text)') IS NOT NULL AS enabled")
            if c.fetchone()['enabled']:
                from .extraction import media_config
                c.execute('SELECT public.kb_file_candidates(%s) AS value',(media_config(),))
                candidates=[*candidates[:16],*c.fetchone()['value'][:16]]
        settings = get_settings()
        users = {x.strip() for x in settings.knowledge_workspace_user_ids.split(',') if x.strip()}
        orgs = {x.strip() for x in settings.knowledge_workspace_org_ids.split(',') if x.strip()}
        return [job for job in candidates if not users and not orgs
                or str(job['actor_id']) in users or str(job['org_id']) in orgs]

    async def execute(self, job):
        if not get_settings().knowledge_ai_processing_enabled:return None
        repo=Repository(self.pool,DatabaseScope(str(job['actor_id']),str(job['org_id']) if job['org_id'] else None,DatabaseAccessKind.RUNTIME))
        if job.get('phase')=='file':
            from .file_jobs import process as process_file
            if job.get('needs_enqueue'):
                with repo.cursor() as c:c.execute('SELECT public.kb_file_enqueue(%s,%s,%s,%s)',(job['object_id'],job['binding_id'],job['revision'],job['config']))
            return await process_file(repo,job)
        if job.get('needs_enqueue'):
            with repo.cursor() as c:
                c.execute('SELECT public.kb_enqueue_derivation(%s)',(job['object_id'],))
        return await process(repo,str(job['object_id']))

    async def run(self):
        try:
            while True:
                if get_settings().knowledge_workspace_enabled:
                    try:
                        candidates = await asyncio.to_thread(self.candidates)
                        self.jobs.dispatch([(str(j.get('binding_id',j['object_id'])),str(j['actor_id']),str(j['org_id']),
                          lambda j=j:self.execute(j)) for j in candidates])
                    except Exception as error:
                        logger.warning('knowledge_derivation_scan_failed | error_type={}',type(error).__name__)
                await asyncio.sleep(3)
        finally:
            await self.jobs.close()
            await _client.close()

    async def stop(self, task):
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
