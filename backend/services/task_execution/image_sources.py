"""Exact saved prompt selections, distinct from newly authored method analysis."""
import hashlib
import json
import re
from typing import Literal
from uuid import UUID
from pydantic import Field
from .contracts import Fact,Key,TaskError

SOURCE_REQUIREMENT='host.image_source'
SOURCE_VALIDATOR='image.source.valid'

class SourcePrompt(Fact):
    message_id: UUID
    content_index: int = Field(strict=True,ge=0)
    sha256: str = Field(pattern=r'^[a-f0-9]{64}$')

class SavedImageBrief(Fact):
    title: str
    prompt: str = Field(min_length=1,max_length=12000)
    aspect_ratio: str = Field(pattern=r'^[1-9]\d?:[1-9]\d?$')
    plan_item_id: Key
    source_prompt: SourcePrompt

class SavedPromptDocument(Fact):
    schema_version: Literal['saved-image-prompts.v1']='saved-image-prompts.v1'
    image_plans: tuple[SavedImageBrief,...]=Field(min_length=1,max_length=100)


def completed_sources(state):
    steps={step.id:step for step in state.steps}
    source_ids={r.id for r in state.requirements if r.validator==SOURCE_VALIDATOR}
    ids={key for result in state.results if result.step_id in steps and source_ids & set((
        *steps[result.step_id].requirement_ids,*steps[result.step_id].contributes_to)) for key in result.artifact_ids}
    return tuple(a for a in state.artifacts if a.id in ids and a.kind=='text'
        and SOURCE_VALIDATOR in a.validations and a.reference.startswith(('legacy-plan:','legacy-message:','task-source:')))


def has_source_handoff(state):
    return bool(state and state.intent_action in {'execute_ready','execute_prompt'} and completed_sources(state))


def prompt_candidates(source_id,parts):
    choices=[]
    for part in parts:
        text=part['text'];index=part['content_index']
        ranges=[(0,len(text))]+[(m.start(1),m.end(1)) for m in re.finditer(r'```[^\n]*\n(.*?)\n```',text,re.S)]
        for start,end in dict.fromkeys(ranges):
            prompt=text[start:end]
            if not prompt.strip() or len(prompt)>12000 or (prompt!=text and text.count(prompt)!=1): continue
            checksum=hashlib.sha256(prompt.encode()).hexdigest()
            key=hashlib.sha256(json.dumps([str(source_id),index,start,end,checksum],separators=(',',':')).encode()).hexdigest()
            choices.append({'id':key,'content_index':index,'start':start,'end':end,'sha256':checksum,
                'preview':prompt[:200],'text':prompt})
    return choices


def saved_prompt_document(state,source_id,parts,selected,aspect_ratio):
    count=sum(r.minimum for r in state.requirements if r.artifact_kind=='image')
    choices={c['id']:c for c in prompt_candidates(source_id,parts)}
    if not count or len(selected) not in {1,count} or any(key not in choices for key in selected):
        raise TaskError('TASK_SAVED_PROMPT_SELECTION_INVALID')
    selected=selected*count if len(selected)==1 else selected
    briefs=[]
    for index,key in enumerate(selected,1):
        source=choices[key]
        if aspect_ratio is None:
            raise TaskError('TASK_SAVED_PROMPT_ASPECT_RATIO_REQUIRED')
        briefs.append(SavedImageBrief(title=f'原提示词{index}',prompt=source['text'],aspect_ratio=aspect_ratio,
            plan_item_id='saved-'+key[:24]+'-'+str(index),source_prompt=SourcePrompt(message_id=source_id,
                content_index=source['content_index'],sha256=source['sha256'])))
    return SavedPromptDocument(image_plans=tuple(briefs)).model_dump_json()


def reused_source(state,step_id,source,source_root):
    """Host-only promotion of a committed structured plan; never copy old reviews."""
    targets=next(s for s in state.steps if s.id==step_id)
    if not any(r.validator==SOURCE_VALIDATOR and r.id in (*targets.requirement_ids,*targets.contributes_to) for r in state.requirements):
        return source.reference,()
    from .methods import ImageMethodDocument
    try:document=ImageMethodDocument.model_validate_json(source.inline_text)
    except ValueError:
        try:saved=SavedPromptDocument.model_validate_json(source.inline_text)
        except ValueError:return source.reference,()
        count=sum(r.minimum for r in state.requirements if r.artifact_kind=='image')
        if (count and len(saved.image_plans)>=count and all(source.reference==f'legacy-message:{p.source_prompt.message_id}' for p in saved.image_plans)):
            return source.reference,(SOURCE_VALIDATOR,)
        return source.reference,()
    count=sum(r.minimum for r in state.requirements if r.artifact_kind=='image')
    if not count or len(document.image_plans)<count:return source.reference,()
    # Original plan_source links must retain their native plan identity.
    if any(p.plan_source is not None for p in document.image_plans):
        if not source.reference.startswith('legacy-plan:'):raise TaskError('TASK_IMAGE_SOURCE_PLAN_IDENTITY_INVALID')
        return source.reference,(SOURCE_VALIDATOR,)
    return f'task-source:{source_root}:{source.id}',(SOURCE_VALIDATOR,)
