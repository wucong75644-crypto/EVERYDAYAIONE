"""Reviewed method phases and their actual structured handoff evidence."""
from __future__ import annotations

import json
from uuid import UUID
from pydantic import ConfigDict, Field, ValidationError

from .contracts import CapabilityGate, Fact, MethodBinding, Requirement, TaskError, TaskState
from .reducer import missing_requirements


# Exact original published revisions, previously read and pinned in the legacy
# compatibility inventory. A name alone never opts a method into phase semantics.
PROFILES = {
    ('725d179e-a6fe-4282-9e8a-83876c336ff4', 'reference-image-prompts',
     'vf540a7eb0eff4862b924d9af0f7032bd',
     'dd85988f88e96ae446ff5588866b0d2641113d96b9a73bae332c947606e6e90e'):
        ('reference-prompts-v1', True),
    ('4c7d133e-33e5-455a-9216-4b6cff0ce892', 'ecommerce-image-prompts',
     'v5d3a01973e41404981324860769e3750',
     '7f8e8de78b18105a2456a327e8bb9ee38edee9ed5d1ddae48a2e0a2c634df69e'):
        ('ecommerce-image-plan-v1', False),
}


class MethodDocumentFact(Fact):
    model_config = ConfigDict(str_strip_whitespace=True)


class PlanSource(Fact):
    plan_id: UUID
    revision: int = Field(strict=True, ge=1)
    item_id: UUID


class ImageBrief(MethodDocumentFact):
    title: str = Field(min_length=1, max_length=300)
    layout: str = Field(min_length=1, max_length=4000)
    prompt: str = Field(min_length=1, max_length=12000)
    negative_prompt: str = Field(min_length=1, max_length=4000)
    aspect_ratio: str = Field(pattern=r'^[1-9]\d?:[1-9]\d?$')
    plan_source: PlanSource | None = None


class ImageMethodDocument(MethodDocumentFact):
    reference_analysis: str = Field(min_length=1, max_length=8000)
    product_facts: str = Field(min_length=1, max_length=8000)
    visual_direction: str = Field(min_length=1, max_length=8000)
    page_continuity: str = Field(min_length=1, max_length=8000)
    image_plans: tuple[ImageBrief, ...] = Field(min_length=1, max_length=100)


OUTPUT_PROFILES = frozenset({'reference-prompts-v1', 'ecommerce-image-plan-v1'})


def profile_for(candidate, active):
    declaration = candidate.catalog_metadata.task_stage
    if declaration is not None:
        return declaration.output_profile, declaration.restriction_scope == 'phase', 'declared'
    profile = PROFILES.get((str(candidate.package_id), candidate.skill_key, active.revision, active.body_sha256))
    return (*profile, 'reviewed') if profile is not None else None


def binding_profile(binding):
    if binding.contract_source == 'declared':
        if binding.profile_id not in OUTPUT_PROFILES:
            raise TaskError('TASK_METHOD_PROFILE_UNAVAILABLE')
        return binding.profile_id, binding.retire_on_complete
    key = (str(binding.package_id), binding.skill_key, binding.revision, binding.body_sha256)
    profile = PROFILES.get(key)
    if profile is None or profile != (binding.profile_id, binding.retire_on_complete):
        raise TaskError('TASK_METHOD_PROFILE_UNAVAILABLE')
    return profile


def validate_proposed_method_intent(proposal,skills):
    """A direct-source execution cannot also request a fresh planning phase.

    This checks protocol consistency, not user keywords. The model can repair
    its proposal before any goal/host prerequisite is frozen.
    """
    if proposal.action not in {'execute_ready','execute_prompt'} or skills is None:return
    for key in proposal.applicable_method_ids:
        active=skills.active.get(key)
        candidate=skills.directory.get(key)
        if active is not None and candidate is not None and profile_for(candidate,active) is not None:
            raise TaskError('TASK_INTENT_METHOD_CONFLICT')


def attach_methods(state, skills, *, activated_skill_id=None):
    if state.model_directed:return state  # Original SkillRuntime owns method bodies and ceilings.
    if skills is None:
        if state.applicable_method_ids:
            raise TaskError('TASK_METHOD_RUNTIME_UNAVAILABLE')
        return state
    existing = {binding.skill_key for binding in state.method_bindings}
    if not set(state.applicable_method_ids) <= set(skills.active) | existing:
        raise TaskError('TASK_METHOD_NOT_ACTIVE')
    requirements, gates, bindings = list(state.requirements), list(state.capability_gates), list(state.method_bindings)
    applicable = set(state.applicable_method_ids)
    count = sum(r.minimum for r in state.requirements if r.artifact_kind == 'image')
    content_count = max((r.item_count or 0 for r in state.requirements if r.artifact_kind in {'text', 'file'}), default=0)
    for key, active in skills.active.items():
        if key in existing:
            continue
        candidate = skills.directory[key]
        profile = profile_for(candidate, active)
        if profile is None:
            continue
        # Execute-existing tasks get a separate host source gate at bootstrap.
        # They cannot dispatch until a real prior source is imported/completed.
        # Explicitly applicable methods retain their original obligations.
        existing_execution = state.intent_action in {'execute_ready','execute_prompt'} and key not in applicable and key != activated_skill_id
        mandatory = bool(count) and not existing_execution
        if not mandatory and (state.intent_action != 'plan' or
                              (key not in applicable and key != activated_skill_id)):
            continue
        applicable.add(key)
        profile_id, retire, contract_source = profile
        rid = ('r'+str(state.requirements_revision)+'.' if state.requirements_history else '') + 'method.' + key + '.planning'
        requirement = Requirement(id=rid, source_message_id=state.source_message_ids[0],
            description='完成所选方法的参考/商品分析、整套视觉与逐图完整提示词规划',
            artifact_kind='text', validator=profile_id)
        requirements.append(requirement)
        bindings.append(MethodBinding(profile_id=profile_id, package_id=candidate.package_id,
            skill_key=key, revision=active.revision, body_sha256=active.body_sha256,
            requirement_ids=(rid,), image_count=count or content_count or (6 if profile_id == 'reference-prompts-v1' else 1),
            retire_on_complete=retire, contract_source=contract_source))
        gates.extend(CapabilityGate(capability=capability, requirement_ids=(rid,))
                     for capability in ('platform.generate_image', 'platform.image_agent'))
    if len(bindings) == len(state.method_bindings):
        return state
    return TaskState.model_validate({**state.model_dump(), 'requirements': requirements,
        'capability_gates': gates, 'method_bindings': bindings,
        'applicable_method_ids': tuple(sorted(applicable))})


def append_active_methods(state, skills, *, activated_skill_id=None):
    """Trusted activation path only: add constraints, never revise user goals."""
    if state.status != 'active':
        raise TaskError('TASK_NOT_ACTIVE')
    candidate = attach_methods(state, skills, activated_skill_id=activated_skill_id)
    if candidate is state:
        return state
    return TaskState.model_validate({**candidate.model_dump(), 'revision': state.revision + 1})


def validate_method_text(state, step_id, text):
    """Structural evidence only. It is not a semantic/visual quality guarantee."""
    step = next(s for s in state.steps if s.id == step_id)
    applies = [b for b in state.method_bindings
               if set(b.requirement_ids) & set((*step.requirement_ids, *step.contributes_to))]
    generic=any(r.validator=='image.plan.valid' and r.id in (*step.requirement_ids,*step.contributes_to) for r in state.requirements)
    if not applies and not generic:
        return ()
    try:
        document = ImageMethodDocument.model_validate_json(text)
    except ValidationError:
        return ()
    passed = []
    distinct = len({tuple(' '.join(value.split()) for value in (p.layout, p.prompt, p.negative_prompt))
                    for p in document.image_plans})
    if generic and distinct>=sum(r.minimum for r in state.requirements if r.artifact_kind=='image'):
        passed.append('image.plan.valid')
    for binding in applies:
        binding_profile(binding)
        if distinct >= binding.image_count:
            passed.append(binding.profile_id)
    return tuple(passed)


def method_context(state):
    if not state.method_bindings and not any(r.id=='host.image_plan' for r in state.requirements):
        return None
    for binding in state.method_bindings:
        binding_profile(binding)
    return {'role': 'system', 'content': '[Task method handoff]\n'
        '先完成所选方法的完整规划，再执行后续图片工具。已有规划/完整提示词时调用find_task_text、reuse_task_text复用，'
        '不重写已完成内容；没有可复用成果时，将真实规划作为JSON文本交给save_task_text保存；'
        '完成该步骤后才能生图。这里检查结构与数量，不能以空泛字段代替专业内容。\n'
        '新规划在complete_task_step时由宿主核验用户原文、原图和商品事实。fact_review_pending不代表完成步骤或允许生图；'
        '核验指出未证实的规格/材质/单位时，在原未完成步骤保存修订稿再完成，保留正确内容与用户目标。'
        '不知道的商品事实保留未知，不凭外观推断防水/材质，不把数量单位改为尺寸。\n'
        '若plan_ecommerce_images已返回ready，使用其plan_id/revision调用import_task_source(kind=ecom_plan)'
        '读取服务器真实三阶段成果；不要再另写规划或把plan_source包装为新提示词。\n'
        '实际生图prompt须保留逐图prompt原文，然后追加两个换行、负面提示词：、换行和negative_prompt原文；'
        'aspect_ratio按对应逐图规划填写。拆分逐图步骤时用image_item_index标明规划序号并contributes_to图片交付要求。\n'
        '规划完成后先调用bind_task_image_sources逐张绑定真实原图定位与顺序，再按绑定的mode/references生图。'
        '绑定是执行前控制动作，不是独立产图步骤，不重复占用同一图片交付要求和image_item_index。'
        '原ready方案的plan_source已有原生资源绑定，直接沿用，不重绑。\n'
        '使用已规划比例时传size_requirement={"mode":"explicit"}；原生工具仍会优先核对用户明确的尺寸要求，'
        '不能用参数覆盖用户要求。\n'
        + json.dumps({'methods': [b.model_dump(mode='json') for b in state.method_bindings],
                      'document_schema': ImageMethodDocument.model_json_schema()}, ensure_ascii=False)}


def artifact_display(state, artifact):
    """Show complete planning content; retain canonical structured evidence."""
    if ('image.plan.valid' not in artifact.validations
            and not any(b.profile_id in artifact.validations for b in state.method_bindings)):
        return artifact.inline_text
    document = ImageMethodDocument.model_validate_json(artifact.inline_text)
    sections = ['## 参考图判断\n' + document.reference_analysis,
                '## 商品分析\n' + document.product_facts,
                '## 整套视觉定位\n' + document.visual_direction,
                '## 页面衔接\n' + document.page_continuity]
    for index, brief in enumerate(document.image_plans, 1):
        sections.append(f'## 第{index}张：{brief.title}\n\n构图与内容：{brief.layout}\n\n'
            f'完整提示词：\n{brief.prompt}\n\n动态负面提示词：\n{brief.negative_prompt}\n\n'
            f'画面比例：{brief.aspect_ratio}')
    return '\n\n'.join(sections)


def completed_phase_bindings(state):
    missing = set(missing_requirements(state))
    result = []
    for binding in state.method_bindings:
        binding_profile(binding)
        if binding.retire_on_complete and not (set(binding.requirement_ids) & missing):
            result.append(binding)
    return tuple(result)


def direct_phase_binding(state, candidate):
    if not state.model_directed:
        return None
    completed = {artifact_id for result in state.results for artifact_id in result.artifact_ids}
    return next((a.image_method for a in reversed(state.artifacts)
        if a.id in completed and a.image_method is not None and a.image_method.retire_on_complete
        and a.image_method.skill_key == candidate.skill_key), None)


def source_phase_binding(state, candidate, active):
    from .image_sources import SOURCE_REQUIREMENT, has_source_handoff
    if (not has_source_handoff(state) or candidate.skill_key in state.applicable_method_ids
            or any(b.skill_key==candidate.skill_key for b in state.method_bindings)):
        return None
    profile=profile_for(candidate,active)
    if profile is None or not profile[1]:
        return None
    return MethodBinding(profile_id=profile[0],package_id=candidate.package_id,skill_key=candidate.skill_key,
        revision=active.revision,body_sha256=active.body_sha256,requirement_ids=tuple(r.id for r in state.requirements if r.validator=='image.source.valid'),
        image_count=sum(r.minimum for r in state.requirements if r.artifact_kind=='image'),
        retire_on_complete=True,contract_source=profile[2])


def retire_completed_methods(skills, state):
    """Stage a reviewed phase exit, retaining manual/session selection identity.

    Only the completed phase ceases participating. Every remaining method and
    the original host ceiling still constrain the next stage. The caller must
    checkpoint this projection atomically with TaskState and the tool receipt.
    """
    if skills is None:
        return None
    retiring = [b for b in completed_phase_bindings(state) if b.skill_key in skills.active]
    known={b.skill_key for b in retiring}
    for key,active in skills.active.items():
        proof=source_phase_binding(state,skills.directory[key],active)
        if key not in known and proof is not None:
            retiring.append(proof)
    return retire_bound_methods(skills, state, retiring)


def retire_bound_methods(skills, state, retiring):
    if not retiring:
        return skills
    if skills.execution_mode != 'interactive' or skills.checkpoint_version != 2:
        raise TaskError('TASK_METHOD_PHASE_UNSUPPORTED')
    staged = skills.staged()
    staged.task_execution_state = state
    # Retain frozen bodies as dependencies, while removing only the completed
    # phase's action ownership and tool ceiling. They remain live-authorized.
    for binding in retiring:
        active = staged.active[binding.skill_key]
        candidate = staged.directory[binding.skill_key]
        if (str(candidate.package_id), active.revision, active.body_sha256) != (
                str(binding.package_id), binding.revision, binding.body_sha256):
            raise TaskError('TASK_METHOD_BINDING_CHANGED')
        del staged.active[binding.skill_key]
        staged.resident[binding.skill_key] = active
    from services.skills.resolver import skill_tool_ceiling
    from services.skills.workflow_contracts import required_protocol_tools
    ceiling = staged._initial_ceiling
    for key, active in list(staged.active.items()):
        declared = skill_tool_ceiling(staged.directory[key].catalog_metadata,
            staged.platform_tool_names, staged._initial_ceiling,
            available_tool_names=staged.available_tool_names)
        staged.active[key] = active.model_copy(update={'effective_allowed_tool_names': declared})
        ceiling &= declared
    composition = staged._compose(tools=ceiling)
    if not required_protocol_tools(composition) <= ceiling:
        raise TaskError('TASK_METHOD_CAPABILITY_UNAVAILABLE')
    staged.effective_allowed_tool_names, staged.composition = ceiling, composition
    return staged


async def validate_method_replay(state, skills):
    """Retired method bodies still require original pinned scope/hash checks."""
    if not state.method_bindings:
        return
    if skills is None:
        raise TaskError('TASK_METHOD_RUNTIME_UNAVAILABLE')
    completed = {b.skill_key for b in completed_phase_bindings(state)}
    for binding in state.method_bindings:
        binding_profile(binding)
        candidate = skills.directory.get(binding.skill_key)
        if (candidate is None or str(candidate.package_id) != str(binding.package_id)
                or candidate.revision != binding.revision):
            raise TaskError('TASK_METHOD_BINDING_CHANGED')
        validated = await skills.source.load(candidate, restoring=True)
        skills._check_cancelled()
        skills._validate_identity(candidate, validated)
        if validated.body_sha256 != binding.body_sha256:
            raise TaskError('TASK_METHOD_BINDING_CHANGED')
        expected = profile_for(candidate, validated)
        if expected != (binding.profile_id, binding.retire_on_complete, binding.contract_source):
            raise TaskError('TASK_METHOD_BINDING_CHANGED')
        if binding.skill_key not in skills.active and binding.skill_key not in completed:
            raise TaskError('TASK_METHOD_PHASE_INCOMPLETE')


def blocked_tool_names(state, registry, skills=None):
    """Prerequisites from actual registered capabilities, never model step labels."""
    missing = set(missing_requirements(state)) if state is not None else set()
    blocked = {gate.capability for gate in state.capability_gates if missing & set(gate.requirement_ids)} if state is not None else set()
    if skills is not None:
        bound = {b.skill_key for b in state.method_bindings} if state is not None else set()
        for key, active in skills.active.items():
            candidate = skills.directory[key]
            from .image_sources import has_source_handoff
            if key not in bound and profile_for(candidate, active) is not None and not has_source_handoff(state):
                blocked.update(('platform.generate_image', 'platform.image_agent'))
    return frozenset(spec.name for spec in registry.specs() if spec.capability in blocked)


def reopen_revision_phases(skills, applicable_method_ids=()):
    """Reinstate original pinned phase bodies and their original ceilings."""
    if not skills.resident:return skills
    from services.skills.resolver import skill_tool_ceiling
    from services.skills.workflow_contracts import required_protocol_tools
    staged=skills.staged()
    selected=set(applicable_method_ids) | {k for k in (skills.manual_skill_id,*skills.session_skill_ids) if k is not None}
    reopening={key for key in staged.resident if key in selected}
    if len(staged.active)+len(reopening)>4:raise TaskError('TASK_REVISION_PHASE_REPAIR_REQUIRED')
    for key in reopening:
        active=staged.resident[key]
        staged.active[key]=active
        del staged.resident[key]
    ceiling=staged._initial_ceiling
    for key,active in list(staged.active.items()):
        declared=skill_tool_ceiling(staged.directory[key].catalog_metadata,staged.platform_tool_names,
            staged._initial_ceiling,available_tool_names=staged.available_tool_names)
        staged.active[key]=active.model_copy(update={'effective_allowed_tool_names':declared})
        ceiling &= declared
    staged.effective_allowed_tool_names=ceiling
    staged.composition=staged._compose(tools=ceiling)
    if not required_protocol_tools(staged.composition)<=ceiling:raise TaskError('TASK_METHOD_CAPABILITY_UNAVAILABLE')
    return staged
