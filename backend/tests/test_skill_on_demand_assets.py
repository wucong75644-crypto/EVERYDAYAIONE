"""Pinned lazy reads, atomic model barriers, old snapshots and noninteractive fallbacks."""
import asyncio
import copy
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.skills.assets import AssetDraft, TemplateVariable
from services.skills.contracts import SkillError
from services.skills.renderer import asset_manifest_digest, digest
from services.skills.runtime import SkillReplayError, READ_SKILL_ASSET
from services.skills.selection import SkillSelection
from tests.test_skill_assets import attachment, document, publish
from tests.test_skill_assets_runtime import source_for
from tests.test_skill_runtime import state, item, activate, Source
from tests.test_skill_storage import storage  # noqa: F401
from tests.test_skill_runtime_actor import actor, prepared, batch, call
from services.conversation_commands import SafePoint


def request(key='skill0', asset='guide', **extra):
    return json.dumps({'skill_id': key, 'asset_id': asset, **extra})


async def loaded(storage, content=None, **options):
    skill = publish(storage, content or document(attachment(loading='on_demand')))
    source = source_for(storage, skill)
    runtime = state(source, **options)
    await runtime.initialize()
    assert (await runtime.activate(activate('skill0')))['ok']
    return runtime, source, skill


def test_default_resource_serialization_preserves_old_hash_shape():
    eager = attachment()
    assert 'loading' not in eager.model_dump(mode='json')
    assert 'loading' not in eager.manifest().model_dump(mode='json', exclude_none=True)
    lazy = attachment(loading='on_demand')
    assert lazy.manifest().model_dump()['loading'] == 'on_demand'
    assert AssetDraft.model_validate(eager.model_dump()).loading == 'eager'


async def test_only_requested_lazy_asset_is_read_and_context_contains_one_body(storage):
    content = document(attachment(loading='on_demand', content='UNIQUE REFERENCE'),
                       attachment('other', loading='on_demand', content='DO NOT READ'),
                       body='Use [[asset:guide]] for reference; [[asset:other]] only for another phase.')
    runtime, source, _ = await loaded(storage, content)
    source.load_assets.assert_not_awaited()
    assert runtime.active['skill0'].loaded_asset_ids == ()
    before_scope = runtime.effective_allowed_tool_names
    result = await runtime.read_asset(request())
    assert result['ok'] and 'UNIQUE REFERENCE' not in json.dumps(result)
    assert runtime.effective_allowed_tool_names == before_scope
    assert runtime.active['skill0'].loaded_asset_ids == ('guide',)
    assert 'DO NOT READ' not in runtime.active['skill0'].rendered
    assert (await runtime.read_asset(request()))['code'] == 'SKILL_ASSET_ALREADY_LOADED'
    assert source.load_assets.await_count == 1
    # Activation + both read attempts revalidate the pinned source.
    assert source.load.await_count == 3
    messages = []
    runtime.ensure_messages(messages)
    runtime.ensure_messages(messages)
    assert json.dumps(runtime.model_messages(messages, []), ensure_ascii=False).count('UNIQUE REFERENCE') == 1


async def test_unread_and_read_snapshots_restore_only_their_exact_loaded_set(storage):
    runtime, source, _ = await loaded(storage)
    unread = runtime.checkpoint()
    restored = state(source)
    await restored.initialize(unread)
    source.load_assets.assert_not_awaited()
    assert restored.checkpoint() == unread
    assert (await restored.read_asset(request()))['ok']
    saved = restored.checkpoint()
    source.load_assets.reset_mock()
    again = state(source, template_context={'org_id': 'changed'})
    await again.initialize(saved)
    source.load_assets.assert_awaited_once()
    assert again.checkpoint() == saved
    assert again.active['skill0'].rendered == restored.active['skill0'].rendered


@pytest.mark.parametrize('bad', [None, '[]', '{', '{}', request(asset='../guide'), request(asset='other'),
                               request(key='inactive'), request(path='/etc/passwd'), request(revision='v2')])
async def test_invalid_or_unreferenced_reads_do_no_io(storage, bad):
    runtime, source, _ = await loaded(storage)
    before = runtime.checkpoint()
    assert not (await runtime.read_asset(bad))['ok']
    source.load_assets.assert_not_awaited()
    assert runtime.checkpoint() == before


async def test_unreferenced_declared_lazy_asset_cannot_be_read(storage):
    runtime, source, _ = await loaded(storage, document(attachment(), attachment('other', loading='on_demand')))
    before = runtime.checkpoint()
    assert (await runtime.read_asset(request(asset='other')))['code'] == 'SKILL_ASSET_NOT_DECLARED'
    assert runtime.checkpoint() == before


async def test_revocation_is_checked_even_for_previously_loaded_resources(storage):
    runtime, source, _ = await loaded(storage)
    assert (await runtime.read_asset(request()))['ok']
    before = runtime.checkpoint()
    source.load.side_effect = SkillError('SKILL_ACCESS_DENIED')
    assert (await runtime.read_asset(request()))['code'] == 'SKILL_ACCESS_DENIED'
    assert runtime.checkpoint() == before
    assert source.load_assets.await_count == 1


@pytest.mark.parametrize('when', ['before', 'during'])
async def test_resource_drift_stops_read_or_replay(storage, when):
    runtime, source, skill = await loaded(storage)
    if when == 'during':
        assert (await runtime.read_asset(request()))['ok']
    path = storage.root / skill.nas_path.replace('SKILL.md', 'assets/guide.md')
    path.chmod(0o600)
    path.write_text('tampered')
    if when == 'before':
        before = runtime.checkpoint()
        assert not (await runtime.read_asset(request()))['ok']
        assert runtime.checkpoint() == before
    else:
        with pytest.raises(SkillReplayError):
            await state(source).initialize(runtime.checkpoint())


async def test_read_budget_rejects_before_asset_io_and_retains_method(storage):
    runtime, source, _ = await loaded(storage, document(attachment(loading='on_demand', content='x' * 30000)))
    before = runtime.checkpoint()
    assert (await runtime.read_asset(request()))['code'] == 'SKILL_ASSET_BUDGET_EXCEEDED'
    source.load_assets.assert_not_awaited()
    assert runtime.checkpoint() == before


async def test_template_expansion_cannot_overrun_budget_or_change_saved_arguments(storage):
    content = document(attachment(kind='template', loading='on_demand', content='{{args.org}}' * 1000),
                       variables={'org': TemplateVariable(source='org_id', type='string')})
    runtime, source, _ = await loaded(storage, content, template_context={'org_id': 'o' * 40})
    before = runtime.checkpoint()
    assert (await runtime.read_asset(request()))['code'] == 'SKILL_ASSET_BUDGET_EXCEEDED'
    assert runtime.checkpoint() == before
    restored = state(source, template_context={'org_id': 'new'})
    await restored.initialize(before)
    assert (await restored.read_asset(request()))['code'] == 'SKILL_TEMPLATE_SERVER_VALUE_UNAVAILABLE'


async def test_cancelled_asset_read_does_not_modify_runtime(storage):
    runtime, source, _ = await loaded(storage)
    before = runtime.checkpoint()
    async def cancel(*args):
        runtime.cancellation_event.set()
        return {'guide': 'A private reference.'}
    source.load_assets.side_effect = cancel
    with pytest.raises(asyncio.CancelledError):
        await runtime.read_asset(request())
    assert runtime.checkpoint() == before


async def test_queued_cancel_prevents_resource_io_and_partial_checkpoint(storage):
    from services.conversation_commands import CommandType, ConversationCommand
    from services.conversation_state import ConversationStopRequested
    skills, source, _ = await loaded(storage)
    before = skills.checkpoint()
    source.load.reset_mock()
    source.load_assets.reset_mock()
    runtime = actor()
    runtime.skill_runtime = skills
    runtime.push(ConversationCommand('cancel', CommandType.CANCEL, 'conv-1', 'task-1', 'turn-1'))
    p = prepared()
    messages = copy.deepcopy(p.messages)
    with pytest.raises(ConversationStopRequested):
        await batch(runtime, p, [call(READ_SKILL_ASSET, request())])
    source.load.assert_not_awaited()
    source.load_assets.assert_not_awaited()
    assert runtime.replay_save_sequence == 0
    assert runtime.skill_runtime.checkpoint() == before
    assert p.messages == messages


@pytest.mark.parametrize('damage', ['unknown_deferred', 'undeclared_loaded', 'duplicate_loaded', 'missing_eager'])
async def test_replay_validates_complete_loading_plan(storage, damage):
    content = document(attachment(loading='on_demand'), attachment('eager', content='EAGER'),
                       body='Read [[asset:eager]], then optionally [[asset:guide]].')
    runtime, source, _ = await loaded(storage, content)
    assert (await runtime.read_asset(request()))['ok']
    saved = runtime.checkpoint()
    active = saved['active'][0]
    if damage == 'unknown_deferred': active['deferred_asset_ids'] = ['missing']
    elif damage == 'undeclared_loaded': active['loaded_asset_ids'] = ['eager', 'missing']
    elif damage == 'duplicate_loaded': active['loaded_asset_ids'] = ['eager', 'guide', 'guide']
    else: active['loaded_asset_ids'] = ['guide']
    with pytest.raises(SkillReplayError, match='ASSET_MISMATCH'):
        await state(source).initialize(saved)


async def test_media_fallback_eagerly_loads_explicit_lazy_resources(storage):
    runtime, source, _ = await loaded(storage, allow_deferred_assets=False)
    assert not runtime.has_deferred_assets
    assert runtime.active['skill0'].loaded_asset_ids == ('guide',)
    assert 'A private reference.' in runtime.active['skill0'].rendered
    assert 'deferred_asset_ids' not in runtime.checkpoint()['active'][0]
    assert (await runtime.read_asset(request()))['code'] == 'SKILL_ASSET_READ_UNAVAILABLE'
    restored = state(source, allow_deferred_assets=False)
    await restored.initialize(runtime.checkpoint())
    assert restored.checkpoint() == runtime.checkpoint()


async def test_scheduled_fallback_has_all_pinned_resources_before_model(storage):
    from tests.test_scheduled_skill_snapshots import snapshot
    skill = publish(storage, document(attachment(loading='on_demand')))
    c = item(execution_modes=['scheduled'])
    source = Source([c], body=skill.body)
    source.load = AsyncMock(return_value=replace(skill, catalog_metadata=c.catalog_metadata))
    source.load_assets = AsyncMock(side_effect=lambda candidate, validated, ids: storage.read_assets(validated, ids))
    runtime = state(source, execution_mode='scheduled')
    pins = snapshot(source)
    await runtime.initialize_scheduled(pins)
    assert runtime.active['report'].loaded_asset_ids == ('guide',)
    assert not runtime.has_deferred_assets
    restored = state(source, execution_mode='scheduled')
    await restored.initialize_scheduled(pins, runtime.checkpoint())
    assert restored.checkpoint() == runtime.checkpoint()


async def test_resource_batch_is_a_barrier_and_saves_complete_read_before_delivery(storage):
    skills, source, _ = await loaded(storage)
    saved = []
    async def checkpoint(point, payload):
        saved.append(copy.deepcopy(payload))
        return {'outcome': 'saved'}
    runtime = actor(checkpoint)
    runtime.skill_runtime = skills
    h, blocks = await batch(runtime, prepared(), [call(READ_SKILL_ASSET, request()), call('file_search', '{}', 'other')])
    h._execute_tool_calls.assert_not_awaited()
    assert saved[-1]['skill_runtime']['active'][0]['loaded_asset_ids'] == ['guide']
    assert any('SKILL_ACTIVATION_BARRIER' in str(b) for b in blocks)
    assert runtime.skill_runtime.active['skill0'].loaded_asset_ids == ('guide',)


async def test_failed_resource_checkpoint_rolls_back_context_and_runtime(storage):
    skills, _, _ = await loaded(storage)
    before = skills.checkpoint()
    async def fail(point, payload):
        if point == SafePoint.AFTER_SKILL_ACTIVATION:
            raise RuntimeError('persistence unavailable')
        return {'outcome': 'saved'}
    runtime = actor(fail)
    runtime.skill_runtime = skills
    p = prepared()
    messages = copy.deepcopy(p.messages)
    with pytest.raises(RuntimeError, match='persistence unavailable'):
        await batch(runtime, p, [call(READ_SKILL_ASSET, request())])
    assert runtime.skill_runtime.checkpoint() == before
    assert p.messages == messages


@pytest.mark.parametrize('interrupt', ['pause', 'cancel', 'delivery_failure'])
async def test_confirmed_resource_checkpoint_is_not_rolled_back_by_later_interrupt(storage, interrupt):
    from services.conversation_commands import CommandType, ConversationCommand
    from services.conversation_state import ConversationPauseRequested, ConversationStopRequested
    from services.handlers.chat.execution_engine import _execute_tools
    from tests.test_chat_execution_engine import _request
    from tests.test_skill_runtime_actor import handler
    skills, source, _ = await loaded(storage)
    saved = []
    async def checkpoint(point, payload):
        saved.append(copy.deepcopy(payload))
        if interrupt != 'delivery_failure':
            runtime.push(ConversationCommand('interrupt',
                CommandType.PAUSE if interrupt == 'pause' else CommandType.CANCEL,
                'conv-1', 'task-1', 'turn-1'))
        return {'outcome': 'saved'}
    runtime = actor(checkpoint)
    runtime.skill_runtime = skills
    p, h = prepared(), handler()
    sink = SimpleNamespace(on_block=AsyncMock(), on_block_update=AsyncMock(
        side_effect=RuntimeError('delivery failed') if interrupt == 'delivery_failure' else None))
    expected = (RuntimeError if interrupt == 'delivery_failure' else
                ConversationPauseRequested if interrupt == 'pause' else ConversationStopRequested)
    with pytest.raises(expected):
        await _execute_tools(handler=h, request=_request(), prepared=p, turn=0, turn_text='',
            calls=[call(READ_SKILL_ASSET, request()), call('file_search', '{}', 'deferred')], previewed_call_ids=set(),
            cancellation_event=runtime.cancellation_event, sink=sink, blocks=[], runtime=runtime,
            next_model_round=1)
    h._execute_tool_calls.assert_not_awaited()
    assert runtime.replay_save_sequence == 1
    assert runtime.skill_runtime.active['skill0'].loaded_asset_ids == ('guide',)
    assert runtime.skill_runtime.checkpoint() == saved[-1]['skill_runtime']
    assert p.messages == saved[-1]['messages']
    assert len([message for message in p.messages if message.get('role') == 'tool']) == 2
    restored = state(source)
    await restored.initialize(saved[-1]['skill_runtime'])
    assert restored.checkpoint() == runtime.skill_runtime.checkpoint()


async def test_legacy_context_cannot_acquire_a_new_lazy_checkpoint(storage):
    runtime, source, _ = await loaded(storage)
    saved = runtime.checkpoint()
    saved['context_version'] = 1
    with pytest.raises(SkillReplayError, match='ASSET_MISMATCH'):
        await state(source).initialize(saved)
