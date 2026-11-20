"""Personal MCP authentication, protocol and paid-submission boundaries."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.testclient import TestClient

from services import kie_video_mcp as service


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    config = SimpleNamespace(kie_api_key="vendor-secret", kie_mcp_token="x" * 40, kie_mcp_callback_base_url="https://example.com")
    monkeypatch.setattr(service, "get_settings", lambda: config)
    return config


def test_auth_and_protocol(settings, monkeypatch, redis):
    upstream = AsyncMock(return_value={"taskId": "protocol-task"})
    monkeypatch.setattr(service, "kie_request", upstream)
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(app):
        async with service.mcp.session_manager.run():
            yield

    app = Starlette(routes=[Mount('/api/kie-mcp', service.authenticated_app)], lifespan=lifespan)
    with TestClient(app) as client:
        assert client.post('/api/kie-mcp/', json={}).status_code == 401
        headers = {"Authorization": "Bearer " + settings.kie_mcp_token,
                   "Accept": "application/json, text/event-stream"}
        assert client.post('/api/kie-mcp/', headers={**headers, "Origin": "https://evil.example"}, json={}).status_code == 403
        response = client.post('/api/kie-mcp/', headers=headers, json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                       "clientInfo": {"name": "test", "version": "1"}},
        })
        assert response.status_code == 200
        assert response.json()['result']['serverInfo']['name'] == 'KIE Video'
        response = client.post('/api/kie-mcp/', headers=headers, json={
            "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        tools = response.json()['result']['tools']
        assert {t['name'] for t in tools} == {'list_video_models', 'create_video', 'get_video_task', 'wait_video_task', 'list_video_tasks'}
        create = next(t for t in tools if t['name'] == 'create_video')
        assert 'request_key' in create['inputSchema']['required']
        for sound in (False, True):
            args = {"request_key": "protocol-sound-" + str(sound),
                    "prompt": "A pencil rolls", "model": "wan/3-0-video"}
            if sound:
                args["sound"] = True
            response = client.post('/api/kie-mcp/', headers=headers, json={
                "jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {"name": "create_video", "arguments": args}})
            assert response.status_code == 200
            assert not response.json()['result'].get('isError', False)
            assert upstream.call_args.kwargs['json']['input']['audio'] is sound
        settings.kie_mcp_token = None
        assert client.post('/api/kie-mcp/', headers=headers, json={}).status_code == 401


class FakeStore:
    def __init__(self):
        self.rows = {}

    async def by_key(self, key):
        return self.rows.get(key)

    async def reserve(self, key, fingerprint, payload, callback_base):
        from datetime import datetime, timezone
        from uuid import uuid4
        if key in self.rows:
            row = self.rows[key]
            if row['fingerprint'] != fingerprint:
                raise ValueError('request_key already belongs to a different request')
            return row, False
        local_id = uuid4()
        row = dict(id=local_id, request_key=key, fingerprint=fingerprint,
                   payload={**payload, 'callBackUrl': callback_base+'/api/kie-mcp-callback/'+str(local_id)},
                   provider_task_id=None, callback_candidate=None, state='submission_unknown',
                   source_urls=[], archived_urls=[], storage_state='pending', created_at=datetime.now(timezone.utc))
        self.rows[key] = row
        return row, True

    async def bind(self, local_id, provider_id):
        row = next(r for r in self.rows.values() if r['id'] == local_id)
        row.update(provider_task_id=provider_id, state='waiting')
        return row

    async def get(self, reference):
        return next((r for r in self.rows.values() if str(r['id']) == reference or r['provider_task_id'] == reference), None)

    async def listing(self, before, limit):
        return list(self.rows.values())[:limit]


@pytest.fixture(autouse=True)
def durable_store(monkeypatch):
    fake = FakeStore()
    monkeypatch.setattr(service, 'store', fake)
    return fake


class FakeRedis:
    def __init__(self):
        self.data = {}

    async def set(self, key, value, nx=False):
        if nx and key in self.data:
            return False
        self.data[key] = value
        return True

    async def get(self, key):
        return self.data.get(key)


@pytest.fixture
def redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(service.RedisClient, 'get_client', AsyncMock(return_value=fake))
    return fake


@pytest.mark.asyncio
async def test_create_deduplicates_and_rejects_changed_payload(monkeypatch, redis):
    upstream = AsyncMock(return_value={"taskId": "task-123"})
    monkeypatch.setattr(service, 'kie_request', upstream)
    first = await service.create_video('unique-request-1', 'A pencil rolling')
    assert first['task_id'] == 'task-123'
    assert (await service.create_video('unique-request-1', 'A pencil rolling'))['reused']
    assert upstream.await_count == 1
    assert upstream.call_args.kwargs['json']['input']['duration'] == '5'
    with pytest.raises(ValueError, match='different request'):
        await service.create_video('unique-request-1', 'Different scene')


@pytest.mark.asyncio
async def test_ambiguous_creation_never_retries(monkeypatch, redis):
    upstream = AsyncMock(side_effect=ValueError('unknown'))
    monkeypatch.setattr(service, 'kie_request', upstream)
    first = await service.create_video('uncertain-request', 'A pencil')
    assert first['state'] == 'submission_unknown'
    assert first['local_task_id']
    repeated = await service.create_video('uncertain-request', 'A pencil')
    assert repeated['reused'] is True
    assert repeated['local_task_id'] == first['local_task_id']
    assert upstream.await_count == 1


@pytest.mark.asyncio
async def test_image_validation_before_submission(monkeypatch, redis):
    upstream = AsyncMock(return_value={"taskId": "image-123"})
    monkeypatch.setattr(service, 'kie_request', upstream)
    with pytest.raises(ValueError, match='HTTPS'):
        await service.create_video('image-request', 'A pencil', model=service.MODELS[1], image_url='file:///etc/passwd')
    assert not redis.data
    await service.create_video('image-request', 'A pencil', model=service.MODELS[1], image_url='https://example.com/pencil.png')
    params = upstream.call_args.kwargs['json']['input']
    assert params['image_urls'] == ['https://example.com/pencil.png']
    assert 'aspect_ratio' not in params


@pytest.mark.asyncio
async def test_query_extracts_only_result_urls(monkeypatch):
    monkeypatch.setattr(service, 'kie_request', AsyncMock(return_value={
        'state': 'success', 'resultJson': json.dumps({'resultUrls': ['https://example.com/video.mp4', 'file:///private']}),
        'param': 'sensitive prompt'}))
    result = await service.get_video_task('task-123')
    assert result['video_urls'] == ['https://example.com/video.mp4']
    assert 'param' not in result


@pytest.mark.asyncio
async def test_vendor_error_is_sanitized(monkeypatch):
    def handler(request):
        return httpx.Response(200, json={'code': 402, 'msg': 'vendor-secret'})
    real_client = httpx.AsyncClient
    monkeypatch.setattr(service.httpx, 'AsyncClient', lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    with pytest.raises(ValueError, match='code=402') as error:
        await service.kie_request('GET', 'recordInfo')
    assert 'vendor-secret' not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize('model,kwargs,expected', [
    ('kling-2.6/text-to-video', {}, {'duration': '5', 'sound': False, 'aspect_ratio': '16:9'}),
    ('kling-2.6/image-to-video', {'image_url': 'https://example.com/start.png', 'sound': True}, {'duration': '5', 'sound': True, 'image_urls': ['https://example.com/start.png']}),
    ('kling-3.0/video', {'duration_seconds': 15, 'resolution': '4k', 'sound': True,
                         'image_url': 'https://example.com/start.png', 'end_image_url': 'https://example.com/end.png'},
     {'duration': '15', 'sound': True, 'mode': '4K', 'multi_shots': False,
      'image_urls': ['https://example.com/start.png', 'https://example.com/end.png']}),
    ('bytedance/seedance-2', {'duration_seconds': 12, 'resolution': '1080p', 'sound': True,
                            'reference_image_urls': ['https://example.com/product.png', 'https://example.com/room.png']},
     {'duration': 12, 'generate_audio': True, 'resolution': '1080p', 'aspect_ratio': '16:9',
      'reference_image_urls': ['https://example.com/product.png', 'https://example.com/room.png']}),
    ('bytedance/seedance-2-fast', {'image_url': 'https://example.com/start.png', 'end_image_url': 'https://example.com/end.png'},
     {'duration': 5, 'generate_audio': False, 'resolution': '720p', 'aspect_ratio': '16:9',
      'first_frame_url': 'https://example.com/start.png', 'last_frame_url': 'https://example.com/end.png'}),

])
async def test_each_model_provider_contract(monkeypatch, redis, model, kwargs, expected):
    upstream = AsyncMock(return_value={'taskId': 'contract-task'})
    monkeypatch.setattr(service, 'kie_request', upstream)
    await service.create_video('contract-request', 'A pencil rolls across a wooden desk', model=model, **kwargs)
    sent = dict(upstream.call_args.kwargs['json'])
    assert '/api/kie-mcp-callback/' in sent.pop('callBackUrl')
    assert sent == {'model': model, 'input': {'prompt': 'A pencil rolls across a wooden desk', **expected}}


@pytest.mark.asyncio
@pytest.mark.parametrize('model,kwargs,reason', [
    ('grok-imagine/text-to-video', {}, 'Unsupported model'),
    ('grok-imagine/image-to-video', {'sound': True}, 'Unsupported model'),
    ('kling-2.6/text-to-video', {'duration_seconds': 15}, 'duration'),
    ('kling-2.6/text-to-video', {'resolution': '1080p'}, 'resolution'),
    ('bytedance/seedance-2-fast', {'resolution': '1080p'}, 'resolution'),
    ('bytedance/seedance-2', {'duration_seconds': -1}, 'duration'),
    ('bytedance/seedance-2', {'reference_image_urls': ['https://example.com/a.png'] * 10}, 'at most 9'),
    ('bytedance/seedance-2', {'image_url': 'https://example.com/start.png', 'reference_image_urls': ['https://example.com/ref.png']}, 'Do not mix'),
    ('kling-3.0/video', {'end_image_url': 'https://example.com/end.png'}, 'needs image_url'),
    ('kling-3.0/video', {'aspect_ratio': '21:9'}, 'aspect_ratio'),
    ('kling-3.0/video', {'reference_image_urls': ['https://example.com/ref.png']}, 'unsupported'),
])
async def test_invalid_choices_rejected_before_paid_request(monkeypatch, redis, model, kwargs, reason):
    upstream = AsyncMock()
    monkeypatch.setattr(service, 'kie_request', upstream)
    with pytest.raises(ValueError, match=reason):
        await service.create_video('invalid-choice', 'A pencil rolls', model=model, **kwargs)
    upstream.assert_not_awaited()
    assert not redis.data


@pytest.mark.asyncio
async def test_model_discovery_exposes_audio_and_capabilities():
    catalog = await service.list_video_models()
    models = {entry['model']: entry for entry in catalog['models']}
    assert len(models) == 11
    assert not any('grok' in model for model in models)
    assert models['bytedance/seedance-2']['reference_images'] == 9
    assert models['bytedance/seedance-2-fast']['resolutions'] == ['480p', '720p']
    tool = next(t for t in await service.mcp.list_tools() if t.name == 'create_video')
    properties = tool.inputSchema['properties']
    assert set(properties['model']['enum']) == set(models)
    assert properties['sound']['default'] is False
    assert 'Grok' not in json.dumps(tool.model_dump())
    assert 'grok' not in json.dumps(properties)
    assert 'sound=true' in tool.description


@pytest.mark.asyncio
@pytest.mark.parametrize('model,kwargs,expected', [
    ('wan/3-0-video', {'duration_seconds': 30, 'image_url': 'https://example.com/start.png', 'end_image_url': 'https://example.com/end.png'},
     {'duration': 30, 'audio': False, 'aspect_ratio': '16:9', 'resolution': '1080P',
      'first_frame_url': 'https://example.com/start.png', 'last_frame_url': 'https://example.com/end.png'}),
    ('wan/3-0-video-prime', {'duration_seconds': 2, 'resolution': '480p',
                            'reference_image_urls': ['https://example.com/product.png'],
                            'reference_video_urls': ['https://example.com/style.mp4'],
                            'reference_audio_urls': ['https://example.com/voice.wav']},
     {'duration': 2, 'audio': False, 'aspect_ratio': '16:9', 'resolution': '480P',
      'reference_image_urls': ['https://example.com/product.png'],
      'reference_video_urls': ['https://example.com/style.mp4'],
      'reference_audio_urls': ['https://example.com/voice.wav']}),
    ('wan/2-6-flash-image-to-video', {'duration_seconds': 15, 'image_url': 'https://example.com/start.png', 'resolution': '720p'},
     {'duration': '15', 'audio': False, 'image_urls': ['https://example.com/start.png'],
      'resolution': '720p', 'multi_shots': False}),
    ('bytedance/seedance-2-5', {'duration_seconds': 30, 'resolution': '4k',
                             'reference_image_urls': ['https://example.com/product.png'] * 30},
     {'duration': 30, 'generate_audio': False, 'aspect_ratio': '16:9', 'resolution': '4k',
      'reference_image_urls': ['https://example.com/product.png'] * 30}),
    ('pixverse-v6-max/text-to-video', {'duration_seconds': 1, 'resolution': '360p', 'aspect_ratio': '2:3'},
     {'duration': 1, 'generate_audio_switch': False, 'quality': '360p', 'aspect_ratio': '2:3'}),
    ('pixverse-v6-max/image-to-video', {'duration_seconds': 15, 'resolution': '1080p', 'image_url': 'https://example.com/start.png'},
     {'duration': 15, 'generate_audio_switch': False, 'quality': '1080p', 'image_urls': ['https://example.com/start.png']}),
])
async def test_new_provider_contracts(monkeypatch, redis, model, kwargs, expected):
    upstream = AsyncMock(return_value={'taskId': 'new-model-task'})
    monkeypatch.setattr(service, 'kie_request', upstream)
    await service.create_video('new-model-request', 'A pencil rolls on a desk', model=model, **kwargs)
    sent = dict(upstream.call_args.kwargs['json'])
    assert '/api/kie-mcp-callback/' in sent.pop('callBackUrl')
    assert sent == {'model': model, 'input': {'prompt': 'A pencil rolls on a desk', **expected}}


@pytest.mark.asyncio
@pytest.mark.parametrize('model,audio_field,image', [
    ('kling-2.6/text-to-video', 'sound', False),
    ('kling-2.6/image-to-video', 'sound', True),
    ('kling-3.0/video', 'sound', False),
    ('bytedance/seedance-2', 'generate_audio', False),
    ('bytedance/seedance-2-fast', 'generate_audio', False),
    ('bytedance/seedance-2-5', 'generate_audio', False),
    ('wan/3-0-video', 'audio', False),
    ('wan/3-0-video-prime', 'audio', False),
    ('wan/2-6-flash-image-to-video', 'audio', True),
    ('pixverse-v6-max/text-to-video', 'generate_audio_switch', False),
    ('pixverse-v6-max/image-to-video', 'generate_audio_switch', True),
])
async def test_every_model_defaults_silent_and_can_enable_audio(monkeypatch, redis, model, audio_field, image):
    upstream = AsyncMock(return_value={'taskId': 'audio-test'})
    monkeypatch.setattr(service, 'kie_request', upstream)
    kwargs = {'image_url': 'https://example.com/start.png'} if image else {}
    await service.create_video('audio-default-request', 'A pencil rolling; sound of pencil on wood.', model=model, **kwargs)
    assert upstream.call_args.kwargs['json']['input'][audio_field] is False
    await service.create_video('audio-enabled-request', 'A pencil rolling; sound of pencil on wood.', model=model, sound=True, **kwargs)
    payload = upstream.call_args.kwargs['json']['input']
    assert payload[audio_field] is True
    assert {key for key in ('sound', 'audio', 'generate_audio', 'generate_audio_switch') if key in payload} == {audio_field}


@pytest.mark.asyncio
@pytest.mark.parametrize('model,kwargs', [
    ('wan/2-6-flash-image-to-video', {}),
    ('wan/3-0-video', {'duration_seconds': 31}),
    ('wan/3-0-video-prime', {'resolution': '4k'}),
    ('wan/3-0-video', {'reference_image_urls': ['https://example.com/ref.png'] * 11}),
    ('wan/3-0-video', {'reference_video_urls': ['https://example.com/ref.mp4'] * 6}),
    ('wan/3-0-video-prime', {'image_url': 'https://example.com/start.png', 'reference_audio_urls': ['https://example.com/voice.wav']}),
    ('bytedance/seedance-2-5', {'reference_image_urls': ['https://example.com/ref.png'] * 31}),
    ('bytedance/seedance-2-5', {'reference_video_urls': ['file:///private/video.mp4']}),
    ('pixverse-v6-max/text-to-video', {'duration_seconds': 16}),
    ('pixverse-v6-max/image-to-video', {'image_url': 'https://example.com/start.png', 'end_image_url': 'https://example.com/end.png'}),
    ('pixverse-v6-max/text-to-video', {'reference_audio_urls': ['https://example.com/voice.wav']}),
])
async def test_new_model_invalid_inputs_do_not_spend(monkeypatch, redis, model, kwargs):
    upstream = AsyncMock()
    monkeypatch.setattr(service, 'kie_request', upstream)
    with pytest.raises(ValueError):
        await service.create_video('invalid-new-model', 'A pencil rolling', model=model, **kwargs)
    assert not redis.data
    upstream.assert_not_awaited()


@pytest.mark.asyncio
async def test_cached_legacy_receipt_failure_does_not_hide_durable_provider_id(monkeypatch, redis):
    original_set = redis.set
    async def flaky_set(key, value, nx=False):
        if not nx:
            raise ConnectionError('cache unavailable')
        return await original_set(key, value, nx=nx)
    monkeypatch.setattr(redis, 'set', flaky_set)
    monkeypatch.setattr(service, 'kie_request', AsyncMock(return_value={'taskId': 'durable-task-123'}))
    created = await service.create_video('cache-outage-request', 'A pencil rolling')
    assert created['task_id'] == 'durable-task-123'
    assert (await service.get_video_task('durable-task-123'))['state'] == 'waiting'


@pytest.mark.asyncio
async def test_old_redis_uncertain_guard_is_never_recreated(monkeypatch, redis):
    import hashlib
    payload = {'model': 'kling-2.6/text-to-video', 'input': {'prompt': 'A pencil rolling',
               'duration': '5', 'sound': False, 'aspect_ratio': '16:9'}}
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    await redis.set('kie:personal-mcp:create:legacy-unknown-request', json.dumps({'fingerprint': fingerprint, 'state': 'submission_unknown'}))
    upstream = AsyncMock()
    monkeypatch.setattr(service, 'kie_request', upstream)
    with pytest.raises(ValueError, match='Previous submission'):
        await service.create_video('legacy-unknown-request', 'A pencil rolling')
    upstream.assert_not_awaited()
