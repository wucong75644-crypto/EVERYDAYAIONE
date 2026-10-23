"""Bind navigation to the persisted input anchor on first run and Actor replay."""
import asyncio
from types import SimpleNamespace
from .chat import KnowledgeRun, repository, unavailable

async def prepare_knowledge_run(handler, anchor, user_id, conversation_id, *, required=False):
    handler._knowledge_run=None
    # Only the server-persisted intent marker activates this extension. Plain
    # chat must not acquire knowledge DB scope or change its retry path.
    if not required:return
    from api.routes.knowledge import enabled
    from services.handlers.context_snapshot import ContextAnchor
    if not isinstance(anchor,ContextAnchor):unavailable('KNOWLEDGE_UNAVAILABLE')
    if not enabled(SimpleNamespace(user_id=user_id,org_id=handler.org_id)):unavailable('KNOWLEDGE_UNAVAILABLE')
    if not getattr(handler,'_personal_context_allowed',True):unavailable()
    run=await asyncio.to_thread(KnowledgeRun.start,repository(handler.db,user_id,handler.org_id),
                                conversation_id=conversation_id,input_message_id=anchor.input_message_id)
    if not run:unavailable('KNOWLEDGE_UNAVAILABLE')
    if not getattr(handler,'_personal_context_allowed',True):unavailable()
    await asyncio.to_thread(run.revalidate)
    handler._knowledge_run=run
