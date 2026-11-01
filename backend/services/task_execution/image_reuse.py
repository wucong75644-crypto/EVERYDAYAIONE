"""Exact published-image reuse through the original scoped native resolver."""
from uuid import UUID
from pydantic import Field
from .contracts import Fact,Artifact,ReusedImage,TaskError
from .reducer import digest

class ReuseImageInput(Fact):
    expected_revision: int=Field(strict=True,ge=0)
    arguments: dict
    source_task_id: UUID | None=None


def slots(state):
    return {(a.image_reuse.requirement_id,a.image_reuse.item_index):a for a in state.artifacts
        if a.image_reuse is not None and a.image_reuse.requirements_revision==state.requirements_revision
        and a.step_id in {s.id for s in state.steps}}


def physical(references):
    return [{k:r.get(k) for k in ('workspace_path','content_sha256','file_version','role')} for r in references]


def content(references):
    # A re-materialized cache inode is not a different product photo. Current
    # freshness still uses full physical fingerprints above; recipe equivalence
    # uses the bytes and ordered semantic roles actually sent to the provider.
    return [{k:r.get(k) for k in ('content_sha256','role')} for r in references]


async def reuse(state,value,*,reader,resolver,owner_task_id,call_id='reuse-check'):
    if state.execution_identity.context_scope!='user':raise TaskError('TASK_IMAGE_REUSE_SCOPE_UNSUPPORTED')
    # Explicit regeneration without a stable retry identity means a new image.
    if 'source_task_id' in value.arguments and not value.arguments.get('variant_id'):
        raise TaskError('TASK_IMAGE_REUSE_REGENERATION_REQUEST')
    size=value.arguments.get('size_requirement')
    from .image_binding import bind_image_call
    from .image_resources import binding_for
    from services.tools import ToolCall
    from services.tools.catalog import build_capability_catalog
    from services.tools.argument_validation import validate_model_image_arguments,ToolArgumentValidationError
    catalog=build_capability_catalog()
    try:validate_model_image_arguments(catalog.get('generate_image'),value.arguments)
    except ToolArgumentValidationError:
        raise TaskError('IMAGE_TOOL_ARGUMENTS_INVALID') from None
    binding=bind_image_call(state,ToolCall(call_id,'generate_image',value.arguments),catalog)
    resource=binding_for(state,binding.planning_artifact_id,binding.item_index)
    if state.model_directed:
        import json
        recipe=next((a for a in state.artifacts if a.id==binding.planning_artifact_id and 'image.direct.recipe' in a.validations),None)
        if recipe is None:raise TaskError('TASK_IMAGE_RESOURCE_BINDING_REQUIRED')
        direct=json.loads(recipe.inline_text)
        resource={'mode':direct['normalized']['mode'],'references':value.arguments.get('references',[]),'resolved':direct['resolved']}
    if reader is None or resolver is None:raise TaskError('TASK_IMAGE_REUSE_READER_UNAVAILABLE')
    async def resolve(references,*,require_originals):
        try:return await resolver(references,require_originals=require_originals)
        except (ValueError,PermissionError,OSError):
            raise TaskError('TASK_IMAGE_REUSE_RESOURCE_UNAVAILABLE') from None
    prepare=getattr(resolver,'__dict__',{}).get('prepare_request')
    normalized=value.arguments;plan_proof=None
    if prepare is not None:
        try:normalized,resolved,plan_proof=await prepare(value.arguments,state,call_id)
        except (ValueError,PermissionError,OSError) as error:
            code=str(error)
            raise TaskError(code if code.replace('_','').isalnum() and code.isupper() else 'TASK_IMAGE_REUSE_INPUT_UNAVAILABLE') from None
    else:
        # Pure test adapters / older resolvers cannot attest saved-source semantics.
        if any(k in value.arguments for k in ('plan_source','source_prompt','source_task_id')):
            raise TaskError('TASK_IMAGE_REUSE_NATIVE_PREPARATION_REQUIRED')
        if resource is None:raise TaskError('TASK_IMAGE_RESOURCE_BINDING_REQUIRED')
        resolved=await resolve(resource['references'],require_originals=bool(state.method_bindings)) if resource['references'] else []
        size_resolver=getattr(resolver,'__dict__',{}).get('size_requirement')
        try:
            if size_resolver is not None:normalized,_target=await size_resolver(value.arguments,resolved)
            elif size is not None:
                from services.handlers.image_size_requirements import resolve_size_requirement
                normalized,_target=resolve_size_requirement(value.arguments,resolved)
        except ValueError as error:
            code=str(error)
            raise TaskError(code if code.startswith('IMAGE_') else 'TASK_IMAGE_REUSE_SIZE_UNRESOLVED') from None
    if plan_proof is None:
        if resource is None:raise TaskError('TASK_IMAGE_RESOURCE_BINDING_REQUIRED')
        if physical(resolved)!=physical(resource['resolved']):raise TaskError('TASK_IMAGE_RESOURCE_CHANGED')
    from services.handlers.chat_image_request import validate_single_image_request
    try:validate_single_image_request(normalized,len(resolved))
    except ValueError as error:raise TaskError(str(error)) from None
    if any(op.effect_key==binding.effect_key for op in state.operations):raise TaskError('TASK_IMAGE_SLOT_ALREADY_SUBMITTED')
    rows=await reader.reusable_images(state,source_task_id=value.source_task_id)
    current_steps={s.id for s in state.steps}
    if state.model_directed:
        current_input=next(b.input_sha256 for b in state.call_bindings if b.call_id==call_id)
        current_steps={b.step_id for b in state.call_bindings if b.input_sha256==current_input}
    used={str(a.source_task_id) for a in state.artifacts if a.kind=='image' and a.step_id in current_steps and a.reference.startswith('task-image:')}
    matches=[r for r in rows if r['task_id'] not in used
        and all(key not in normalized or r['request'].get(key)==normalized[key]
            for key in ('resolution','output_format','background','model','source_prompt'))
        and all(key not in value.arguments or r['request'].get(key)==value.arguments[key] for key in ('variant_id','plan_item_id','source_task_id'))
        and r['request']['prompt']==normalized.get('prompt')
        and (plan_proof is None or r['request'].get('origin',{}).get('plan_source')==plan_proof)
        and ('source_task_id' not in value.arguments or r['task_id']!=value.arguments['source_task_id'])
        and r['request']['aspect_ratio']==normalized.get('aspect_ratio')
        and r['request']['mode']==normalized.get('mode') and content(r['request']['references'])==content(resolved)]
    if not matches:raise TaskError('TASK_REUSABLE_IMAGE_NOT_FOUND')
    source=matches[0]
    # Resolve the real generated output under today's policy, not its old URL.
    await resolve([{'message_id':source['message_id'],'content_index':source['content_index'],'role':'reference'}],require_originals=False)
    proof=ReusedImage(requirement_id=binding.requirement_id,item_index=binding.item_index,
        requirements_revision=state.requirements_revision,planning_artifact_id=binding.planning_artifact_id,
        request_sha256=source['request']['request_hash'])
    artifact=Artifact(id='reuse-image-'+digest([str(state.root_task_id),proof.model_dump(mode='json'),source['task_id']])[:32],
        kind='image',reference='task-image:'+source['task_id'],sha256=source['part_sha256'],source_task_id=source['task_id'],
        delivery_task_id=owner_task_id,source_call_id=source['request']['origin']['tool_call_id'],step_id=binding.step_id,image_reuse=proof)
    return artifact,source['part']
