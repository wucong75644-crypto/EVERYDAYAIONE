"""Bind chosen original resources with the existing native image resolver.

Selectors are model proposals. Paths, versions and byte hashes come only from
native resolution. Native submission resolves them again before acceptance.
"""
import asyncio
import hashlib
import json
import re
from types import SimpleNamespace
from pydantic import Field
from .contracts import Fact,Key,Artifact,TaskError
from .methods import ImageMethodDocument
from .image_sources import SavedPromptDocument

VALIDATOR='image.resources.bound'
FINGERPRINT_FIELDS=('resource_ref','file_id','asset_id','message_id','content_index','source_message_id',
    'source_content_index','source_asset_id','source_content_sha256','quoted_message_id','quoted_content_index',
    'quoted_task_id','workspace_path','file_version','content_sha256','width','height','aspect_ratio','role')

class BindImageResources(Fact):
    expected_revision: int=Field(strict=True,ge=0)
    planning_artifact_id: Key
    image_item_index: int=Field(strict=True,ge=1,le=100)
    mode: str=Field(pattern=r'^(image_to_image|text_to_image)$')
    references: tuple[dict,...]=Field(max_length=16)
    step_id: Key | None=Field(default=None,
        description='仅当现有计划另有独立原图绑定准备步骤时填写其ID；必须就绪、无capability且不贡献用户交付。'
            '绑定证据将归属该准备步骤，返回artifact_ids后可完成它。没有独立准备步骤就不填。')


def fingerprint(references):
    return [{key:value[key] for key in FINGERPRINT_FIELDS if key in value} for value in references]


def planning_document(state,artifact_id):
    committed={key for result in state.results for key in result.artifact_ids}
    artifact=next((a for a in state.artifacts if a.id==artifact_id and a.kind=='text' and a.id in committed and a.step_id in {s.id for s in state.steps}),None)
    if artifact is None:raise TaskError('TASK_IMAGE_PLAN_EVIDENCE_MISSING')
    try:document=ImageMethodDocument.model_validate_json(artifact.inline_text)
    except ValueError:
        try:document=SavedPromptDocument.model_validate_json(artifact.inline_text)
        except ValueError:raise TaskError('TASK_IMAGE_PLAN_EVIDENCE_INVALID') from None
    return artifact,document


def binding_for(state,artifact_id,index):
    # Later pre-dispatch corrections append new evidence; older evidence is never
    # overwritten. Existing operations still pin the same stable effect/args.
    matches=[]
    for artifact in state.artifacts:
        if VALIDATOR not in artifact.validations or not artifact.inline_text:continue
        try:value=json.loads(artifact.inline_text)
        except ValueError:raise TaskError('TASK_IMAGE_RESOURCE_PROOF_INVALID') from None
        if not isinstance(value,dict):raise TaskError('TASK_IMAGE_RESOURCE_PROOF_INVALID')
        if value.get('planning_artifact_id')==artifact_id and value.get('image_item_index')==index:
            matches.append(value)
    return matches[-1] if matches else None


async def bind_resources(state,value,*,resolver,owner_task_id,call_id):
    artifact,document=planning_document(state,value.planning_artifact_id)
    proof_step=artifact.step_id
    preparation_ready=True
    if value.step_id is not None:
        from .reducer import ready_steps
        step=next((s for s in state.steps if s.id==value.step_id),None)
        if step is None or step.capability is not None or step.requirement_ids or step.contributes_to:
            raise TaskError('TASK_IMAGE_RESOURCE_PREPARATION_STEP_INVALID')
        preparation_ready=step.id in {s.id for s in ready_steps(state)}
        proof_step=step.id
    if value.image_item_index>len(document.image_plans):raise TaskError('TASK_IMAGE_SLOT_INVALID')
    if getattr(document.image_plans[value.image_item_index-1],'plan_source',None) is not None:
        raise TaskError('TASK_IMAGE_NATIVE_PLAN_ALREADY_BOUND')
    if (value.mode=='image_to_image')!=bool(value.references):raise TaskError('TASK_IMAGE_RESOURCE_MODE_MISMATCH')
    if resolver is None:raise TaskError('TASK_IMAGE_RESOURCE_RESOLVER_UNAVAILABLE')
    try:resolved=await resolver(list(value.references),require_originals=bool(state.method_bindings))
    except (ValueError,PermissionError,OSError) as error:
        code=getattr(error,'code',None) or str(error)
        raise TaskError(code if re.fullmatch(r'[A-Z][A-Z0-9_]{1,100}',code) else 'TASK_IMAGE_RESOURCE_BINDING_FAILED') from None
    if state.image_binding_version>=3:
        from .plan_facts import review_proof
        from .plan_fact_sources import physical
        approved=review_proof(state,artifact)
        if approved is None:raise TaskError('TASK_PLAN_FACT_REVIEW_REQUIRED')
        reviewed={physical(ref) for ref in approved['sources']['resolved']}
        if any(physical(ref) not in reviewed for ref in fingerprint(resolved)):
            raise TaskError('TASK_PLAN_FACT_REFERENCE_NOT_REVIEWED')
    proof={'version':1,'planning_artifact_id':artifact.id,'image_item_index':value.image_item_index,
        'mode':value.mode,'references':list(value.references),'resolved':fingerprint(resolved),'step_id':proof_step}
    text=json.dumps(proof,ensure_ascii=False,sort_keys=True,separators=(',',':'))
    checksum=hashlib.sha256(text.encode()).hexdigest()
    existing=binding_for(state,artifact.id,value.image_item_index)
    if existing==proof:return None,proof
    if not preparation_ready:raise TaskError('TASK_IMAGE_RESOURCE_PREPARATION_STEP_INVALID')
    # No speculative reference change may escape an accepted/unknown effect.
    from .image_binding import image_slot_key
    if any(op.capability=='platform.generate_image' and op.status in {'reserved','uncertain','accepted','completed'}
        and any(op.effect_key==image_slot_key(r.id,value.image_item_index) for r in state.requirements if r.artifact_kind=='image')
        for op in state.operations):raise TaskError('TASK_IMAGE_RESOURCE_BINDING_LOCKED')
    bound=Artifact(id='image-resources-'+checksum[:32],kind='text',reference='task-image-resources:'+checksum,
        sha256=checksum,inline_text=text,source_task_id=owner_task_id,delivery_task_id=owner_task_id,
        source_call_id=call_id,step_id=proof_step,validations=('artifact.exists',VALIDATOR))
    return bound,proof


def native_resolver(handler,request,context):
    from services.handlers.chat_image_request import ChatImageInputResolver
    from services.handlers.chat_context.image_sources import legacy_catalog
    parent=handler.db.table('tasks').select('id,user_id,org_id,conversation_id,input_message_id,base_context_revision,request_params').eq('id',request.task_id).single().execute().data
    if (not parent or parent['user_id']!=context.actor_user_id or parent.get('org_id')!=context.org_id
        or parent['conversation_id']!=request.conversation_id):raise PermissionError('TASK_IMAGE_RESOURCE_SCOPE_DENIED')
    owner=SimpleNamespace(db=handler.db,user_id=context.actor_user_id,workspace_user_id=context.workspace_owner_id,
        org_id=context.org_id,conversation_id=request.conversation_id,context_scope=context.context_scope,
        task_id=request.task_id,execution_mode=context.execution_mode,
        resource_manifest=getattr(handler,'_resource_manifest',None),
        resource_access_boundary=getattr(handler,'_resource_access_boundary',None),
        _actor_runtime=getattr(handler,'_actor_runtime',None),
        _tool_runtime=getattr(getattr(handler,'_tool_executor',None),'_tool_runtime',None))
    return ChatImageInputResolver(owner,base_revision=parent['base_context_revision'],
        input_message_id=str(parent['input_message_id']),legacy_sources=legacy_catalog(parent),require_known_sources=True)


def create_resolver(handler,request,context):
    async def resolve(references,*,require_originals):
        def read():
            from services.handlers.chat_context.image_sources import content_parts
            native=native_resolver(handler,request,context)
            current=native._message(native.input_message_id)
            if require_originals and not references and any(p.get('type')=='image' for p in content_parts(current.get('content'))):
                raise ValueError('TASK_IMAGE_ORIGINAL_REFERENCE_REQUIRED')
            return native.resolve(references)
        return await asyncio.to_thread(read)
    async def size_requirement(arguments,references):
        def read():
            from services.handlers.image_size_requirements import resolve_size_requirement
            native=native_resolver(handler,request,context)
            intent,previous=native.size_context()
            return resolve_size_requirement(dict(arguments),references,intent=intent,previous=previous)
        return await asyncio.to_thread(read)
    async def prepare_request(arguments,state,call_id,*,binding_required=True):
        def read():
            from core.config import get_settings
            from core.db_scope import DatabaseScope,DatabaseAccessKind,ScopedDatabaseClient
            from services.agent.image.ecommerce_planner.workflow import binding,KEY
            from services.handlers.chat_image_preparation import prepare_input
            from services.handlers.chat_image_request import validate_single_image_request
            native=native_resolver(handler,request,context)
            parent=handler.db.table('tasks').select('request_params').eq('id',request.task_id).single().execute().data
            workflow=binding((parent.get('request_params') or {}).get(KEY))
            scoped=ScopedDatabaseClient(handler.db,DatabaseScope(context.actor_user_id,context.org_id,DatabaseAccessKind.RUNTIME))
            from services.skills.coordination import Composition
            composition=(parent.get('request_params') or {}).get('_skill_composition')
            composition=Composition.model_validate(composition) if composition is not None else None
            normalized,refs,_target,proof=prepare_input(native,dict(arguments),scoped=scoped,workflow=workflow,settings=get_settings(),composition=composition)
            verify_native_resources(state.model_dump(mode='json'),call_id,arguments,normalized,refs,native=native,
                binding_required=binding_required)
            settings=validate_single_image_request(normalized,len(refs))
            native.verify(refs)
            return {**normalized,**{k:settings[k] for k in ('model','resolution','output_format','aspect_ratio')}},refs,proof
        return await asyncio.to_thread(read)
    resolve.prepare_request=prepare_request
    resolve.size_requirement=size_requirement
    return resolve


def verify_native_resources(raw_state,call_id,arguments,normalized,resolved,*,native=None,binding_required=True):
    from .contracts import TaskState
    from .image_binding import bind_image_call
    from services.tools import ToolCall
    from services.tools.catalog import build_capability_catalog
    state=TaskState.model_validate(raw_state)
    if state.image_binding_version<2:return
    binding=bind_image_call(state,ToolCall(call_id,'generate_image',arguments),build_capability_catalog())
    if state.model_directed:
        import json
        recipe=next((a for a in state.artifacts if a.id==binding.planning_artifact_id
            and a.step_id==binding.step_id and 'image.direct.recipe' in a.validations),None)
        if recipe is None:
            if not binding_required:return
            raise TaskError('TASK_IMAGE_RESOURCE_BINDING_REQUIRED')
        value=json.loads(recipe.inline_text)
        from .direct_loop import image_plan_item
        handoff = value.get('method_handoff')
        if handoff is not None:
            expected = image_plan_item(state, set(), arguments, artifact_id=handoff['artifact_id'])
            if handoff != expected:
                raise TaskError('TASK_IMAGE_ARGUMENTS_NOT_PLANNED')
        # Recipes committed before a plan was saved remain resumable.
        from services.handlers.chat_image_request import validate_single_image_request
        settings=validate_single_image_request(normalized,len(resolved))
        actual={**normalized,**{k:settings[k] for k in ('model','resolution','output_format','aspect_ratio')}}
        if (value.get('arguments_sha256')!=binding.arguments_sha256 or value.get('normalized')!=actual
                or value.get('resolved')!=fingerprint(resolved)):
            raise TaskError('TASK_IMAGE_ORIGINAL_RESOURCE_CHANGED')
        return
    _artifact,document=planning_document(state,binding.planning_artifact_id)
    brief=document.image_plans[binding.item_index-1]
    if state.image_binding_version>=3:
        from .plan_fact_sources import verify_review_sources
        verify_review_sources(state,_artifact,native,resolved)
    if normalized.get('aspect_ratio')!=brief.aspect_ratio:
        raise TaskError('TASK_IMAGE_RESOLVED_SIZE_CHANGED')
    if arguments.get('plan_source') is not None:return  # Original native plan verifies its frozen resources.
    proof=binding_for(state,binding.planning_artifact_id,binding.item_index)
    if proof is None or proof['resolved']!=fingerprint(resolved):
        raise TaskError('TASK_IMAGE_ORIGINAL_RESOURCE_CHANGED')
