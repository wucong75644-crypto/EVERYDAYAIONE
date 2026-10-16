"""Bounded local dispatch; durable claims and recovery remain owned by callers."""
import asyncio
import time
from collections import defaultdict, deque

from loguru import logger


class FairBackgroundJobs:
    def __init__(self, name, concurrency, timeout=None):
        self.name, self.concurrency, self.timeout = name, concurrency, timeout
        self.jobs = {}
        self._owner_turn = {}
        self._project_turn = {}
        self._item_turn = {}
        self._turn = 0
        self._next_prune = 0
        self._closed = False

    def dispatch(self, candidates):
        """Round-robin owners, projects, then durable IDs; never allocate waiting tasks.

        Each candidate is (durable_id, owner_key, project_key, async_operation).
        Only admitted work advances the fairness cursor.
        """
        if self._closed:
            return
        now = time.monotonic()
        if now >= self._next_prune:
            # Keep fairness across paginated scans without retaining retired
            # task IDs forever. Frequently discovered/active work never expires.
            self._item_turn = {key: value for key, value in self._item_turn.items()
                if key in self.jobs or now - value[1] < 3600}
            self._owner_turn = {key: value for key, value in self._owner_turn.items() if now - value[1] < 3600}
            self._project_turn = {key: value for key, value in self._project_turn.items() if now - value[1] < 3600}
            self._next_prune = now + 60
        owners = defaultdict(lambda: defaultdict(deque))
        seen = set(self.jobs)
        for key, owner, project, operation in candidates:
            self._item_turn[key] = (self._item_turn.get(key, (0, now))[0], now)
            self._owner_turn[owner] = (self._owner_turn.get(owner, (0, now))[0], now)
            self._project_turn[(owner, project)] = (self._project_turn.get((owner, project), (0, now))[0], now)
            if key not in seen:
                owners[owner][project].append((key, operation))
                seen.add(key)
        for projects in owners.values():
            for project, queue in projects.items():
                # Last-served order survives pages that omit the previous ID.
                # A single "last ID" cursor would repeatedly pick page prefixes.
                projects[project] = deque(sorted(queue, key=lambda item:self._item_turn[item[0]][0]))
        owner_keys = sorted(owners, key=lambda owner:self._owner_turn[owner][0])
        while owner_keys and len(self.jobs) < self.concurrency:
            for owner in list(owner_keys):
                if len(self.jobs) >= self.concurrency:
                    break
                projects = owners[owner]
                project = min(projects, key=lambda project:self._project_turn[(owner, project)][0])
                key, operation = projects[project].popleft()
                if not projects[project]:
                    del projects[project]
                if not projects:
                    owner_keys.remove(owner)
                self._turn += 1
                self._owner_turn[owner] = (self._turn, now)
                self._project_turn[(owner, project)] = (self._turn, now)
                self._item_turn[key] = (self._turn, now)
                job = asyncio.create_task(self._run(key, operation), name=f'{self.name}:{key}')
                self.jobs[key] = job
                job.add_done_callback(lambda done, key=key: self._finished(key, done))
        logger.debug('background_dispatch | kind={} active={} limit={} candidates={}',
            self.name, len(self.jobs), self.concurrency, len(candidates))

    def _finished(self, key, job):
        if self.jobs.get(key) is job:
            self.jobs.pop(key)
        if not job.cancelled():
            job.exception()

    async def _run(self, key, operation):
        started = time.monotonic()
        try:
            async with asyncio.timeout(self.timeout):
                await operation()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning('background_job_pending | kind={} id={} error_type={} elapsed={:.2f}',
                self.name, key, type(error).__name__, time.monotonic() - started)
        finally:
            logger.debug('background_job_finished | kind={} id={} elapsed={:.2f}',
                self.name, key, time.monotonic() - started)

    async def close(self):
        self._closed = True
        jobs = list(self.jobs.values())
        for job in jobs:
            job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)
        self.jobs.clear()
        self._owner_turn.clear()
        self._project_turn.clear()
        self._item_turn.clear()
