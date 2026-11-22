"""Verify off at the serialized transport, not merely hiding thought text."""
import json

import httpx
import pytest

from services.adapters.dashscope.chat_adapter import DashScopeChatAdapter
from services.model_reasoning import ReasoningUnsupportedError, validate_reasoning, openrouter_reasoning
from services.agent.observability.model_request import capture_requests, record_request
from tests.test_dashscope_responses import terminal, SEARCH


@pytest.mark.asyncio
@pytest.mark.parametrize('effort', [None, 'low', 'high'])
async def test_disabled_wins_on_both_dashscope_protocols_and_removes_extractor(effort):
    adapter = DashScopeChatAdapter(api_key='offline-test', model='qwen3.8-max')
    chat = adapter._request_body([], stream=True, reasoning_effort=effort, thinking_mode='disabled')
    assert chat['enable_thinking'] is False
    captured = []
    def transport(req):
        captured.append(json.loads(req.content))
        return httpx.Response(200, text='data: ' + json.dumps(terminal()) + '\n\n')
    adapter._client = httpx.AsyncClient(base_url='https://offline.invalid', transport=httpx.MockTransport(transport))
    rows = []
    try:
        with capture_requests(rows):
            chunks = [c async for c in adapter.stream_chat([], tools=[SEARCH], enable_builtin_tools=True,
                                                          thinking_mode='disabled', reasoning_effort=effort)]
    finally:
        await adapter.close()
    body, = captured
    assert body['reasoning'] == {'effort': 'none'}
    assert not any(t['type'] == 'web_extractor' for t in body['tools'])
    assert rows[0]['tools_schema'] == body['tools']
    assert rows[0]['function_tools'] == sum(t['type'] == 'function' for t in body['tools'])
    assert rows[0]['protocol'] == 'dashscope.responses'


@pytest.mark.parametrize(('model', 'provider'), [('kimi-k3', 'dashscope'), ('gemini-3-flash', 'kie'),
                                                ('gpt-6-luna', 'kie'), ('gemini-2.5-pro', 'google')])
def test_unsupported_off_is_a_local_business_refusal_not_a_hidden_low_setting(model, provider):
    from core.error_classifier import classify_error
    with pytest.raises(ReasoningUnsupportedError) as exc:
        validate_reasoning(model, provider, 'disabled')
    classified = classify_error(exc.value, model_call=True)
    assert not classified.is_retryable and not classified.should_record_breaker and not classified.should_refund
    validate_reasoning(model, provider, None)  # Old checkpoint payloads remain valid.
    validate_reasoning(model, provider, 'deep_think')


def test_openrouter_off_overrides_low_instead_of_excluding_visible_thoughts():
    assert openrouter_reasoning('disabled', 'low') == {'enabled': False}
    assert openrouter_reasoning(None, 'low') == {'effort': 'low'}
    assert openrouter_reasoning(None, None) is None


def test_wire_measurement_is_bounded_private_and_captures_final_tool_surface(monkeypatch):
    published = []
    monkeypatch.setattr('services.agent.observability.chat_stream.publish_chat_stream_summary', published.append)
    rows = []
    tool = {'type': 'function', 'function': {'name': 'private_tool', 'description': 'private schema', 'parameters': {}}}
    body = {'model': 'qwen3.8-max', 'messages': [{'role': 'user', 'content': 'PRIVATE USER CONTENT'}], 'tools': [tool]}
    with capture_requests(rows):
        for _ in range(12):
            record_request(body, 'test.chat')
    assert len(rows) == 8 and len(published) == 12
    assert rows[0]['tools_schema'] == [tool]
    assert 'PRIVATE USER CONTENT' not in json.dumps(rows)
    assert 'private schema' not in json.dumps(published)
    assert rows[0]['role_bytes']['user'] > 0
    assert len(rows[0]['tool_sha256']) == 64
    oversized = body | {'tools': [tool | {'private': 'x' * 70000}]}
    with capture_requests(rows := []):
        record_request(oversized, 'test.chat')
    assert rows[0]['tools_schema'] is None and rows[0]['schema_omitted']


def test_thinking_duration_is_stable_after_stream_end():
    from services.agent.observability.chat_stream import ModelTurnTiming
    now = [0.0]
    timing = ModelTurnTiming(clock=lambda: now[0])
    now[0] = 2; timing.on_thinking()
    now[0] = 4; timing.on_thinking()
    now[0] = 5; timing.on_text()
    now[0] = 80
    assert timing.thinking_duration_ms == 3000


@pytest.mark.asyncio
async def test_unverified_google_off_is_rejected_before_image_conversion_or_network():
    from unittest.mock import AsyncMock
    from services.adapters.google.chat_adapter import GoogleChatAdapter
    adapter = GoogleChatAdapter('gemini-2.5-flash', api_key='offline-test')
    adapter._convert_to_google_format = AsyncMock()
    with pytest.raises(ReasoningUnsupportedError):
        chunks = [c async for c in adapter.stream_chat([], thinking_mode='disabled')]
    adapter._convert_to_google_format.assert_not_awaited()


@pytest.mark.asyncio
async def test_openrouter_disabled_reaches_http_request_even_with_effort():
    from services.adapters.openrouter.chat_adapter import OpenRouterChatAdapter
    adapter = OpenRouterChatAdapter(api_key='offline-test', model='openai/gpt-5.4')
    sent = []
    def transport(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'message': {'content': 'ok'}}], 'usage': {}})
    adapter._client = httpx.AsyncClient(base_url='https://offline.invalid', transport=httpx.MockTransport(transport))
    try:
        await adapter.chat_sync([], thinking_mode='disabled', reasoning_effort='low')
    finally:
        await adapter.close()
    assert sent[0]['reasoning'] == {'enabled': False}


@pytest.mark.asyncio
async def test_smart_retry_excludes_fixed_reasoning_without_fabricating_failures():
    from unittest.mock import MagicMock, AsyncMock
    from schemas.message import TextPart, GenerationType
    from services.handlers.chat_handler import ChatHandler
    from services.intent_router import RetryContext, RoutingDecision
    handler = ChatHandler(MagicMock())
    handler._route_retry = AsyncMock(return_value=RoutingDecision(GenerationType.CHAT, recommended_model='qwen3.8-flash'))
    policy = handler._build_model_retry_policy(params={'thinking_mode': 'disabled'}, content=[TextPart(text='hello')],
                                              task_id='task', conversation_id='conv', user_id='user')
    ctx = RetryContext(True, 'hello', GenerationType.CHAT, failed_attempts=[{'model': 'qwen3.8-max', 'error': 'unavailable'}])
    await policy.route(ctx)
    excluded = handler._route_retry.call_args.kwargs['excluded_models']
    assert 'kimi-k3' in excluded and 'gemini-3-pro' in excluded
    assert 'qwen3.8-flash' not in excluded
    assert ctx.failed_models == ['qwen3.8-max'] and len(ctx.failed_attempts) == 1


@pytest.mark.asyncio
async def test_gateway_never_switches_to_fixed_reasoning_when_router_ignores_off_constraint():
    from services.adapters.dashscope.chat_adapter import DashScopeAPIError
    from services.model_gateway import ModelGatewayError
    from tests.test_model_gateway_retry import setup, adapter
    first, second = adapter(DashScopeAPIError('busy', status_code=503)), adapter()
    session, handler, factory, _ = setup(first, second, models=('qwen3.8-max', 'kimi-k3'))
    try:
        with pytest.raises(ModelGatewayError):
            chunks = [c async for c in session.stream_chat(messages=[], thinking_mode='disabled', reasoning_effort='low')]
    finally:
        await session.close()
    assert factory.call_count == 1
    second.stream_chat.assert_not_called()
