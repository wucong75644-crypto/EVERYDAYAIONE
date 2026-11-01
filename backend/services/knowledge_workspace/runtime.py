"""Bind navigation to the persisted input anchor on first run and Actor replay."""
import asyncio
from types import SimpleNamespace
from .chat import KnowledgeRun, repository, unavailable

async def prepare_knowledge_run(handler, anchor, user_id, conversation_id, *, required=False, question=None, self_contained=False, defer_default=False):
    handler._knowledge_run=None
    handler._knowledge_answer_guard=None
    # Only explicit references and real previous tool snapshots restore access.
    # Default candidates are prepared independently and never become a guard.
    from core.config import get_settings
    settings = get_settings()
    default_scope = settings.knowledge_derivation_enabled and settings.knowledge_default_scope_enabled
    if not required and not default_scope:return
    from api.routes.knowledge import enabled
    from services.handlers.context_snapshot import ContextAnchor
    if not isinstance(anchor,ContextAnchor):
        if not required: return
        unavailable('KNOWLEDGE_UNAVAILABLE')
    if not enabled(SimpleNamespace(user_id=user_id,org_id=handler.org_id)):
        if not required: return
        unavailable('KNOWLEDGE_UNAVAILABLE')
    if not getattr(handler,'_personal_context_allowed',True):
        if not required:return
        unavailable()
    run=await asyncio.to_thread(KnowledgeRun.start,repository(handler.db,user_id,handler.org_id),
                                conversation_id=conversation_id,input_message_id=anchor.input_message_id,default_scope=False,question=question,allow_self_contained=False)
    if not run:
        if not required:return
        unavailable('KNOWLEDGE_UNAVAILABLE')
    if not getattr(handler,'_personal_context_allowed',True):unavailable()
    await asyncio.to_thread(run.revalidate)
    if run.snapshot.get('automatic') is True:
        def consumed():
            with run.repo.cursor() as c:
                c.execute('SELECT 1 FROM public.kb_read_receipts WHERE snapshot_id=%s LIMIT 1', (run.id,))
                return c.fetchone() is not None
        # Older releases froze default navigation before the model requested
        # knowledge. Restore handles and usage provenance without an output-review obligation.
        run.knowledge_used = await asyncio.to_thread(consumed)
    handler._knowledge_run=run
