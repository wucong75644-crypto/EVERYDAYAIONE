"""Phase permission boundaries use real SkillRuntime and staged Actor saves.

Local source fixtures exercise generic policy mechanics; they are not new
production Skills and do not replace original-revision live acceptance.
"""
import json
from types import SimpleNamespace

import pytest

from services.skills.selection import SkillSelection
from services.task_execution import methods
from services.task_execution.contracts import PlanStep, WorkPlan, TaskError
from services.task_execution.reducer import revise_plan, complete_step, register_artifacts
from tests.test_skill_runtime import Source, item, state as skill_state, activate
from tests.test_task_execution_bootstrap import bootstrap, proposal
from tests.test_task_execution_model_bridge import setup_case, execute, call


async def bound(monkeypatch, *, global_limit=False, retire=True):
    candidates = [item(tools=())]
    if global_limit:
        candidates.append(item('global', tools=('file_search',)))
    source = Source(candidates, body='测试来源：完成规划阶段。')
    skills = skill_state(source, platform_tool_names={'generate_image', 'file_search'},
                        authorized_tool_names={'generate_image', 'file_search'})
    await skills.initialize()
    skills.manual_skill_id = 'report'
    skills.manual_selection = SkillSelection(skill_id='report', revision='v1')
    assert (await skills.activate(activate(), manual=True))['ok']
    if global_limit:
        assert (await skills.activate(activate('global')))['ok']
    active, candidate = skills.active['report'], skills.directory['report']
    identity = (str(candidate.package_id), 'report', 'v1', active.body_sha256)
    monkeypatch.setitem(methods.PROFILES, identity, ('reference-prompts-v1', retire))
    source_input, *_ = bootstrap()
    state = methods.attach_methods(source_input.initialize({**proposal(), 'action': 'plan',
        'applicable_method_ids': ['report']}, model_rounds_used=1), skills)
    state = revise_plan(state, WorkPlan(expected_revision=state.revision, steps=(
        PlanStep(id='planning', title='完整提示词规划', requirement_ids=(
            'user.prompts', state.method_bindings[0].requirement_ids[0])),
        PlanStep(id='images', title='五张详情图', depends_on=('planning',),
            requirement_ids=('user.images',), capability='platform.generate_image'),
    )))
    return state, skills


def document(count=5):
    return json.dumps({'reference_analysis': '参考构图、桌面场景和产品一致性',
        'product_facts': '蓝色荔枝与粉色蜜桃风琴包，来自当前商品图片',
        'visual_direction': '统一自然光、低饱和桌面背景，突出产品',
        'page_continuity': '整体展示、容量、分层、材质和使用场景依次衔接',
        'image_plans': [{'title': f'第{i+1}张', 'layout': f'第{i+1}张的构图',
            'prompt': f'保留真实产品外观，设计第{i+1}张详情图完整画面',
            'negative_prompt': '不改变产品花纹，不增添未知规格', 'aspect_ratio': '3:4'}
            for i in range(count)]}, ensure_ascii=False)


async def saved_plan(monkeypatch, **kwargs):
    state, skills = await bound(monkeypatch, **kwargs)
    runtime, prepared, handler, sink = setup_case(initial=state)
    runtime.skill_runtime = skills
    blocks = []
    await execute(runtime, prepared, handler, sink, [call('save_task_text', {
        'expected_revision': state.revision, 'step_id': 'planning', 'text': document()}, 'plan-text')], blocks)
    return runtime, prepared, handler, sink, blocks


@pytest.mark.asyncio
async def test_direct_source_execution_and_fresh_planning_method_are_repaired_before_freeze(monkeypatch):
    from services.task_execution.methods import validate_proposed_method_intent
    from services.task_execution.bootstrap import StartTaskInput
    _,skills=await bound(monkeypatch)
    for action in ('execute_ready','execute_prompt'):
        proposal=StartTaskInput.model_validate({'action':action,'applicable_method_ids':['report'],
            'requirements':[{'id':'images','description':'五张图','artifact_kind':'image','minimum':5}]})
        with pytest.raises(TaskError,match='INTENT_METHOD_CONFLICT'):
            validate_proposed_method_intent(proposal,skills)
        validate_proposed_method_intent(proposal.model_copy(update={'applicable_method_ids':()}),skills)
        validate_proposed_method_intent(proposal.model_copy(update={'action':'plan'}),skills)


@pytest.mark.asyncio
async def test_reviewed_five_prompt_collection_stays_five_through_initialization_and_method_validation(monkeypatch):
    from services.task_execution.bootstrap import StartTaskInput
    from services.task_execution.requirement_extraction import RequirementObservation,compare_observation
    from services.task_execution.contracts import PlanStep,WorkPlan
    from types import SimpleNamespace
    _,skills=await bound(monkeypatch)
    goal='请给五条完整提示词，不要生图'
    proposal=StartTaskInput(action='plan',applicable_method_ids=('report',),requirements=({
        'id':'prompts','description':'五条完整提示词','artifact_kind':'text','minimum':1,'item_count':5,
        'evidence':['五条完整提示词']},))
    observed=RequirementObservation(action='plan',deliveries=({'artifact_kind':'text','minimum':1,'item_count':5,
        'evidence':['五条完整提示词']},))
    assert compare_observation(SimpleNamespace(original_goal=goal),proposal,observed).coverage=='covered'
    boot,*_=bootstrap()
    state=methods.attach_methods(boot.initialize(proposal.model_dump(mode='json'),model_rounds_used=2),skills)
    assert state.method_bindings[0].image_count==5
    state=revise_plan(state,WorkPlan(expected_revision=state.revision,steps=(PlanStep(id='planning',title='五条提示词',
        requirement_ids=('user.prompts',*state.method_bindings[0].requirement_ids)),)))
    assert methods.validate_method_text(state,'planning',document(5))==('reference-prompts-v1',)
    assert methods.validate_method_text(state,'planning',document(4))==()


@pytest.mark.asyncio
async def test_incomplete_method_gate_uses_registered_tool_capability(monkeypatch):
    from services.tools.catalog import build_capability_catalog
    state, skills = await bound(monkeypatch)
    assert 'generate_image' in methods.blocked_tool_names(state, build_capability_catalog())
    assert state.method_bindings[0].image_count == 5
    assert not methods.validate_method_text(state, 'planning', '我已完成全部提示词')
    assert not methods.validate_method_text(state, 'planning', document(4))
    assert methods.validate_method_text(state, 'planning', document()) == ('reference-prompts-v1',)
    raw = json.loads(document())
    raw['image_plans'] = [raw['image_plans'][0]] * 5
    assert not methods.validate_method_text(state, 'planning', json.dumps(raw))
    raw = json.loads(document())
    for brief in raw['image_plans']:
        brief['aspect_ratio'] = '0:3'
    assert not methods.validate_method_text(state, 'planning', json.dumps(raw))
    raw = json.loads(document())
    for key in ('reference_analysis', 'product_facts', 'visual_direction', 'page_continuity'):
        raw[key] = ' '
    for index, brief in enumerate(raw['image_plans']):
        brief.update(title=' ', layout=' ' * (index+1), prompt=' ', negative_prompt=' ', aspect_ratio='0:0')
    assert not methods.validate_method_text(state, 'planning', json.dumps(raw))


@pytest.mark.asyncio
async def test_full_plan_is_displayed_as_readable_content_while_evidence_stays_structured(monkeypatch):
    runtime, prepared, handler, sink, blocks = await saved_plan(monkeypatch)
    artifact = runtime.task_execution_state.artifacts[0]
    assert json.loads(artifact.inline_text)['image_plans'][0]['aspect_ratio'] == '3:4'
    displayed = next(b for b in blocks if b.get('task_artifact_id') == artifact.id)
    assert displayed['text'].count('完整提示词：') == 5
    assert displayed['text'].count('动态负面提示词：') == 5
    assert '整套视觉定位' in displayed['text'] and '页面衔接' in displayed['text']
    assert '"image_plans"' not in displayed['text']


@pytest.mark.asyncio
@pytest.mark.parametrize('global_limit', [False, True])
async def test_completed_phase_retires_only_phase_and_preserves_global_and_selection(monkeypatch, global_limit):
    runtime, prepared, handler, sink, blocks = await saved_plan(monkeypatch, global_limit=global_limit)
    assert runtime.skill_runtime.effective_allowed_tool_names == frozenset()
    artifact = runtime.task_execution_state.artifacts[0]
    await execute(runtime, prepared, handler, sink, [call('complete_task_step', {
        'expected_revision': runtime.task_execution_state.revision, 'step_id': 'planning',
        'artifact_ids': [artifact.id]}, 'complete')], blocks)
    skills = runtime.skill_runtime
    assert skills.manual_skill_id == 'report' and 'report' not in skills.active
    expected = {'file_search'} if global_limit else {'file_search', 'generate_image'}
    assert skills.effective_allowed_tool_names == expected
    payload = runtime._replay_checkpoint_callback.call_args.args[1]
    assert payload['_task_execution']['results'][0]['step_id'] == 'planning'
    assert payload['skill_runtime']['manual_skill_id'] == 'report'
    assert 'report' not in {a['skill_key'] for a in payload['skill_runtime']['active']}
    # The actual old v2 restore contract can replay this phase projection, while
    # the task adapter revalidates even the retired method's original source.
    restored = skill_state(skills.source, task_execution_state=runtime.task_execution_state, platform_tool_names={'generate_image', 'file_search'},
        authorized_tool_names={'generate_image', 'file_search'})
    await restored.initialize(skills.checkpoint())
    await methods.validate_method_replay(runtime.task_execution_state, restored)
    assert restored.effective_allowed_tool_names == expected


@pytest.mark.asyncio
async def test_failed_phase_checkpoint_restores_original_method_and_task(monkeypatch):
    runtime, prepared, handler, sink, blocks = await saved_plan(monkeypatch)
    original, skills = runtime.task_execution_state, runtime.skill_runtime
    async def fail(*args):
        return {'outcome': 'disabled'}
    runtime._replay_checkpoint_callback = fail
    with pytest.raises(RuntimeError, match='NOT_SAVED'):
        await execute(runtime, prepared, handler, sink, [call('complete_task_step', {
            'expected_revision': original.revision, 'step_id': 'planning',
            'artifact_ids': [original.artifacts[0].id]}, 'complete')], blocks)
    assert runtime.task_execution_state == original and runtime.skill_runtime is skills
    assert 'report' in skills.active and skills.effective_allowed_tool_names == frozenset()


@pytest.mark.asyncio
async def test_host_narrowing_during_empty_phase_survives_retirement_and_restore(monkeypatch):
    runtime, prepared, handler, sink, blocks = await saved_plan(monkeypatch)
    runtime.skill_runtime.restrict_tools({'file_search'})
    original = runtime.task_execution_state
    await execute(runtime, prepared, handler, sink, [call('complete_task_step', {
        'expected_revision': original.revision, 'step_id': 'planning',
        'artifact_ids': [original.artifacts[0].id]}, 'complete')], blocks)
    assert runtime.skill_runtime.effective_allowed_tool_names == {'file_search'}
    checkpoint = runtime.skill_runtime.checkpoint()
    assert checkpoint['host_allowed_tool_names'] == ['file_search']
    restored = skill_state(runtime.skill_runtime.source, task_execution_state=runtime.task_execution_state,
        platform_tool_names={'generate_image', 'file_search'},
        authorized_tool_names={'generate_image', 'file_search'})
    await restored.initialize(checkpoint)
    await methods.validate_method_replay(runtime.task_execution_state, restored)
    assert restored.effective_allowed_tool_names == {'file_search'}


@pytest.mark.asyncio
async def test_premature_phase_removal_is_rejected_on_replay(monkeypatch):
    state, skills = await bound(monkeypatch)
    skills.active.clear()
    with pytest.raises(TaskError, match='PHASE_INCOMPLETE'):
        await methods.validate_method_replay(state, skills)


@pytest.mark.asyncio
async def test_unregistered_revision_cannot_receive_phase_semantics(monkeypatch):
    state, skills = await bound(monkeypatch)
    monkeypatch.setattr(methods, 'PROFILES', {})
    with pytest.raises(TaskError, match='PROFILE_UNAVAILABLE'):
        methods.retire_completed_methods(skills, state)


@pytest.mark.asyncio
@pytest.mark.parametrize('action', ['other', 'execute_ready', 'execute_prompt', 'explain'])
async def test_loaded_method_does_not_force_planning_for_another_action(monkeypatch, action):
    state, skills = await bound(monkeypatch)
    source, *_ = bootstrap()
    initial = source.initialize({'requirements': [{'id': 'explanation',
        'description': '解释当前流程', 'artifact_kind': 'text'}], 'action': action,
        'applicable_method_ids': ['report']}, model_rounds_used=1)
    result = methods.attach_methods(initial, skills)
    assert result.method_bindings == () and result.capability_gates == ()
    assert result.requirements == initial.requirements


@pytest.mark.asyncio
@pytest.mark.parametrize('action', ['other', 'execute_prompt'])
async def test_image_delivery_cannot_waive_selected_method_by_omitting_intent(monkeypatch, action):
    from services.tools.catalog import build_capability_catalog
    state, skills = await bound(monkeypatch, retire=False)
    source, *_ = bootstrap()
    initial = source.initialize({**proposal(), 'action': action}, model_rounds_used=1)
    result = methods.attach_methods(initial, skills)
    if action=='execute_prompt':
        from services.task_execution.image_sources import SOURCE_REQUIREMENT
        assert not result.method_bindings and any(r.id==SOURCE_REQUIREMENT for r in result.requirements)
    else:
        assert result.method_bindings and result.method_bindings[0].image_count == 5
    assert 'generate_image' in methods.blocked_tool_names(result, build_capability_catalog(), skills)


@pytest.mark.asyncio
async def test_automatically_activated_registered_method_keeps_prerequisites(monkeypatch):
    state, skills = await bound(monkeypatch)
    skills.manual_skill_id = None
    source, *_ = bootstrap()
    initialized = source.initialize(proposal(), model_rounds_used=1)
    result = methods.attach_methods(initialized, skills)
    assert result.method_bindings and result.method_bindings[0].image_count == 5


@pytest.mark.asyncio
async def test_five_prompts_in_one_text_artifact_preserves_requested_item_count(monkeypatch):
    state, skills = await bound(monkeypatch)
    source, *_ = bootstrap()
    initial = source.initialize({'action': 'plan', 'applicable_method_ids': ['report'],
        'requirements': [{'id': 'prompts', 'description': '一份规划，包含五条完整提示词',
                          'artifact_kind': 'text', 'minimum': 1, 'item_count': 5}]}, model_rounds_used=1)
    result = methods.attach_methods(initial, skills)
    assert result.requirements[0].minimum == 1 and result.requirements[0].item_count == 5
    assert result.method_bindings[0].image_count == 5  # default six must not override explicit five


@pytest.mark.asyncio
async def test_file_report_does_not_inherit_an_unrelated_fixed_image_method(monkeypatch):
    state, skills = await bound(monkeypatch)
    source, *_ = bootstrap()
    initial = source.initialize({'action': 'plan', 'requirements': [
        {'id': 'report', 'description': '交付文件报告', 'artifact_kind': 'file'}]}, model_rounds_used=1)
    result = methods.attach_methods(initial, skills)
    assert result.method_bindings == () and len(result.requirements) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('before_initialization', [False, True])
async def test_actual_dispatch_rejects_premature_image_even_if_model_ignores_advertisement(monkeypatch, before_initialization):
    from unittest.mock import AsyncMock
    from services.handlers.chat.execution_engine import _execute_tools
    from services.tools.runtime_context import catalog_context
    state, skills = await bound(monkeypatch)
    runtime, prepared, handler, sink = setup_case(initial=state)
    if before_initialization:
        runtime.task_execution_state = None
        runtime.task_bootstrap, *_ = bootstrap()
        runtime.skill_runtime = skills
    prepared.execution_context = catalog_context(None)
    prepared.permission = SimpleNamespace(mode=SimpleNamespace(value='auto'))
    prepared.budget = SimpleNamespace()
    prepared.tool_context = SimpleNamespace(update_from_batch=lambda _: None,
        build_context_prompt=lambda: None, discovered_tools=set())
    handler._get_conv_source = lambda _: 'web'
    handler._pending_emit_payloads = []
    request = SimpleNamespace(task_id=runtime.task_id, conversation_id=runtime.conversation_id,
        message_id='output', user_id=str(state.actor_user_id))
    monkeypatch.setattr('services.handlers.chat.execution_engine.compact_tool_context', AsyncMock())
    blocks = []
    await _execute_tools(handler=handler, request=request, prepared=prepared, turn=0,
        turn_text='', calls=[call('generate_image', {'prompt': '跳过规划'}, 'early-image')],
        previewed_call_ids=set(), cancellation_event=runtime.cancellation_event,
        sink=sink, blocks=blocks, runtime=runtime)
    handler._execute_tool_calls.assert_not_called()
    assert blocks[0]['status'] == 'error'
    assert '完整规划' in blocks[0]['output']


@pytest.mark.asyncio
@pytest.mark.parametrize('session_bound',[False,True])
async def test_internal_reply_with_empty_display_does_not_reactivate_completed_prompt_phase(monkeypatch,session_bound):
    from unittest.mock import AsyncMock
    from services.handlers.chat.execution_engine import execute_chat,ChatExecutionRequest
    from services.tools.runtime_context import catalog_context
    runtime,prepared,handler,sink,blocks=await saved_plan(monkeypatch)
    original=runtime.task_execution_state
    await execute(runtime,prepared,handler,sink,[call('complete_task_step',{
        'expected_revision':original.revision,'step_id':'planning','artifact_ids':[original.artifacts[0].id]},'done')],blocks)
    skills=runtime.skill_runtime
    assert 'report' not in skills.active
    if session_bound:
        skills.session_skill_ids=('report',)
    gateway=SimpleNamespace(close=AsyncMock())
    prepared.model_gateway=gateway
    prepared.execution_context=catalog_context(None)
    prepared.tool_context=SimpleNamespace(discovered_tools=set())
    prepared.budget=SimpleNamespace(turns_used=0)
    handler.org_id=None
    handler.db=object()
    async def restore(**kwargs):
        kwargs['runtime'].skill_runtime=skills
        return skills
    monkeypatch.setattr('services.skills.runtime.create_skill_runtime',restore)
    monkeypatch.setattr('services.handlers.chat.execution_engine.prepare_chat_stream',AsyncMock(return_value=prepared))
    class PreparationReached(Exception): pass
    async def after_activation(*args):
        assert 'report' not in skills.active
        assert skills.effective_allowed_tool_names=={'generate_image','file_search'}
        assert skills.manual_skill_id=='report'
        raise PreparationReached()
    monkeypatch.setattr('services.knowledge_workspace.answer_plan.prepare',after_activation)
    request=ChatExecutionRequest(content=[],user_id=str(original.actor_user_id),conversation_id=str(original.conversation_id),
        task_id=str(original.root_task_id),message_id='new-reply',model_id='test',context_anchor=None,
        selected_skill=skills.manual_selection,replay_context={'messages':prepared.messages,'content_blocks':[],
            'skill_runtime':skills.checkpoint()})
    with pytest.raises(PreparationReached):
        await execute_chat(handler=handler,request=request,runtime=runtime)
    assert 'report' not in skills.active
    gateway.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('tools',[{'file_search'},{'file_search','generate_image'}])
async def test_completed_method_remains_resident_for_fixed_dependency_and_replays_with_proof(monkeypatch,tools):
    runtime,prepared,handler,sink,blocks=await saved_plan(monkeypatch)
    skills=runtime.skill_runtime
    dependency=item('dependent',tools=tuple(sorted(tools)),coordination={'version':1,'kind':'guidance','requires':['report']})
    skills.directory['dependent']=dependency
    skills.session_skill_ids=('dependent',)
    assert (await skills.activate(activate('dependent'),session=True))['ok']
    state=runtime.task_execution_state
    await execute(runtime,prepared,handler,sink,[call('complete_task_step',{
        'expected_revision':state.revision,'step_id':'planning','artifact_ids':[state.artifacts[0].id]},'complete')],blocks)
    skills=runtime.skill_runtime
    assert set(skills.active)=={'dependent'} and set(skills.resident)=={'report'}
    assert skills.effective_allowed_tool_names==tools
    assert (await skills.activate_required(completed_phase_ids={'report'}))['ok']
    assert all('测试来源：完成规划阶段。' not in m['content'] for m in skills.messages() if 'report' in m['content'])
    checkpoint=skills.checkpoint()
    restored=skill_state(skills.source,platform_tool_names={'file_search','generate_image'},
        authorized_tool_names={'file_search','generate_image'},task_execution_state=runtime.task_execution_state)
    await restored.initialize(checkpoint)
    assert set(restored.resident)=={'report'} and set(restored.active)=={'dependent'}
    assert restored.effective_allowed_tool_names==tools
    assert (await restored.activate_required(completed_phase_ids={'report'}))['ok']
    await methods.validate_method_replay(runtime.task_execution_state,restored)
    # Removing the verified resident is still a dependency violation.
    with pytest.raises(ValueError):
        from services.skills.coordination import Composition
        Composition.model_validate({**checkpoint['composition'],'participants':[
            p for p in checkpoint['composition']['participants'] if p['skill_key']!='report']})


@pytest.mark.asyncio
async def test_resident_checkpoint_without_host_task_completion_proof_is_rejected(monkeypatch):
    from services.skills.runtime import SkillReplayError
    runtime,prepared,handler,sink,blocks=await saved_plan(monkeypatch)
    state=runtime.task_execution_state
    await execute(runtime,prepared,handler,sink,[call('complete_task_step',{
        'expected_revision':state.revision,'step_id':'planning','artifact_ids':[state.artifacts[0].id]},'complete')],blocks)
    skills=runtime.skill_runtime
    for proof in (None,state):
        restored=skill_state(skills.source,platform_tool_names={'file_search','generate_image'},task_execution_state=proof)
        with pytest.raises(SkillReplayError):
            await restored.initialize(skills.checkpoint())
