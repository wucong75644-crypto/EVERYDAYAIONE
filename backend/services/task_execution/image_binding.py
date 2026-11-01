"""Bind a real native image call to a completed plan and stable delivery slot.

This does not resolve image files, authorize access, accept a child or bill.
Those checks remain in the native ToolRuntime and ImageHandler.
"""
from dataclasses import dataclass

from pydantic import ValidationError
from services.tools.spec import thaw

from .contracts import TaskError
from .methods import ImageMethodDocument, binding_profile
from .reducer import digest, ready_steps, effect_version


def image_prompt(brief):
    return brief.prompt + '\n\n负面提示词：\n' + brief.negative_prompt


def image_slot_key(requirement_id, index):
    return 'delivery-' + digest([requirement_id, index])


def image_slots(state):
    targets = {target for step in state.steps if step.capability == 'platform.generate_image'
               for target in (*step.requirement_ids, *step.contributes_to)}
    operations = {op.effect_key: op for op in state.operations
        if op.capability == 'platform.generate_image' and op.step_id in {s.id for s in state.steps}}
    multiple_groups=sum(r.artifact_kind=='image' for r in state.requirements)>1
    indices={req.id:sorted(step.image_item_index for step in state.steps
        if step.capability=='platform.generate_image' and req.id in (*step.requirement_ids,*step.contributes_to)
        and step.image_item_index is not None) for req in state.requirements if req.artifact_kind=='image'}
    from .image_reuse import slots as reused_slots
    reused=reused_slots(state)
    return tuple({'requirement_id': req.id, 'item_index': index,
        'status': 'completed' if (req.id,index) in reused else operations[image_slot_key(req.id, index)].status
            if image_slot_key(req.id, index) in operations else 'not_submitted'}
        for req in state.requirements if req.id in targets and req.artifact_kind == 'image'
        for index in (indices[req.id] if multiple_groups else range(1, req.minimum + 1)))


def paid_work_blocked(state):
    paid={'platform.generate_image','platform.image_agent','platform.generate_video','platform.erp_execute',
        'workspace.file.delete','platform.restore_file','platform.agent_reach','platform.manage_scheduled_task','platform.code_execute'}
    return all(s.capability in paid for s in ready_steps(state))


def image_dependencies_waiting(state):
    slots = image_slots(state)
    from .image_sources import has_source_handoff
    current_wait = (bool(managed_image_task(state) and slots) and any(slot['status'] == 'accepted' for slot in slots)
        and all(slot['status'] in {'accepted', 'completed'} for slot in slots)
        and all(step.capability == 'platform.generate_image' for step in ready_steps(state)))
    from .reducer import missing_requirements
    # Superseded requests may already have been accepted. Keep the original
    # continuation consumer responsible for settling them after current work.
    current_ids={s.id for s in state.steps} if state else set()
    # The old accepted image may publish after the new Turn's closed anchor.
    # Keep its original consumer awake while current paid work is fenced;
    # independent planning/read/text work can still finish first.
    blocked_paid=bool(state and paid_work_blocked(state))
    historical_wait=bool(state and state.requirements_history and state.steps and (not missing_requirements(state) or blocked_paid)
        and any(op.capability=='platform.generate_image' and op.status=='accepted' and op.step_id not in current_ids for op in state.operations))
    return current_wait or historical_wait


def managed_image_task(state):
    from .image_sources import has_source_handoff
    return bool(state and (state.image_binding_version>=3 or state.method_bindings or has_source_handoff(state)
        or (state.image_binding_version>=2 and any(r.artifact_kind=='image' for r in state.requirements))))


def validate_image_steps(state, steps):
    requirements = {r.id: r for r in state.requirements if r.artifact_kind == 'image'}
    occupied, global_slots = {}, {}
    multiple_groups = len(requirements)>1
    total=sum(r.minimum for r in requirements.values())
    for step in steps:
        targets = set((*step.requirement_ids, *step.contributes_to)) & requirements.keys()
        if step.image_item_index is not None:
            if step.capability != 'platform.generate_image' or len(targets) != 1:
                raise TaskError('TASK_IMAGE_SLOT_INVALID')
        if step.capability != 'platform.generate_image':
            continue
        if not targets:raise TaskError('TASK_IMAGE_DELIVERY_TARGET_REQUIRED')
        if multiple_groups and (step.image_item_index is None or len(targets)!=1):
            raise TaskError('TASK_IMAGE_GROUP_BINDING_REQUIRED')
        for target in targets:
            requirement = requirements[target]
            if step.image_item_index is not None:
                if (step.image_item_index > (total if multiple_groups else requirement.minimum)
                        or (target in step.requirement_ids and requirement.minimum > 1)):
                    raise TaskError('TASK_IMAGE_SLOT_INVALID')
                indices = (step.image_item_index,)
            else:
                indices = range(1, requirement.minimum + 1)
            for index in indices:
                if (target, index) in occupied:
                    raise TaskError('TASK_IMAGE_SLOT_OVERLAP')
                occupied[target, index] = step.id
                if multiple_groups:
                    if index in global_slots:raise TaskError('TASK_IMAGE_SLOT_OVERLAP')
                    global_slots[index]=target
    if multiple_groups:
        for target in set(global_slots.values()):
            if sum(group==target for group in global_slots.values())!=requirements[target].minimum:
                raise TaskError('TASK_IMAGE_GROUP_BINDING_REQUIRED')


@dataclass(frozen=True)
class ImageCallBinding:
    step_id: str
    requirement_id: str
    item_index: int
    planning_artifact_id: str
    effect_key: str
    effect_version: int
    arguments_sha256: str


def bind_image_call(state, call, registry):
    spec = registry.get(call.name)
    if call.name != 'generate_image' or spec is None or spec.capability != 'platform.generate_image':
        raise TaskError('TASK_IMAGE_NATIVE_TOOL_MISMATCH')
    if state.status != 'active':
        raise TaskError('TASK_NOT_ACTIVE')
    validate_image_steps(state, state.steps)
    # Include artifacts only through completed steps that actually delivered
    # the method requirement. A validation tag on uncommitted text is not enough.
    steps = {step.id: step for step in state.steps}
    documents = {}
    for method in state.method_bindings:
        binding_profile(method)
        for result in state.results:
            if result.step_id not in steps:continue
            step = steps[result.step_id]
            if not set(method.requirement_ids) & set((*step.requirement_ids, *step.contributes_to)):
                continue
            for artifact in state.artifacts:
                if (artifact.id not in result.artifact_ids or artifact.kind != 'text'
                        or method.profile_id not in artifact.validations):
                    continue
                try:
                    documents[artifact.id] = ImageMethodDocument.model_validate_json(artifact.inline_text)
                except (ValidationError, TypeError):
                    raise TaskError('TASK_IMAGE_PLAN_EVIDENCE_INVALID') from None
    from .image_sources import completed_sources,SavedPromptDocument
    committed={key for result in state.results for key in result.artifact_ids}
    for artifact in state.artifacts:
        if artifact.id in committed and artifact.step_id in steps and 'image.plan.valid' in artifact.validations:
            documents[artifact.id]=ImageMethodDocument.model_validate_json(artifact.inline_text)
    for artifact in completed_sources(state):
        try:
            documents[artifact.id]=ImageMethodDocument.model_validate_json(artifact.inline_text)
        except ValidationError:
            documents[artifact.id]=SavedPromptDocument.model_validate_json(artifact.inline_text)
    if not documents:
        raise TaskError('TASK_IMAGE_PLAN_EVIDENCE_MISSING')
    active_bindings={}
    if state.image_binding_version>=3:
        from .plan_facts import review_proof
        documents={key:value for key,value in documents.items() if review_proof(state,next(a for a in state.artifacts if a.id==key)) is not None}
        if not documents:raise TaskError('TASK_PLAN_FACT_REVIEW_REQUIRED')
        from .image_resources import VALIDATOR
        import json
        for proof in state.artifacts:
            if VALIDATOR in proof.validations:
                value=json.loads(proof.inline_text)
                active_bindings[value['image_item_index']]=value['planning_artifact_id']
    requirements = {r.id: r for r in state.requirements if r.artifact_kind == 'image'}
    multiple_groups=len(requirements)>1
    matches = {}
    for step in ready_steps(state):
        if step.capability != spec.capability:
            continue
        targets = set((*step.requirement_ids, *step.contributes_to)) & requirements.keys()
        for target in targets:
            for artifact_id, document in sorted(documents.items()):
                for index, brief in enumerate(document.image_plans, 1):
                    if state.image_binding_version>=3 and getattr(brief,'plan_source',None) is None and active_bindings.get(index)!=artifact_id:
                        continue
                    if ((not multiple_groups and index > requirements[target].minimum)
                            or (step.image_item_index is not None and step.image_item_index != index)):
                        continue
                    if isinstance(document,SavedPromptDocument):
                        proof=brief.source_prompt.model_dump(mode='json')
                        artifact=next(a for a in state.artifacts if a.id==artifact_id)
                        planned=(artifact.reference==f"legacy-message:{proof['message_id']}"
                            and call.arguments.get('prompt')==brief.prompt and call.arguments.get('aspect_ratio')==brief.aspect_ratio
                            and call.arguments.get('plan_item_id')==brief.plan_item_id and thaw(call.arguments.get('source_prompt'))==proof
                            and 'plan_source' not in call.arguments)
                    elif brief.plan_source is not None:
                        proof = brief.plan_source.model_dump(mode='json')
                        artifact = next(a for a in state.artifacts if a.id == artifact_id)
                        planned = (artifact.reference == f"legacy-plan:{proof['plan_id']}:{proof['revision']}"
                            and thaw(call.arguments) == {'plan_source': proof})
                    else:
                        planned = (call.arguments.get('prompt') == image_prompt(brief)
                            and call.arguments.get('aspect_ratio') == brief.aspect_ratio and 'plan_source' not in call.arguments)
                    from .image_reuse import slots as reused_slots
                    if planned and (target,index) not in reused_slots(state):
                        if state.image_binding_version>=2 and getattr(brief,'plan_source',None) is None:
                            from .image_resources import binding_for
                            resource=binding_for(state,artifact_id,index)
                            if resource is None:raise TaskError('TASK_IMAGE_RESOURCE_BINDING_REQUIRED')
                            if (call.arguments.get('mode')!=resource['mode']
                                    or thaw(call.arguments.get('references',[]))!=resource['references']):
                                raise TaskError('TASK_IMAGE_RESOURCES_NOT_PLANNED')
                        matches.setdefault((step.id, target, index), artifact_id)
    if len(matches) != 1:
        raise TaskError('TASK_IMAGE_BINDING_AMBIGUOUS' if matches else 'TASK_IMAGE_ARGUMENTS_NOT_PLANNED')
    (step_id, requirement_id, index), artifact_id = next(iter(matches.items()))
    # Neither tool/call IDs nor model step names create new delivery rights.
    # The original plan index is stable even when another unexecuted step is
    # moved. A rank computed from mutable neighboring steps is not an identity.
    key = image_slot_key(requirement_id, index)
    return ImageCallBinding(step_id, requirement_id, index, artifact_id, key,
        effect_version(state,key), digest(thaw(call.arguments)))
