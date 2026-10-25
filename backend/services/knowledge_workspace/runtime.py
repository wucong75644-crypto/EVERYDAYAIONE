"""Bind navigation to the persisted input anchor on first run and Actor replay."""
import asyncio
from types import SimpleNamespace
from .chat import KnowledgeRun, repository, unavailable

async def prepare_knowledge_run(handler, anchor, user_id, conversation_id, *, required=False):
    handler._knowledge_run=None
    # Explicit references retain priority. Default scope is enabled only with
    # the derivation rollout and is frozen per input under live actor/org RLS.
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
                                conversation_id=conversation_id,input_message_id=anchor.input_message_id,default_scope=default_scope and not required)
    if not run:
        if not required:return
        unavailable('KNOWLEDGE_UNAVAILABLE')
    if not getattr(handler,'_personal_context_allowed',True):unavailable()
    await asyncio.to_thread(run.revalidate)
    handler._knowledge_run=run
