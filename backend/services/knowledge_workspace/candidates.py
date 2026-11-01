"""Bounded, disposable preparation; never an answer or a knowledge receipt.

The main model never waits for this job. Original knowledge tools may reuse
versioned object bodies after their usual live authorization and read protocol.
"""
import asyncio
import threading
import time
import weakref
from types import SimpleNamespace

from loguru import logger

_slots = weakref.WeakKeyDictionary()


def collect(repo, anchor, cancelled):
    from .scope_selection import roots
    from .query_terms import keywords
    from services.handlers.chat_context.image_sources import content_parts
    with repo.cursor(connection_timeout=.2, read_only=True, statement_timeout_ms=500) as c:
        c.execute("SELECT content FROM public.messages WHERE id=%s AND conversation_id=%s AND turn_id=%s AND role='user'",
                  (anchor.input_message_id, anchor.conversation_id, anchor.turn_id))
        source = c.fetchone()
        if source is None or cancelled.is_set():
            return {}
        query = '\n'.join(p.get('text', '') for p in content_parts(source['content'])
                          if isinstance(p, dict) and p.get('type') == 'text')
        terms = keywords(query[:2000])[:8]
        if not terms:
            return {}
        refs, _ = roots(c, query, allow_self_contained=False)
        containers = [r['root']['id'] for r in refs]
        if not containers or cancelled.is_set():
            return {}
        c.execute('''SELECT o.id,v.revision,left(v.body,4001) AS body
          FROM public.kb_objects o JOIN public.kb_object_versions v
            ON v.object_id=o.id AND v.revision=o.current_revision
          WHERE o.container_id=ANY(%s::uuid[]) AND o.deleted_at IS NULL AND NOT o.undo_hidden
            AND EXISTS(SELECT 1 FROM unnest(%s::text[]) term
              WHERE strpos(lower(v.title||' '||left(v.body,4000)),lower(term))>0)
          ORDER BY (SELECT count(*) FROM unnest(%s::text[]) term
              WHERE strpos(lower(v.title),lower(term))>0) DESC,o.id LIMIT 8''',
                  (containers, terms, terms))
        # Truncated bodies cannot replace full reads/search or imply completeness.
        return {} if cancelled.is_set() else {
            (str(r['id']), int(r['revision'])): r['body'] for r in c.fetchall() if len(r['body']) <= 4000}


class CandidatePreparation:
    def __init__(self, repo, anchor):
        self.repo, self.anchor = repo, anchor
        self.cancelled = threading.Event()
        self.state = 'preparing'
        self.bodies = {}
        self.task = asyncio.create_task(self._run(), name='knowledge-candidates')

    async def _run(self):
        started = time.monotonic()
        loop = asyncio.get_running_loop()
        slots = _slots.setdefault(loop, asyncio.Semaphore(4))
        if slots.locked():
            self.state = 'busy'
            return
        await slots.acquire()
        # Cancellation of the chat must not free a worker slot while its bounded
        # read-only database job is still running in a thread.
        worker = loop.run_in_executor(None, collect, self.repo, self.anchor, self.cancelled)
        def finished(future):
            slots.release()
            if not future.cancelled():
                future.exception()  # Observe failures even if the owner stopped.
        worker.add_done_callback(finished)
        try:
            values = await asyncio.shield(worker)
            if not self.cancelled.is_set():
                self.bodies, self.state = values, 'ready'
        except asyncio.CancelledError:
            self.state = 'cancelled'
        except Exception:
            self.state = 'unavailable'
        finally:
            logger.info('Knowledge candidates | input={} | state={} | count={} | elapsed_ms={}',
                        self.anchor.input_message_id, self.state, len(self.bodies),
                        round((time.monotonic() - started) * 1000, 3))

    async def attach(self, run, input_message_id):
        if (self.cancelled.is_set() or str(input_message_id) != str(self.anchor.input_message_id)
                or run.repo.scope != self.repo.scope):
            return False
        try:
            # Waiting is permitted only inside the model-requested knowledge
            # tool. Slow preparation never delays unrelated tools or chat.
            await asyncio.wait_for(asyncio.shield(self.task), timeout=.6)
        except TimeoutError:
            return False
        except asyncio.CancelledError:
            if asyncio.current_task().cancelling():
                raise
            return False
        if self.state != 'ready' or self.cancelled.is_set():
            return False
        run._candidate_bodies = dict(self.bodies)
        return True

    async def close(self):
        self.cancelled.set()
        self.bodies.clear()
        if not self.task.done():
            self.task.cancel()
        # No wait for threads here: pool/statement budgets bound the remaining
        # work, which cannot write receipts or touch business state.


def start_preparation(handler, anchor, user_id, conversation_id, *, execution_context=None):
    from core.config import get_settings
    from api.routes.knowledge import enabled
    from services.handlers.context_snapshot import ContextAnchor
    settings = get_settings()
    if execution_context is not None:
        authorized = execution_context.authorized_tool_names
        if (execution_context.context_scope != 'user'
                or (authorized is not None and 'knowledge_discover' not in authorized)):
            return None
    if (not isinstance(anchor, ContextAnchor) or str(anchor.conversation_id) != str(conversation_id)
            or not settings.knowledge_default_scope_enabled or not settings.knowledge_derivation_enabled
            or getattr(handler, '_knowledge_run', None) is not None
            or not getattr(handler, '_personal_context_allowed', True)
            or not enabled(SimpleNamespace(user_id=user_id, org_id=handler.org_id))):
        return None
    from .runtime import repository
    return CandidatePreparation(repository(handler.db, user_id, handler.org_id), anchor)
