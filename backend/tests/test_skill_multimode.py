"""Mode-specific discovery and revision-pinned media preparation, no provider IO."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, MagicMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from core.config import get_settings
from schemas.message import GenerateRequest, TextPart, GenerationType
from services.handlers.base import TaskMetadata
from services.skills import media
from services.skills.contracts import SkillCatalogMetadata, SkillError
from services.skills.resolver import SkillResolver
from services.message_idempotency_service import MessageIdempotencyService
from tests.test_skill_resolver import candidate, context, ACTOR, ORG, OTHER_ORG
from tests.test_skill_runtime import Source, item
from tests.test_skill_available_api import api, get  # noqa: F401
from tests.test_skill_binding_api import binding_api, call  # noqa: F401

MODES = ('smart', 'image-i2i', 'image-t2i', 'image-ecom', 'video')


@pytest.mark.parametrize('mode', MODES)
def test_resolver_scopes_legacy_and_explicit_modes(mode):
    legacy = candidate(skill_key='legacy')
    multi = candidate(skill_key='multi', catalog_metadata={'task_modes': MODES})
    assert [s.skill_id for s in SkillResolver().resolve(context(task_mode=mode), [legacy, multi])] == (
        ['legacy', 'multi'] if mode == 'smart' else ['multi'])
    assert SkillResolver().resolve(context(task_mode=mode, org_id=OTHER_ORG), [multi]) == []
    assert SkillResolver().resolve(context(task_mode=mode, enabled_feature_flags=set()), [multi]) == []


def test_legacy_metadata_keeps_review_hash_shape():
    assert 'task_modes' not in SkillCatalogMetadata().model_dump(mode='json')
    assert SkillCatalogMetadata().task_modes == ('smart',)
    assert SkillCatalogMetadata(task_modes=['video']).model_dump(mode='json')['task_modes'] == ['video']


@pytest.mark.parametrize('values', [[], ['chat'], ['../image'], ['video'] * 6])
def test_invalid_mode_declarations_rejected(values):
    with pytest.raises(ValidationError):
        SkillCatalogMetadata(task_modes=values)


@pytest.mark.parametrize('mode', MODES)
def test_http_catalog_filter_is_summary_only(api, mode):
    api[5].catalog_candidates.return_value = [candidate(catalog_metadata={'task_modes': ['video']})]
    response = get(api, task_mode=mode)
    assert response.status_code == 200
    assert len(response.json()) == int(mode == 'video')
    if mode == 'video':
        assert response.json()[0]['task_modes'] == ['video']


def test_unknown_http_mode_is_rejected_before_db(api):
    assert get(api, task_mode='arbitrary').status_code == 422
    api[8].assert_not_called()


def test_media_binding_mode_does_not_delete_other_pins(binding_api):
    env, repo, *_ = binding_api
    row = candidate(catalog_metadata={'task_modes': ['image-i2i']})
    repo.catalog_candidates.return_value = [row]
    repo.bindings.return_value[0].update(row.model_dump())
    assert call(binding_api).json()[0]['available'] is False
    assert call(binding_api, params={'task_mode': 'image-i2i'}).json()[0]['available'] is True
    assert call(binding_api, 'POST', json={'skill_id': 'report', 'revision': 'v1'}).status_code == 409
    assert call(binding_api, 'POST', params={'task_mode': 'image-i2i'},
                json={'skill_id': 'report', 'revision': 'v1'}).status_code == 201
    repo.remove_binding.assert_not_called()


@pytest.mark.parametrize('generation,mode,images,expected', [
    ('chat', None, False, 'smart'), ('image', None, False, 'image-t2i'),
    ('image', None, True, 'image-i2i'), ('image', 'image-t2i', True, 'image-t2i'),
    ('image_ecom', None, True, 'image-ecom'), ('video', None, False, 'video'),
])
def test_mode_is_bounded_by_actual_generation_type(generation, mode, images, expected):
    parts = [{'type': 'image'}] if images else []
    assert media.resolve_task_mode(generation, mode, parts) == expected


@pytest.mark.parametrize('generation,mode', [('chat', 'video'), ('video', 'image-t2i'),
                                            ('image', 'image-ecom'), ('image', 'image-i2i')])
def test_mode_cannot_change_generation_or_skip_required_image(generation, mode):
    with pytest.raises(HTTPException) as error:
        media.resolve_task_mode(generation, mode, [])
    assert error.value.status_code == 422


def test_idempotency_accounts_for_mode_but_ignores_private_audit():
    request = GenerateRequest(content=[TextPart(text='')], generation_type=GenerationType.IMAGE)
    fingerprint = MessageIdempotencyService.build_fingerprint
    assert fingerprint('c', request) != fingerprint('c', request.model_copy(update={'skill_task_mode': 'image-t2i'}))
    assert fingerprint('c', request) == fingerprint('c', request.model_copy(update={
        'params': {'_skill_task_mode': 'video', '_media_skills': [{'skill_id': 'forged'}]},
    }))


@pytest.fixture
def execution(monkeypatch):
    settings = get_settings().model_copy(update={'skill_catalog_enabled': True, 'skill_runtime_enabled': True,
                                               'skill_recommendations_enabled': True})
    monkeypatch.setattr(media, 'get_settings', lambda: settings)
    monkeypatch.setattr('core.config.get_settings', lambda: settings)
    authority = Mock(return_value=str(ORG))
    monkeypatch.setattr(media, '_conversation_org', authority)
    source = Source([item(task_modes=MODES, model_selectable=False)], body='Create a clean product image.')
    factory = Mock(return_value=source)
    monkeypatch.setattr('services.skills.runtime_source.ActorSkillSource', factory)
    session = SimpleNamespace(close=AsyncMock())
    gateway = Mock(open_chat=Mock(return_value=session))
    monkeypatch.setattr('services.model_gateway.get_model_gateway', lambda: gateway)
    collect = AsyncMock(return_value=SimpleNamespace(content='{"prompt":"white background","input_required":""}'))
    monkeypatch.setattr('services.model_gateway._collect_stream_response', collect)
    handler = SimpleNamespace(db=object(), org_id=str(ORG))
    return settings, source, factory, authority, session, gateway, collect, handler


async def prepare(execution, params=None, mode='image-t2i', images=None):
    return await media.prepare_media_prompt(execution[-1], conversation_id='conv', user_id=str(ACTOR),
        params=params if params is not None else {'_selected_skill': {'skill_id': 'report', 'revision': 'v1'}},
        metadata=TaskMetadata(client_task_id='task', turn_id='turn'), task_mode=mode,
        prompt='', image_urls=images or [])


async def test_manual_activation_uses_same_runtime_and_only_prompt_output(execution):
    params = {'_selected_skill': {'skill_id': 'report', 'revision': 'v1'}, 'num_images': 2, 'aspect_ratio': '16:9'}
    assert await prepare(execution, params) == 'white background'
    _, source, factory, _, session, gateway, collect, _ = execution
    source.load.assert_awaited_once()
    assert params['num_images'] == 2 and params['aspect_ratio'] == '16:9'
    assert params['_media_skills'][0]['revision'] == 'v1'
    assert set(params['_media_skills'][0]) == {'skill_id', 'revision', 'body_sha256', 'rendered_sha256', 'selection'}
    messages = collect.await_args.kwargs['messages']
    assert 'Create a clean product image.' in json.dumps(messages)
    assert collect.await_args.kwargs['tools'] == []
    assert factory.call_args.kwargs['task_mode'] == 'image-t2i'
    assert factory.call_args.args[1].authorized_tool_names == frozenset()
    session.close.assert_awaited_once()


async def test_no_selection_and_no_binding_never_loads_or_requests_prompt(execution):
    execution[1].discover.side_effect = RuntimeError('optional catalog failure')
    assert await prepare(execution, {}) == ''
    execution[1].discover.assert_not_awaited()
    execution[1].load.assert_not_awaited()
    execution[6].assert_not_awaited()


async def test_matching_session_pin_is_explicit_intent_without_manual_choice(execution):
    execution[1].session_bindings.return_value = [item('fixed', task_modes=['image-t2i'], model_selectable=False)]
    params = {}
    assert await prepare(execution, params) == 'white background'
    assert execution[1].load.await_args.args[0].skill_key == 'fixed'
    assert params['_media_skills'][0]['selection'] == 'session'


@pytest.mark.parametrize('failure', ['SKILL_ACCESS_DENIED', 'SKILL_PINNED_METADATA_MISMATCH',
                                    'SKILL_BODY_HASH_MISMATCH', 'SKILL_LOAD_UNAVAILABLE'])
async def test_load_failure_stops_before_model_with_safe_error(execution, failure):
    execution[1].load.side_effect = SkillError(failure)
    with pytest.raises(HTTPException, match='所选 Skill'):
        await prepare(execution)
    execution[6].assert_not_awaited()


async def test_stale_manual_revision_cannot_load_latest(execution):
    with pytest.raises(HTTPException):
        await prepare(execution, {'_selected_skill': {'skill_id': 'report', 'revision': 'v2'}})
    execution[1].load.assert_not_awaited()


@pytest.mark.parametrize('failure', [None, HTTPException(403, '无权限')])
async def test_org_authority_is_checked_before_activation(execution, failure):
    authority = execution[3]
    if failure:
        authority.side_effect = failure
    else:
        authority.return_value = None
    with pytest.raises(HTTPException) as error:
        await prepare(execution)
    assert error.value.status_code == 403
    execution[2].assert_not_called()
    execution[6].assert_not_awaited()


async def test_foreign_execution_org_cannot_load_skill(execution):
    execution[-1].org_id = str(OTHER_ORG)
    with pytest.raises(HTTPException):
        await prepare(execution)
    execution[2].assert_not_called()


async def test_disabled_catalog_has_no_db_or_body_io(execution):
    execution[0].skill_catalog_enabled = False
    assert await prepare(execution, {}) == ''
    with pytest.raises(HTTPException, match='暂未开放'):
        await prepare(execution)
    execution[3].assert_not_called()
    execution[2].assert_not_called()


async def test_disabled_runtime_preserves_mandatory_session_intent(execution):
    execution[0].skill_runtime_enabled = False
    execution[1].session_bindings.return_value = [item('fixed', task_modes=['image-t2i'])]
    with pytest.raises(HTTPException, match='暂未开放'):
        await prepare(execution, {})
    execution[1].load.assert_not_awaited()
    execution[6].assert_not_awaited()


@pytest.mark.parametrize('output', [
    '{}', '{"prompt":"ok","model":"forged"}', '{"prompt":"ok","image_urls":["https://forged"]}',
    '{"prompt":"ok","num_images":4}', '{"prompt":123}', '```json\n{"prompt":"ok"}\n```',
    '{"prompt":"","input_required":"请上传参考图"}',
])
async def test_invalid_or_missing_input_response_cannot_reach_generator(execution, output):
    execution[6].return_value.content = output
    with pytest.raises(HTTPException):
        await prepare(execution)
    execution[4].close.assert_awaited_once()


async def test_model_failure_is_safe_and_closed(execution):
    execution[6].side_effect = RuntimeError('secret db /private/nas')
    with pytest.raises(HTTPException) as error:
        await prepare(execution)
    assert error.value.status_code == 503 and 'secret' not in error.value.detail
    execution[4].close.assert_awaited_once()


async def test_image_handler_uses_prepared_prompt_and_preserves_batch_settings(execution, monkeypatch):
    from services.handlers.image_handler import ImageHandler
    handler = ImageHandler(execution[-1].db)
    handler.org_id = str(ORG)
    handler._check_balance = Mock()
    handler._build_callback_url = Mock(return_value='https://callback.test/image')
    handler._create_single_task = AsyncMock(return_value='provider-task')
    adapter = SimpleNamespace(provider=SimpleNamespace(value='kie'), supports_resolution=False, close=AsyncMock())
    monkeypatch.setattr('services.adapters.factory.create_image_adapter', Mock(return_value=adapter))
    params = {'_selected_skill': {'skill_id': 'report', 'revision': 'v1'}, 'num_images': 2, 'aspect_ratio': '16:9'}
    content = [TextPart(text='')]
    result = await handler.start('message', 'conv', str(ACTOR), content, params,
                                 TaskMetadata(client_task_id='task', turn_id='turn'))
    assert result == 'task' and handler._create_single_task.await_count == 2
    for call in handler._create_single_task.await_args_list:
        assert call.kwargs['prompt'] == 'white background'
        assert call.kwargs['generate_kwargs']['size'] == '16:9'
        assert call.kwargs['params']['_media_skills'][0]['revision'] == 'v1'
    assert content[0].text == ''
    execution[1].load.assert_awaited_once()
    execution[6].assert_awaited_once()


async def test_image_handler_stops_before_credit_lock_and_provider_on_missing_input(execution, monkeypatch):
    from services.handlers.image_handler import ImageHandler
    handler = ImageHandler(execution[-1].db)
    handler.org_id = str(ORG)
    handler._check_balance = Mock()
    provider = Mock()
    monkeypatch.setattr('services.adapters.factory.create_image_adapter', provider)
    execution[6].return_value.content = '{"input_required":"请上传产品照片"}'
    with pytest.raises(HTTPException, match='请上传产品照片'):
        await handler.start('m', 'conv', str(ACTOR), [TextPart(text='')],
                            {'_selected_skill': {'skill_id': 'report', 'revision': 'v1'}}, TaskMetadata(turn_id='turn'))
    handler._check_balance.assert_not_called()
    provider.assert_not_called()


async def test_video_handler_uses_method_without_changing_duration_or_model(execution, monkeypatch):
    from services.handlers.video_handler import VideoHandler
    handler = VideoHandler(execution[-1].db)
    handler.org_id = str(ORG)
    for name in ('_check_balance', '_save_task', '_update_task_by_id'):
        setattr(handler, name, Mock())
    handler._lock_credits = Mock(return_value='tx')
    handler._build_callback_url = Mock(return_value='https://callback.test/video')
    adapter = SimpleNamespace(provider=SimpleNamespace(value='kie'), close=AsyncMock(),
                              generate=AsyncMock(return_value=SimpleNamespace(task_id='provider-video')))
    factory = Mock(return_value=adapter)
    monkeypatch.setattr('services.adapters.factory.create_video_adapter', factory)
    cost = Mock(return_value={'user_credits': 10})
    monkeypatch.setattr('config.kie_models.calculate_video_cost', cost)
    params = {'model': 'sora-2-text-to-video', 'n_frames': '150', 'aspect_ratio': 'portrait', 'remove_watermark': False,
              '_selected_skill': {'skill_id': 'report', 'revision': 'v1'}}
    assert await handler.start('m', 'conv', str(ACTOR), [TextPart(text='')], params,
                               TaskMetadata(client_task_id='task', turn_id='turn')) == 'task'
    factory.assert_called_once_with('sora-2-text-to-video')
    cost.assert_called_once_with(model_name='sora-2-text-to-video', duration_seconds=15)
    assert adapter.generate.await_args.kwargs['prompt'] == 'white background'
    assert adapter.generate.await_args.kwargs['aspect_ratio'] == 'portrait'
    assert adapter.generate.await_args.kwargs['remove_watermark'] is False
    assert handler._save_task.call_args.kwargs['params']['_media_skills'][0]['revision'] == 'v1'


async def test_ecom_skill_reuses_planning_and_returns_confirmation_without_generating(execution, monkeypatch):
    from services.handlers.ecom_image_handler import EcomImageHandler
    from services.agent.image.image_agent import ImageAgent
    handler = EcomImageHandler(MagicMock())
    handler.org_id = str(ORG)
    handler.on_complete = AsyncMock()
    handler.on_error = AsyncMock()
    request_plan = AsyncMock(return_value=(SimpleNamespace(content=json.dumps({
        'product_insight': '商品', 'images': [{'prompt': 'white background', 'role': '主图'}],
    })), 'planning-model', None))
    monkeypatch.setattr(ImageAgent, '_request_plan', request_plan)
    monkeypatch.setattr(ImageAgent, '_read_style_directive', Mock(return_value=''))
    provider = Mock()
    monkeypatch.setattr('services.adapters.factory.create_image_adapter', provider)
    await handler._phase1_plan('m', 'conv', str(ACTOR), [{'type': 'image', 'url': 'https://cdn.test/product.png'}],
        {'_selected_skill': {'skill_id': 'report', 'revision': 'v1'}}, 'task',
        metadata=TaskMetadata(client_task_id='task', turn_id='turn'))
    handler.on_error.assert_not_awaited()
    handler.on_complete.assert_awaited_once()
    parts = handler.on_complete.await_args.args[1]
    assert parts[0]['type'] == 'ecom_plan'
    assert 'Create a clean product image.' in json.dumps(request_plan.await_args.args[1])
    execution[6].assert_not_awaited()  # no extra prompt-generation call for ecom
    provider.assert_not_called()


@pytest.mark.parametrize('generation,mode', [(GenerationType.IMAGE, 'image-t2i'),
                                             (GenerationType.IMAGE_ECOM, 'image-ecom'), (GenerationType.VIDEO, 'video')])
async def test_http_media_intent_overwrites_private_spoof(monkeypatch, generation, mode):
    from api.routes import message as route
    from api.deps import OrgContext
    from tests.test_message_routes import _make_request, _make_message
    body = GenerateRequest(content=[TextPart(text='')], generation_type=generation, skill_task_mode=mode,
        selected_skill={'skill_id': 'report', 'revision': 'v1'},
        params={'_selected_skill': {'skill_id': 'forged'}, '_skill_task_mode': 'smart', '_media_skills': ['forged']})
    monkeypatch.setattr(route, 'load_control_tasks', lambda *a, **kw: SimpleNamespace(running=None, paused=None))
    monkeypatch.setattr(route, 'resolve_generation_context', AsyncMock(return_value=generation))
    monkeypatch.setattr(route, 'get_handler', Mock(return_value=object()))
    monkeypatch.setattr(route, 'get_conversation_service', Mock())
    monkeypatch.setattr(route, 'prepare_generation_request', AsyncMock(return_value=(object(), {}, _make_message('input'))))
    monkeypatch.setattr(route, 'prepare_assistant_message', AsyncMock(return_value=('output', _make_message('output'))))
    start = AsyncMock(return_value='task')
    monkeypatch.setattr(route, 'start_generation_task', start)
    await route._do_generate_message(_make_request(), 'conv', body, OrgContext(user_id='u'), object(), 'u')
    params = start.await_args.kwargs['params']
    assert params['_selected_skill'] == {'skill_id': 'report', 'revision': 'v1'}
    assert params['_skill_task_mode'] == mode
    assert '_media_skills' not in params


async def test_prompt_adapter_open_failure_is_safe(execution):
    execution[5].open_chat.side_effect = RuntimeError('private API key path')
    with pytest.raises(HTTPException) as error:
        await prepare(execution)
    assert error.value.status_code == 503
    assert 'private' not in error.value.detail
    execution[6].assert_not_awaited()


async def test_ecom_missing_input_does_not_offer_a_generation_plan(monkeypatch):
    from services.agent.image.image_agent import ImageAgent
    agent = ImageAgent(db=None, user_id='u', conversation_id='c')
    monkeypatch.setattr(agent, '_read_style_directive', Mock(return_value=''))
    request = AsyncMock(return_value=(SimpleNamespace(content=json.dumps({
        'input_required': '请提供商品尺寸', 'images': [{'prompt': 'should not generate'}],
    })), 'model', None))
    monkeypatch.setattr(agent, '_request_plan', request)
    result = await agent.ecom_plan('', ['https://cdn.test/item.png'],
                                   skill_messages=[{'role': 'system', 'content': 'Required product size.'}])
    assert result.status == 'error' and result.summary == '请提供商品尺寸'
    assert not result.metadata.get('ecom_plan')
