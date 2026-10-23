"""Restore typed knowledge intent before creating a new message or assistant."""
import asyncio
from types import SimpleNamespace
from fastapi import HTTPException
from schemas.knowledge_reference import KnowledgeReference
from schemas.message import MessageOperation, GenerationType
from .chat import repository, validate

async def prepare_knowledge_intent(db, body, actor, org, conversation, gen_type):
    if body.params:
        for key in list(body.params):
            if key.startswith('_knowledge') or key=='knowledge_refs':body.params.pop(key)
    if body.operation!=MessageOperation.SEND:
        result=db.table('messages').select('id,role,reply_to_message_id,knowledge_refs').eq('id',body.original_message_id).eq('conversation_id',conversation).maybe_single().execute()
        original=result.data if result else None
        if original:
            if original['role']=='assistant':
                result=db.table('messages').select('knowledge_refs').eq('id',original.get('reply_to_message_id')).eq('conversation_id',conversation).maybe_single().execute()
                original=result.data if result else None
            body.knowledge_refs=[KnowledgeReference.model_validate(r) for r in (original or {}).get('knowledge_refs',[])]
    if not body.knowledge_refs:return
    body.params = dict(body.params or {})
    body.params['_knowledge_refs_required'] = True
    if gen_type != GenerationType.CHAT:
        raise HTTPException(422,'知识引用仅支持主对话问答')
    from api.routes.knowledge import enabled
    if not enabled(SimpleNamespace(user_id=actor,org_id=org)):raise HTTPException(404,'知识引用不可用')
    row=db.table('conversations').select('scope_type,user_id').eq('id',conversation).maybe_single().execute()
    conversation_row=row.data if row else None
    if not conversation_row or conversation_row.get('scope_type') not in (None,'user') or str(conversation_row.get('user_id'))!=str(actor):
        raise HTTPException(403,'知识引用仅支持当前账号的个人主对话')
    await asyncio.to_thread(validate,repository(db,actor,org),[r.model_dump(mode='json') for r in body.knowledge_refs])
