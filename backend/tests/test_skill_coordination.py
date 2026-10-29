"""Current-action ownership, locked sets, isolated candidates and pinned replay."""
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from services.skills.contracts import CoordinationContract, SkillCatalogMetadata, SkillError
from services.skills.coordination import ActionContext, CompositionConflict
from services.skills.runtime import SkillReplayError
from services.skills.selection import SkillSelection
from services.skills.workflow_contracts import contract_for, legacy_contracts
from tests.test_skill_runtime import Source, activate, item, state

TOOLS = {'generate_image', 'plan_ecommerce_images', 'file_search', 'web_search'}


def workflow(method='ecommerce.inline', scope='image.planning', actions=('plan',), requires=()):
    return dict(version=1, kind='workflow', claims=[dict(scope=scope, method=method, actions=actions)],
                requires=requires)


async def prepared(*candidates, action='plan', selection=None):
    source = Source(list(candidates), body='Use the current action only.')
    runtime = state(source, platform_tool_names=TOOLS)
    await runtime.initialize(selection=selection)
    runtime.set_action(ActionContext(action=action))
    return runtime, source


async def test_conflicting_optional_candidate_never_loads_or_changes_primary():
    runtime, source = await prepared(item('primary', tools=TOOLS, coordination=workflow()),
        item('other', tools=TOOLS, coordination=workflow('ecommerce.server_plan')))
    assert (await runtime.activate(activate('primary')))['ok']
    before = runtime.checkpoint()
    result = await runtime.activate(activate('other'))
    assert result['code'] == 'SKILL_WORKFLOW_CONFLICT' and result['state_unchanged']
    assert runtime.checkpoint() == before
    assert source.load.await_count == 1


async def test_loaded_does_not_mean_controlling_explanation_or_other_topics():
    runtime, _ = await prepared(item('a', tools=TOOLS, coordination=workflow()),
                               item('b', tools=TOOLS, coordination=workflow('ecommerce.server_plan')),
                               action='explain')
    assert (await runtime.activate(activate('a')))['ok']
    assert (await runtime.activate(activate('b')))['ok']
    assert not runtime.composition.owners
    old = runtime.checkpoint()
    with pytest.raises(CompositionConflict):
        runtime.set_action(ActionContext(action='plan'))
    assert runtime.checkpoint() == old
    runtime.set_action(ActionContext(action='other'))
    assert set(runtime.active) == {'a', 'b'} and not runtime.composition.owners


async def test_fixed_set_is_atomic_even_when_later_load_fails():
    a, b = item('a', tools=TOOLS), item('b', tools=TOOLS)
    source = Source([a, b], body='method')
    source.session_bindings.return_value = [a, b]
    runtime = state(source, platform_tool_names=TOOLS)
    await runtime.initialize()
    load = source._load
    async def fail(c, **kwargs):
        if c.skill_key == 'b':
            raise SkillError('SKILL_ACCESS_DENIED')
        return await load(c, **kwargs)
    source.load.side_effect = fail
    result = await runtime.activate_required()
    assert not result['ok'] and not runtime.active
    assert runtime.effective_allowed_tool_names == TOOLS


async def test_manual_and_session_sources_survive_replay():
    a = item('a', tools=TOOLS, coordination=workflow())
    source = Source([a], body='method')
    source.session_bindings.return_value = [a]
    runtime = state(source, platform_tool_names=TOOLS)
    await runtime.initialize(selection=SkillSelection(skill_id='a', revision='v1'))
    runtime.set_action(ActionContext(action='plan'))
    assert (await runtime.activate_required())['ok']
    assert runtime.composition.participants[0].sources == ('user', 'session')
    checkpoint = runtime.checkpoint()
    restored = state(source, platform_tool_names=TOOLS | {'file_delete'})
    await restored.initialize(copy.deepcopy(checkpoint))
    assert restored.checkpoint() == checkpoint
    assert (await restored.deactivate(json.dumps({'skill_id':'a'})))['code'] == 'SKILL_BINDING_LOCKED'


async def test_stale_manual_selection_is_rejected_before_any_load():
    runtime, source = await prepared(item('a', tools=TOOLS), selection=SkillSelection(skill_id='a', revision='v2'))
    assert (await runtime.activate_required())['code'] == 'SKILL_SELECTION_CHANGED'
    source.load.assert_not_awaited()


async def test_required_dependencies_are_sorted_and_optional_failure_is_isolated():
    a = item('primary', tools=TOOLS, coordination=workflow())
    b = item('dependent', tools=TOOLS, coordination={'kind':'guidance','requires':['helper']})
    c = item('helper', tools=TOOLS, coordination={'kind':'guidance'})
    runtime, _ = await prepared(a, b, c)
    assert (await runtime.activate(activate('primary')))['ok']
    assert (await runtime.activate(activate('dependent')))['code'] == 'SKILL_DEPENDENCY_UNAVAILABLE'
    assert (await runtime.activate(activate('helper')))['ok']
    assert (await runtime.activate(activate('dependent')))['ok']
    before = runtime.checkpoint()
    assert (await runtime.deactivate(json.dumps({'skill_id':'helper'})))['code'] == 'SKILL_DEPENDENCY_UNAVAILABLE'
    assert runtime.checkpoint() == before


async def test_new_auxiliary_cannot_remove_existing_required_tools():
    root = item('root', tools=TOOLS, required_capabilities=['platform.generate_image'])
    helper = item('helper', tools=['file_search'], coordination={'kind':'guidance'})
    runtime, _ = await prepared(root, helper)
    assert (await runtime.activate(activate('root')))['ok']
    before = runtime.checkpoint()
    assert (await runtime.activate(activate('helper')))['code'] == 'SKILL_REQUIRED_CAPABILITY_UNAVAILABLE'
    assert runtime.checkpoint() == before


async def test_action_transition_uses_execution_claim_without_replanning():
    contract = workflow()
    contract['claims'].append(dict(scope='image.submission',method='image.direct',actions=['execute_prompt','edit']))
    runtime, _ = await prepared(item('a', tools=TOOLS, coordination=contract))
    assert (await runtime.activate(activate('a')))['ok']
    runtime.prepare_tool_action([dict(name='generate_image',arguments='{"prompt":"exact"}')])
    assert runtime.action_context.action == 'execute_prompt'
    assert runtime.workflow_method('image.submission') == 'image.direct'
    assert runtime.workflow_method('image.planning') is None


async def test_v1_checkpoint_roundtrips_without_claiming_latest_action():
    runtime, _ = await prepared(item('a', tools=TOOLS))
    assert (await runtime.activate(activate('a')))['ok']
    saved = runtime.checkpoint()
    saved['version'] = 1
    saved.pop('composition')
    restored = state(runtime.source, platform_tool_names=TOOLS)
    await restored.initialize(saved)
    assert restored.checkpoint() == saved


async def test_tampered_composition_replay_is_rejected():
    runtime, _ = await prepared(item('a', tools=TOOLS, coordination=workflow()))
    await runtime.activate(activate('a'))
    saved = runtime.checkpoint()
    saved['composition']['owners'] = {}
    with pytest.raises(SkillReplayError):
        await state(runtime.source, platform_tool_names=TOOLS).initialize(saved)


def test_legacy_contracts_are_exact_not_name_or_revision_guesses():
    identity, (key, expected) = next(iter(legacy_contracts().items()))
    package, revision, sha = identity
    candidate = SimpleNamespace(package_id=package,revision=revision,skill_key=key,catalog_metadata=SkillCatalogMetadata())
    assert contract_for(candidate, sha) == expected
    assert contract_for(candidate, '0'*64) is None
    candidate.package_id = uuid4()
    assert contract_for(candidate, sha) is None


async def test_optional_batch_keeps_compatible_helper_and_never_dispatches_business():
    from tests.test_skill_runtime_actor import actor, prepared as engine_prepared
    from services.handlers.chat.execution_engine import _execute_skill_batch
    from services.handlers.chat.execution_sink import CollectingExecutionSink
    skills, _ = await prepared(item('primary',tools=TOOLS,coordination=workflow()),
        item('helper',tools=TOOLS,coordination={'kind':'guidance'}),
        item('conflict',tools=TOOLS,coordination=workflow('ecommerce.server_plan')))
    await skills.activate(activate('primary'))
    runtime, p = actor(), engine_prepared()
    runtime.skill_runtime = skills
    calls = [dict(id=key,name='activate_skill',arguments=activate(key)) for key in ['helper','conflict']]
    await _execute_skill_batch(prepared=p,calls=calls,runtime=runtime,blocks=[],
        sink=CollectingExecutionSink(),start_times={},turn=0,next_model_round=1,repeated_tool_call_guard=None)
    assert set(skills.active) == {'primary','helper'}
    assert any('SKILL_WORKFLOW_CONFLICT' in str(m.get('content')) for m in p.messages)


async def test_failed_actor_save_does_not_adopt_staged_method():
    from tests.test_skill_runtime_actor import actor, prepared as engine_prepared
    from services.handlers.chat.execution_engine import _execute_skill_batch
    from services.handlers.chat.execution_sink import CollectingExecutionSink
    skills, _ = await prepared(item('helper',tools=TOOLS))
    runtime, p = actor(), engine_prepared()
    runtime.skill_runtime = skills
    runtime.safe_point = AsyncMock(side_effect=RuntimeError('save unconfirmed'))
    with pytest.raises(RuntimeError,match='unconfirmed'):
        await _execute_skill_batch(prepared=p,calls=[dict(id='call',name='activate_skill',arguments=activate('helper'))],
            runtime=runtime,blocks=[],sink=CollectingExecutionSink(),start_times={},turn=0,
            next_model_round=1,repeated_tool_call_guard=None)
    assert runtime.skill_runtime is skills and not skills.active


def test_guidance_cannot_claim_workflow_and_legacy_metadata_is_unchanged():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        CoordinationContract(kind='guidance',claims=workflow()['claims'])
    assert 'coordination' not in SkillCatalogMetadata().model_dump(mode='json')


async def test_inline_owner_overrides_server_route_without_loading_second_method():
    from tests.test_skill_image_routing import flow
    skills, source = await prepared(item('chosen', tools=TOOLS, coordination=workflow()), action='other')
    await skills.activate(activate('chosen'))
    value = flow()
    value.runtime = SimpleNamespace(skill_runtime=skills, skill_coordination=True)
    value.request.replay_context = None
    value.route = {'kind':'main_images', 'mode':'new'}
    await value.coordinate(skills)
    assert value.value.kind == 'ordinary_image'
    assert skills.workflow_method('image.planning') == 'ecommerce.inline'
    assert value.server_skill_key() is None
    assert source.load.await_count == 1
    assert not await value.allows_planning()


async def test_supported_custom_server_owner_is_used_without_name_based_replacement():
    from tests.test_skill_image_routing import flow
    skills, _ = await prepared(item('custom', tools=TOOLS,
        coordination=workflow('ecommerce.server_plan')), action='other')
    await skills.activate(activate('custom'))
    value = flow()
    value.runtime = SimpleNamespace(skill_runtime=skills, skill_coordination=True)
    value.request.replay_context = None
    value.route = {'kind':'main_images', 'mode':'new'}
    await value.coordinate(skills)
    assert value.server_skill_key() == 'custom'
    # An explanation does not reactivate a previously loaded planning method.
    skills.set_action(ActionContext(action='explain'))
    value.route = {'kind':'unrelated'}
    await value.coordinate(skills)
    assert value.server_skill_key() is None


async def test_checkpoint_acknowledgement_loss_requires_identical_full_payload():
    from services.handlers.chat.executor import ChatGenerationExecutor
    from services.conversation_commands import SafePoint
    store = SimpleNamespace(write=AsyncMock(side_effect=TimeoutError()), read_latest=AsyncMock())
    executor = ChatGenerationExecutor(None, replay_checkpoint_store=store)
    claim = SimpleNamespace(task_id='task', execution_token='token')
    payload = {'_skill_composition':{'revision':2}, 'messages':[{'content':'original'}]}
    store.read_latest.return_value = {'outcome':'found', 'payload':dict(payload, safe_point='before_tool')}
    assert (await executor._write_replay_checkpoint(claim, SafePoint.BEFORE_TOOL, payload))['reconciled']
    store.read_latest.return_value['payload']['messages'] = [{'content':'different'}]
    with pytest.raises(RuntimeError, match='SAVE_UNCONFIRMED'):
        await executor._write_replay_checkpoint(claim, SafePoint.BEFORE_TOOL, payload)


async def test_v2_cannot_reach_model_without_checkpoint_writer():
    from tests.test_skill_runtime_actor import actor
    from services.conversation_commands import SafePoint
    runtime = actor()
    runtime._replay_checkpoint_callback = None
    runtime.skill_runtime, _ = await prepared()
    with pytest.raises(RuntimeError, match='COMPOSITION_CHECKPOINT_UNAVAILABLE'):
        await runtime.safe_point(SafePoint.BEFORE_MODEL, replay_payload={'messages':[]})


def test_dependency_order_preserves_non_activation_barriers():
    runtime = state(Source([item('a'),item('b',coordination={'kind':'guidance','requires':['a']})]))
    # This ordering helper reads the pinned directory, not mutable discovery.
    runtime.directory = {c.skill_key:c for c in runtime.source.discover.return_value}
    calls = [dict(name='activate_skill',arguments=activate(k),id=k) for k in ['b','a']]
    assert [c['id'] for c in runtime.ordered_control_calls(calls)] == ['a','b']
    exit_call = dict(name='deactivate_skill',arguments=activate('a'),id='exit')
    assert [c['id'] for c in runtime.ordered_control_calls([calls[0],exit_call,calls[1]])] == ['b','exit','a']


async def test_pause_before_load_resumes_pinned_action_without_routing_again():
    from tests.test_skill_image_routing import flow
    from services.agent.image.ecommerce_planner.workflow import WorkflowBinding
    candidate = item('chosen', tools=TOOLS, coordination=workflow())
    skills, _ = await prepared(candidate, action='other',
        selection=SkillSelection(skill_id='chosen', revision='v1'))
    value = flow()
    value.runtime = SimpleNamespace(skill_runtime=skills, skill_coordination=True)
    value.request.replay_context = None
    value.route = {'kind':'main_images','mode':'new'}
    value.value = WorkflowBinding(kind='main_images', root_task_id=value.request.task_id,
        window_task_id=value.request.task_id, generation_run_id=str(uuid4()))
    await value.coordinate(skills)
    saved = skills.checkpoint()  # Initial checkpoint, before body loading.
    restored = state(Source([candidate], body='Use the current action only.'), platform_tool_names=TOOLS)
    await restored.initialize(saved)
    assert (await restored.activate_required())['ok']
    value.runtime.skill_runtime = restored
    value.request.replay_context = {'skill_runtime':saved}
    value.identify = AsyncMock(side_effect=AssertionError('must not reroute replay'))
    await value.coordinate(restored)
    assert restored.workflow_method('image.planning') == 'ecommerce.inline'
    assert value.value.kind == 'ordinary_image'


async def test_mixed_business_actions_return_errors_without_dispatch_or_state_change():
    from tests.test_skill_runtime_actor import actor, prepared as engine_prepared, batch
    skills, _ = await prepared()
    runtime, p = actor(), engine_prepared()
    runtime.skill_runtime = skills
    before = skills.checkpoint()
    h, blocks = await batch(runtime, p, [
        dict(id='planning',name='plan_ecommerce_images',arguments='{}'),
        dict(id='submission',name='generate_image',arguments='{}'),
    ])
    h._execute_tool_calls.assert_not_awaited()
    assert skills.checkpoint() == before
    assert any(b.get('status') == 'error' for b in blocks)
