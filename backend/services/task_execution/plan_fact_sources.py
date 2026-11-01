"""Review evidence comes from the same scoped native originals as submission."""
import asyncio
import base64
import io

from .contracts import TaskError
from .image_resources import fingerprint,native_resolver
from .reducer import digest


def source_text(native,message_id):
    from services.handlers.chat_context.image_sources import content_parts
    row=native._message(message_id)
    # Assistant output is useful visual reference, never user-asserted facts.
    return {'id':'user:'+message_id,'text':'\n'.join(p.get('text','') for p in content_parts(row.get('content'))
        if isinstance(p,dict) and p.get('type')=='text')} if row.get('role')=='user' else None


def capture_sources(native,references=None):
    if references is None:
        known=native.known_sources()
        current=[(row,index,part) for row,index,part in known if str(row['id'])==native.input_message_id]
        selected=current or [(row,index,part) for row,index,part in known if row.get('role')=='user']
        if len(selected)>16:raise TaskError('TASK_PLAN_FACT_SOURCES_SELECTION_REQUIRED')
        references=[{'message_id':str(row['id']),'content_index':index,'role':'reference'} for row,index,_ in selected]
    resolved=native.resolve(references)
    text_ids=[native.input_message_id]
    for ref in resolved:
        for key in ('message_id','source_message_id','quoted_message_id'):
            if ref.get(key) and str(ref[key]) not in text_ids:text_ids.append(str(ref[key]))
    texts=[value for key in text_ids if (value:=source_text(native,key)) is not None]
    if sum(len(t['text']) for t in texts)>50000:raise TaskError('TASK_PLAN_FACT_SOURCE_TEXT_LIMIT')
    value={'references':references,'resolved':fingerprint(resolved),'texts':texts,
        'origins':['generated' if ref.get('source')=='generated' or ref.get('quoted_task_id') else 'user_reference' for ref in resolved]}
    return {**value,'sha256':digest(value)}


def check_sources(native,snapshot,*,state=None):
    if not isinstance(snapshot,dict) or snapshot.get('sha256')!=digest({k:v for k,v in snapshot.items() if k!='sha256'}):
        raise TaskError('TASK_PLAN_FACT_SOURCE_INVALID')
    actual=capture_sources(native,snapshot['references'])
    if snapshot.get('tool_facts'):
        from .native_results import fact_sources
        if state is None:state=getattr(getattr(native.owner,'_actor_runtime',None),'task_execution_state',None)
        if state is None:raise TaskError('TASK_PLAN_FACT_SOURCE_INVALID')
        actual['tool_facts']=fact_sources(native,state,snapshot['tool_facts'])
        actual['sha256']=digest({k:v for k,v in actual.items() if k!='sha256'})
    if actual!=snapshot:raise TaskError('TASK_PLAN_FACT_SOURCE_CHANGED')
    return actual


def physical(reference):
    return tuple(str(reference.get(key)) for key in ('workspace_path','content_sha256','file_version'))


def verify_review_sources(state,artifact,native,resolved):
    from .plan_facts import review_proof
    proof=review_proof(state,artifact)
    if proof is None:raise TaskError('TASK_PLAN_FACT_REVIEW_REQUIRED')
    if native is None:raise TaskError('TASK_PLAN_FACT_SOURCE_RESOLVER_UNAVAILABLE')
    check_sources(native,proof['sources'],state=state)
    identities={physical(ref) for ref in proof['sources']['resolved']}
    if any(physical(ref) not in identities for ref in fingerprint(resolved)):
        raise TaskError('TASK_PLAN_FACT_REFERENCE_NOT_REVIEWED')


def preview(native,reference):
    from PIL import Image,ImageOps
    from services.file_resources import content_digest
    path=native.files.guarded(reference['workspace_path'])
    if content_digest(path)!=reference['content_sha256']:raise TaskError('TASK_PLAN_FACT_SOURCE_CHANGED')
    with Image.open(path) as source:
        image=ImageOps.exif_transpose(source).convert('RGB')
        image.thumbnail((1600,1600))
        output=io.BytesIO();image.save(output,format='JPEG',quality=92)
    if content_digest(path)!=reference['content_sha256']:raise TaskError('TASK_PLAN_FACT_SOURCE_CHANGED')
    return 'data:image/jpeg;base64,'+base64.b64encode(output.getvalue()).decode('ascii')


def create_source_provider(handler,request,context):
    async def provide(*,snapshot=None,references=None,images=False):
        def read():
            native=native_resolver(handler,request,context)
            value=check_sources(native,snapshot) if snapshot is not None else capture_sources(native,references)
            if snapshot is None:
                state=getattr(getattr(handler,'_actor_runtime',None),'task_execution_state',None)
                if state is not None:
                    from .native_results import fact_sources
                    facts=fact_sources(native,state)
                    if facts:
                        if sum(len(f['text']) for f in facts)>50000:raise TaskError('TASK_PLAN_FACT_SOURCE_TEXT_LIMIT')
                        value['tool_facts']=facts
                        value['sha256']=digest({k:v for k,v in value.items() if k!='sha256'})
            pictures=([{'id':'image-'+str(i),'source':value['origins'][i-1],
                'image_url':preview(native,ref)} for i,ref in enumerate(value['resolved'],1)] if images else [])
            return value,pictures
        return await asyncio.to_thread(read)
    return provide
