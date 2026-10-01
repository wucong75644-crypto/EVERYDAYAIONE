"""User retries restore bounded identities from owned records, never new grants."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from schemas.message import GenerateRequest, GenerationType, MessageOperation, TextPart
from services.skills.retry import SkillIntent, capture_intent, original_intent, PinnedIntentSource, parse_intent
from services.skills.runtime import SkillBindingError, create_skill_runtime
from services.skills.selection import SkillSelection
from tests.test_skill_runtime import Source, item, state
from tests.test_skill_resolver import context, ACTOR, ORG
from tests.test_skill_multimode import execution  # noqa: F401


INTENT = {'task_mode': 'image-t2i', 'selected_skill': {'skill_id': 'report', 'revision': 'v1'},
          'session_skills': [], 'package_ids': {}}


class History:
    def __init__(self, params=None, tasks=None, role='assistant', message=True):
        self.tables, self.filters = [], []
        self.rows = {'messages': {'id': 'old', 'role': role, 'generation_params': params} if message else None,
                     'tasks': tasks or [], 'conversation_turn_checkpoints': None}

    def table(self, name):
        self.tables.append(name)
        return SimpleNamespace(**{k: lambda *a, _k=k, **kw: self._call(name, _k, *a, **kw)
                                  for k in ('select', 'eq', 'is_', 'maybe_single', 'order', 'limit', 'execute')})

    def _call(self, name, method, *args, **kwargs):
        if method == 'execute':
            return SimpleNamespace(data=self.rows[name])
        if method in ('eq', 'is_'):
            self.filters.append((name, *args))
        return SimpleNamespace(**{k: lambda *a, _k=k, **kw: self._call(name, _k, *a, **kw)
                                  for k in ('select', 'eq', 'is_', 'maybe_single', 'order', 'limit', 'execute')})


@pytest.fixture
def authority(monkeypatch):
    check = Mock(return_value=str(ORG))
    monkeypatch.setattr('services.skills.available._conversation_org', check)
    return check


async def restore(db, org=str(ORG)):
    return await original_intent(db, user_id=str(ACTOR), org_id=org, conversation_id='conv',
                                 message_id='old', task_mode='image-t2i')


async def test_history_snapshot_is_authoritative_and_summary_only(authority):
    db = History({'_skill_intent': INTENT}, tasks=[{'request_params': {'_selected_skill': {'skill_id': 'new', 'revision': 'v2'}}}])
    result = await restore(db)
    assert result.model_dump(mode='json') == INTENT
    assert db.tables == ['messages', 'tasks']
    assert ('messages', 'conversation_id', 'conv') in db.filters
    assert 'body' not in result.model_dump_json()


async def test_legacy_media_audit_restores_manual_and_session_versions(authority):
    db = History(tasks=[{'request_params': {'_selected_skill': INTENT['selected_skill'],
        '_skill_task_mode': 'image-i2i', '_media_skills': [{'skill_id': 'fixed', 'revision': 'v3', 'selection': 'session',
        'body_sha256': 'not-authority'}, {'skill_id': 'report', 'revision': 'v1', 'selection': 'user'}]}}])
    result = await restore(db)
    assert result.selected_skill.revision == 'v1'
    assert result.session_skills == (SkillSelection(skill_id='fixed', revision='v3'),)
    assert result.task_mode == 'image-i2i'
    assert ('tasks', 'user_id', str(ACTOR)) in db.filters
    assert ('tasks', 'org_id', str(ORG)) in db.filters


async def test_legacy_task_snapshot_is_used_after_message_metadata_loss(authority):
    assert (await restore(History(tasks=[{'request_params': {'_skill_intent': INTENT}}]))).required


async def test_empty_history_does_not_acquire_current_session_skills(authority):
    result = await restore(History({'type': 'image'}, tasks=[{'request_params': {}}]))
    assert not result.required
    assert result.session_skills == ()


async def test_personal_legacy_task_query_uses_is_null(authority):
    authority.return_value = None
    db = History(tasks=[{'request_params': {}}])
    await restore(db, org=None)
    assert ('tasks', 'org_id', 'null') in db.filters


@pytest.mark.parametrize('role,message', [('user', True), ('assistant', False)])
async def test_missing_or_non_assistant_history_rejected(authority, role, message):
    with pytest.raises(HTTPException) as error:
        await restore(History(role=role, message=message))
    assert error.value.status_code == 404


async def test_foreign_organization_cannot_supply_retry_authority(authority):
    db = History({'_skill_intent': INTENT})
    with pytest.raises(HTTPException) as error:
        await restore(db, org='different')
    assert error.value.status_code == 403 and db.tables == []


@pytest.mark.parametrize('raw', [None, {'task_mode': 'video', 'body': 'grant all'},
    {**INTENT, 'selected_skill': {'skill_id': '../escape', 'revision': 'v1'}},
    {**INTENT, 'session_skills': [{'skill_id': 'x', 'revision': 'v1'}] * 5},
    {**INTENT, 'session_skills': [{'skill_id': 'x', 'revision': 'v1'}] * 2}])
def test_malformed_intent_never_silently_becomes_ordinary(raw):
    with pytest.raises(HTTPException) as error:
        parse_intent(raw)
    assert error.value.status_code == 409


async def test_capture_records_binding_versions_and_empty_set(monkeypatch):
    from core.config import get_settings
    monkeypatch.setattr('core.config.get_settings', lambda: get_settings().model_copy(update={'skill_catalog_enabled': True}))
    source = Source()
    source.session_bindings.return_value = [item('fixed', revision='v2')]
    monkeypatch.setattr('services.skills.runtime_source.ActorSkillSource', Mock(return_value=source))
    h = SimpleNamespace(org_id=str(ORG))
    result = await capture_intent(h, user_id=str(ACTOR), conversation_id='conv', task_mode='smart', selection=None)
    assert result.session_skills[0].revision == 'v2'
    source.session_bindings.return_value = []
    assert not (await capture_intent(h, user_id=str(ACTOR), conversation_id='conv', task_mode='smart', selection=None)).required


def pinned_source(*, mode='smart', manual=True):
    source = Source([item(revision='v2')], body='Use the original method.')
    original = item(revision='v1')
    source.repository = SimpleNamespace(pinned_candidates=Mock(return_value=[original]))
    source._resolution_context = AsyncMock(return_value=context(task_mode=mode))
    intent = SkillIntent(task_mode=mode, selected_skill=SkillSelection(skill_id='report', revision='v1') if manual else None,
                         session_skills=() if manual else (SkillSelection(skill_id='report', revision='v1'),))
    return source, PinnedIntentSource(source, intent, retry=True), intent


async def test_retry_uses_original_manual_revision_not_latest_catalog():
    source, pinned, intent = pinned_source()
    runtime = state(pinned)
    await runtime.initialize(selection=intent.selected_skill)
    assert (await runtime.activate_manual(intent.selected_skill))['ok']
    assert source.load.await_args.args[0].revision == 'v1'
    source.discover.assert_not_awaited()
    assert runtime.effective_allowed_tool_names == {'file_search'}


async def test_retry_uses_original_pins_after_binding_was_removed():
    source, pinned, intent = pinned_source(manual=False)
    runtime = state(pinned)
    await runtime.initialize(discover_catalog=False)
    assert runtime.session_skill_ids == ('report',)
    assert (await runtime.activate_session('report'))['ok']
    source.session_bindings.assert_not_awaited()
    assert source.load.await_args.args[0].revision == 'v1'


@pytest.mark.parametrize('cause', ['missing', 'disabled', 'foreign_org', 'wrong_mode'])
async def test_unavailable_revision_or_grant_stops_without_loading_body(cause):
    source, pinned, intent = pinned_source()
    if cause in ('missing', 'disabled'):
        source.repository.pinned_candidates.return_value = []
    elif cause == 'foreign_org':
        from tests.test_skill_resolver import OTHER_ORG
        source._resolution_context.return_value = context(org_id=OTHER_ORG)
    else:
        source._resolution_context.return_value = context(task_mode='video')
    with pytest.raises(SkillBindingError):
        await state(pinned).initialize(selection=intent.selected_skill)
    source.load.assert_not_awaited()


@pytest.mark.parametrize('flag', ['skill_catalog_enabled', 'skill_runtime_enabled'])
async def test_feature_off_blocks_required_historical_intent(monkeypatch, flag):
    from core.config import get_settings
    settings = get_settings().model_copy(update={'skill_catalog_enabled': True, 'skill_runtime_enabled': True, flag: False})
    monkeypatch.setattr('core.config.get_settings', lambda: settings)
    with pytest.raises(SkillBindingError):
        await create_skill_runtime(handler=object(), context=object(), runtime=object(), intent=INTENT)


@pytest.mark.parametrize('operation', ['retry', 'regenerate', 'regenerate_single'])
@pytest.mark.parametrize('generation,mode', [('chat','smart'), ('image','image-t2i'), ('image_ecom','image-ecom'), ('video','video')])
async def test_http_retry_restores_owned_skill_and_strips_forged_intent(monkeypatch, authority, operation, generation, mode):
    from api.routes import message as route
    from api.deps import OrgContext
    from tests.test_message_routes import _make_request, _make_message
    intent = {**INTENT, 'task_mode': mode}
    db = History({'_skill_intent': intent})
    gen = GenerationType(generation)
    body = GenerateRequest(operation=MessageOperation(operation), original_message_id='old', content=[TextPart(text='supplement')],
                           generation_type=gen, selected_skill={'skill_id': 'forged', 'revision': 'latest'},
                           skill_task_mode='video', params={'_skill_intent': {'task_mode': 'video'}, '_skill_retry': False})
    monkeypatch.setattr(route, 'load_control_tasks', lambda *a, **kw: SimpleNamespace(running=None, paused=None))
    monkeypatch.setattr(route, 'resolve_generation_context', AsyncMock(return_value=gen))
    monkeypatch.setattr(route, 'get_handler', Mock(return_value=object()))
    monkeypatch.setattr(route, 'get_conversation_service', Mock())
    monkeypatch.setattr(route, 'prepare_generation_request', AsyncMock(return_value=(object(), {}, _make_message('input'))))
    monkeypatch.setattr(route, 'prepare_assistant_message', AsyncMock(return_value=('output', _make_message('output'))))
    start = AsyncMock(return_value='task')
    monkeypatch.setattr(route, 'start_generation_task', start)
    await route._do_generate_message(_make_request(), 'conv', body, OrgContext(user_id=str(ACTOR), org_id=str(ORG)), db, str(ACTOR))
    params = start.await_args.kwargs['params']
    assert params['_selected_skill'] == INTENT['selected_skill']
    assert params['_skill_intent'] == intent and params['_skill_retry'] is True
    assert start.await_args.kwargs['content'] == body.content


@pytest.mark.parametrize('generation', list(GenerationType)[:4])
async def test_placeholder_saves_identity_snapshot_before_any_execution(generation):
    from api.routes.message_generation_helpers import handle_regenerate_or_send_operation
    from tests.test_message_generation_helpers import MockGenHelperDB
    db = MockGenHelperDB()
    await handle_regenerate_or_send_operation(db, 'conv', MessageOperation.SEND, None, 'out', None, generation,
                                              params={'_skill_intent': INTENT})
    assert db._inserted[0]['generation_params']['_skill_intent'] == INTENT


@pytest.mark.parametrize('manual', [True, False])
async def test_media_retry_prepares_original_method_with_identical_params(monkeypatch, execution, manual):
    from tests.test_skill_multimode import prepare
    source, pinned, intent = pinned_source(mode='image-t2i', manual=manual)
    source.repository.pinned_candidates.return_value = [item(task_modes=['image-t2i'])]
    monkeypatch.setattr('services.skills.runtime_source.ActorSkillSource', Mock(return_value=source))
    params = {'_skill_intent': intent.model_dump(mode='json'), '_skill_retry': True,
              'num_images': 2, 'aspect_ratio': '16:9'}
    if manual:
        params['_selected_skill'] = intent.selected_skill.model_dump()
    assert await prepare(execution, params) == 'white background'
    assert params['num_images'] == 2 and params['aspect_ratio'] == '16:9'
    assert params['_media_skills'][0]['revision'] == 'v1'
    assert params['_media_skills'][0]['selection'] == ('user' if manual else 'session')
    source.session_bindings.assert_not_awaited()


async def test_media_empty_intent_ignores_new_pins_when_runtime_is_off(execution):
    from tests.test_skill_multimode import prepare
    execution[0].skill_runtime_enabled = False
    execution[1].session_bindings.return_value = [item('new', task_modes=['image-t2i'])]
    params = {'_skill_intent': {'task_mode': 'image-t2i', 'session_skills': []}, '_skill_retry': True}
    assert await prepare(execution, params) == ''
    execution[1].session_bindings.assert_not_awaited()
    execution[6].assert_not_awaited()


async def test_missing_old_records_fail_without_guessing(authority):
    with pytest.raises(HTTPException) as error:
        await restore(History({'type': 'image'}))
    assert error.value.status_code == 409


async def test_legacy_actor_checkpoint_restores_session_ids_only(authority):
    db = History(tasks=[{'id': 'owned-task', 'type': 'chat', 'request_params': {}}])
    db.rows['conversation_turn_checkpoints'] = {'state': {'skill_runtime': {
        'session_skill_ids': ['fixed'], 'directory': [{'skill_key': 'fixed', 'revision': 'v2'}],
        'active': [{'skill_key': 'model-selected', 'revision': 'v9', 'rendered': 'not authority'}]}}}
    intent = await restore(db)
    assert intent.session_skills == (SkillSelection(skill_id='fixed', revision='v2'),)
    assert ('conversation_turn_checkpoints', 'task_id', 'owned-task') in db.filters
    assert ('conversation_turn_checkpoints', 'conversation_id', 'conv') in db.filters


async def test_legacy_chat_without_checkpoint_requires_new_explicit_task(authority):
    db = History(tasks=[{'id': 'owned-task', 'type': 'chat', 'request_params': {}}])
    with pytest.raises(HTTPException) as error:
        await restore(db)
    assert error.value.status_code == 409


async def test_same_name_other_package_cannot_replace_original():
    from uuid import uuid4
    source, pinned, intent = pinned_source()
    original = source.repository.pinned_candidates.return_value[0]
    shadow = original.model_copy(update={'package_id': uuid4()})
    source.repository.pinned_candidates.return_value = [shadow, original]
    intent = intent.model_copy(update={'package_ids': {'report': original.package_id}})
    runtime = state(PinnedIntentSource(source, intent, retry=True))
    await runtime.initialize(selection=intent.selected_skill)
    assert runtime.directory['report'].package_id == original.package_id
    source.repository.pinned_candidates.return_value = [shadow]
    with pytest.raises(SkillBindingError):
        await state(PinnedIntentSource(source, intent, retry=True)).initialize(selection=intent.selected_skill)


async def test_legacy_ambiguous_source_requires_explicit_new_selection():
    from uuid import uuid4
    source, pinned, intent = pinned_source()
    original = source.repository.pinned_candidates.return_value[0]
    source.repository.pinned_candidates.return_value.append(original.model_copy(update={'package_id': uuid4()}))
    with pytest.raises(SkillBindingError):
        await state(pinned).initialize(selection=intent.selected_skill)


async def test_legacy_ecom_user_selection_is_recovered_from_activation_audit(authority):
    db = History(tasks=[{'request_params': {'_media_skills': [{'skill_id': 'report', 'revision': 'v1', 'selection': 'user'}]}}])
    assert (await restore(db)).selected_skill == SkillSelection(skill_id='report', revision='v1')


async def test_confirmed_ecom_retry_uses_saved_plan_not_forged_or_new_plan(authority):
    intent = {**INTENT, 'task_mode': 'image-ecom'}
    recipe = [{'prompt': 'approved second image', 'image_type': 'white_bg'}]
    db = History({'_skill_intent': intent}, tasks=[{'request_params': {'image_task_meta': recipe, 'product_image_urls': ['original-image']}}])
    params = {'image_task_meta': [{'prompt': 'forged replacement'}], 'image_index': 0, 'aspect_ratio': '1:1'}
    restored = await original_intent(db, user_id=str(ACTOR), org_id=str(ORG), conversation_id='conv',
                                     message_id='old', task_mode='image-ecom', params_to_restore=params)
    assert restored.selected_skill.revision == 'v1'
    assert params == {'image_task_meta': recipe, 'product_image_urls': ['original-image'], 'image_index': 0, 'aspect_ratio': '1:1'}


async def test_ecom_plan_retry_cannot_be_switched_to_generation_with_client_params(authority):
    intent = {**INTENT, 'task_mode': 'image-ecom'}
    db = History({'_skill_intent': intent}, tasks=[{'request_params': {'phase': 'plan'}}])
    params = {'image_task_meta': [{'prompt': 'unconfirmed'}], '_batch_prompts': [{'prompt': 'unconfirmed'}]}
    await original_intent(db, user_id=str(ACTOR), org_id=str(ORG), conversation_id='conv',
                          message_id='old', task_mode='image-ecom', params_to_restore=params)
    assert params == {}


async def test_ecom_confirmed_retry_checks_required_skill_without_replanning(monkeypatch):
    from services.handlers.ecom_image_handler import EcomImageHandler
    from services.handlers.base import TaskMetadata
    from unittest.mock import MagicMock
    handler = EcomImageHandler(MagicMock())
    load = AsyncMock()
    monkeypatch.setattr('services.skills.media.load_media_skills', load)
    generate = AsyncMock(return_value='retry-task')
    monkeypatch.setattr(handler, '_phase2_generate', generate)
    params = {'image_task_meta': [{'prompt': 'approved'}], '_skill_intent': {
        'task_mode': 'image-ecom', 'session_skills': [{'skill_id': 'fixed', 'revision': 'v1'}]}}
    assert await handler.start('message', 'conv', 'user', [], params, TaskMetadata()) == 'retry-task'
    load.assert_awaited_once()
    load.side_effect = HTTPException(409, 'revoked')
    generate.reset_mock()
    with pytest.raises(HTTPException):
        await handler.start('message', 'conv', 'user', [], params, TaskMetadata())
    generate.assert_not_awaited()
