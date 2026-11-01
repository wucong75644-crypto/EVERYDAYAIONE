"""Lossless source adapters: no analysis, model calls or business submission."""
import hashlib
import json
from uuid import UUID

from .contracts import Artifact,TaskError
from .methods import ImageMethodDocument,ImageBrief,PlanSource


def native_plan_document(plan):
    if plan.get('status')!='ready' or type(plan.get('plan_revision')) is not int:
        raise TaskError('TASK_LEGACY_PLAN_NOT_READY')
    stages=plan.get('stage_outputs') or {}
    if not stages.get('1') or not stages.get('2') or not plan.get('items'):
        raise TaskError('TASK_LEGACY_PLAN_INCOMPLETE')
    def text(value): return value if isinstance(value,str) else json.dumps(value,ensure_ascii=False)
    briefs=[]
    for position,item in enumerate(sorted(plan['items'],key=lambda i:i['position']),1):
        if (item['position']!=position or item['aspect_ratio']!=plan['target_size']['aspect_ratio']
                or hashlib.sha256(item['request_text'].encode()).hexdigest()!=item['request_text_sha256']
                or item['request_text']!=item['positive_prompt']+'\n\n负面提示词：'+item['negative_prompt']):
            raise TaskError('TASK_LEGACY_PLAN_CONTENT_CHANGED')
        briefs.append(ImageBrief(title=item['name'],layout=item['scheme_markdown'],prompt=item['positive_prompt'],
            negative_prompt=item['negative_prompt'],aspect_ratio=item['aspect_ratio'],
            plan_source=PlanSource(plan_id=plan['id'],revision=plan['plan_revision'],item_id=item['item_id'])))
    # Retain the complete original stage outputs. Labels identify their source;
    # the adapter does not invent a new reference analysis or visual strategy.
    return ImageMethodDocument(reference_analysis=text(stages['1']),product_facts=text(stages['1']),
        visual_direction=text(stages['2']),page_continuity=text(stages['2']),image_plans=tuple(briefs)).model_dump_json()


async def import_source(reader,state,*,owner_task_id,step_id,kind,source_id,source_revision,prompt_ids=(),aspect_ratio=None):
    from .database_scope import task_database
    from services.conversation_state import ConversationStopRequested
    try:
        response=await task_database(reader.db,state).rpc('read_agent_task_legacy_source',{
        'p_task_id':reader.task_id,'p_execution_token':reader.execution_token,'p_logical_task_id':str(state.root_task_id),
        'p_expected_revision':state.revision,'p_kind':kind,'p_source_id':str(source_id),'p_source_revision':source_revision}).execute()
    except Exception:
        raise TaskError('TASK_LEGACY_SOURCE_READ_FAILED') from None
    value=response.data if response else None
    if not isinstance(value,dict): raise TaskError('TASK_LEGACY_SOURCE_INVALID')
    if value.get('outcome') in {'ownership_lost','lease_expired'}: raise ConversationStopRequested('ownership_lost')
    if value.get('outcome')=='stale': raise TaskError('TASK_REVISION_STALE')
    if value.get('outcome')!='found': raise TaskError('TASK_LEGACY_SOURCE_NOT_FOUND')
    if value.get('kind')!=kind or value.get('source_id')!=str(source_id) or value.get('source_revision')!=source_revision:
        raise TaskError('TASK_LEGACY_SOURCE_INVALID')
    try:
        from .image_sources import prompt_candidates,saved_prompt_document,SOURCE_VALIDATOR,SavedPromptDocument
        choices=[]
        body=native_plan_document(value['plan']) if kind=='ecom_plan' else value['text']
        if kind=='legacy_message':
            choices=prompt_candidates(source_id,value.get('parts') or [])
            if prompt_ids:
                body=saved_prompt_document(state,source_id,value.get('parts') or [],prompt_ids,aspect_ratio)
        count=sum(r.minimum for r in state.requirements if r.artifact_kind=='image')
        valid=False
        for schema in (ImageMethodDocument,SavedPromptDocument):
            try:
                parsed=schema.model_validate_json(body)
                valid=bool(count and len(parsed.image_plans)>=count)
                break
            except ValueError:
                continue
        if not isinstance(body,str) or not body.strip(): raise ValueError('empty source')
        checksum=hashlib.sha256(body.encode()).hexdigest()
        reference=f'legacy-plan:{source_id}:{source_revision}' if kind=='ecom_plan' else f'legacy-message:{source_id}'
        return Artifact(id='legacy-'+hashlib.sha256(f'{reference}:{step_id}:{checksum}'.encode()).hexdigest()[:32],
            kind='text',reference=reference,sha256=checksum,inline_text=body,source_task_id=UUID(value['source_task_id']),
            delivery_task_id=UUID(str(owner_task_id)),source_call_id=reference,step_id=step_id,validations=('artifact.exists',SOURCE_VALIDATOR) if valid else ('artifact.exists',)), [
                {k:v for k,v in choice.items() if k!='text'} for choice in choices]
    except (ValueError,KeyError,TypeError) as error:
        if isinstance(error,TaskError): raise
        raise TaskError('TASK_LEGACY_SOURCE_INVALID') from None
