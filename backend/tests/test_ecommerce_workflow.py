"""Regressions for the actual production Skill bypass and KIE 200/401 failure."""
import asyncio
from dataclasses import replace
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from services.adapters.base import StreamChunk
from services.adapters.kie.chat_adapter import KieChatAdapter
from services.adapters.kie.client import KieClient, KieAuthenticationError, KieRateLimitError
from services.agent.agent_result import AgentResult
from services.agent.image.ecommerce_planner.service import EcommerceImagePlanner


@pytest.mark.parametrize('code,error', [(401, KieAuthenticationError), (429, KieRateLimitError)])
@pytest.mark.parametrize('model', ['gpt-5-6-luna', 'gpt-6-luna'])
async def test_kie_http_200_business_failure_keeps_provider_error(code, error, model):
    client = KieClient('test-credential')
    client._client = httpx.AsyncClient(base_url=client.BASE_URL, transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={'code': code, 'msg': 'rejected'})))
    adapter = KieChatAdapter(client, model)
    try:
        with pytest.raises(error) as raised:
            async for _ in adapter.stream_chat([{'role': 'user', 'content': 'test'}]):
                pytest.fail('Provider business error must not produce a successful chunk')
        assert raised.value.status_code == code
        assert raised.value.error_code == str(code)
    finally:
        await adapter.close()


@pytest.mark.parametrize('model', ['gpt-5-6-luna', 'gpt-6-luna'])
async def test_kie_responses_preserves_images_and_terminal_usage(model):
    seen = []
    def response(request):
        assert request.url.path == '/codex/v1/responses'
        seen.append(json.loads(request.content))
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, content=(
            'data: {"type":"response.output_text.delta","delta":"OK"}\n\n'
            'data: {"type":"response.completed","response":{"status":"completed",'
            '"usage":{"input_tokens":13,"output_tokens":2}}}'))
    client = KieClient('test-credential')
    client._client = httpx.AsyncClient(base_url=client.BASE_URL, transport=httpx.MockTransport(response))
    adapter = KieChatAdapter(client, model)
    try:
        chunks = [chunk async for chunk in adapter.stream_chat([{'role': 'user', 'content': [
            {'type': 'input_text', 'text': '原文要求'},
            {'type': 'input_image', 'image_url': 'https://example.invalid/first.png'},
            {'type': 'input_image', 'image_url': 'https://example.invalid/second.png'}]}])]
        assert ''.join(chunk.content or '' for chunk in chunks) == 'OK'
        assert chunks[-1].prompt_tokens == 13 and chunks[-1].completion_tokens == 2
        assert seen[0]['model'] == model and seen[0]['reasoning'] == {'effort':'medium'}
        assert [part.get('image_url') for part in seen[0]['input'][0]['content'][1:]] == [
            'https://example.invalid/first.png', 'https://example.invalid/second.png']
    finally:
        await adapter.close()


@pytest.mark.parametrize('mode', ['completed', 'missing_done', 'length', 'missing_usage', 'missing_cost'])
async def test_openrouter_planner_uses_real_factory_images_receipt_and_fails_closed(monkeypatch, mode):
    from services.adapters.openrouter.chat_adapter import OpenRouterChatAdapter
    from services.agent.execution_budget import ExecutionBudget
    from services.agent.image.ecommerce_planner.recovery import PlannerRecoveryError
    from services.model_gateway import ModelGateway
    service = planner()
    service.settings.ecom_image_planning_model = 'openai/gpt-6.1-sol'
    service.settings.ecom_image_planning_input_credits_per_million = None
    service.settings.ecom_image_planning_output_credits_per_million = None
    service.owner.execution_budget = ExecutionBudget(max_wall_time=600)
    seen = []

    def response(request):
        seen.append((json.loads(request.content), request.headers['authorization'],
            request.extensions['timeout']['read']))
        frames = [{'choices': [{'delta': {'content': 'OK'},
            'finish_reason': 'length' if mode == 'length' else 'stop'}]}]
        if mode != 'missing_usage':
            usage = {'prompt_tokens': 100, 'completion_tokens': 10, 'cost': 0.12}
            if mode == 'missing_cost':
                usage.pop('cost')
            frames.append({'choices': [], 'usage': usage})
        body = ''.join('data: ' + json.dumps(frame) + '\n\n' for frame in frames)
        if mode != 'missing_done':
            body += 'data: [DONE]\n\n'
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, content=body)

    async def client(adapter):
        if adapter._client is None:
            adapter._client = httpx.AsyncClient(base_url=adapter._base_url,
                headers={'Authorization': f'Bearer {adapter._api_key}'},
                timeout=adapter._stream_timeout, transport=httpx.MockTransport(response))
        return adapter._client

    monkeypatch.setattr('services.adapters.factory.get_settings', lambda: SimpleNamespace(
        openrouter_api_key='platform-test-key', openrouter_base_url='https://example.invalid/api/v1',
        openrouter_app_title='Test'))
    monkeypatch.setattr('services.circuit_breaker.is_provider_available', lambda _: True)
    monkeypatch.setattr(OpenRouterChatAdapter, '_get_client', client)
    gateway = ModelGateway(event_publisher=SimpleNamespace(publish=lambda event: None))
    monkeypatch.setattr('services.agent.image.ecommerce_planner.service.get_model_gateway', lambda: gateway)
    messages = [{'role': 'developer', 'content': 'rules'}, {'role': 'user', 'content': [
        {'type': 'input_text', 'text': '要有发财的感觉'},
        {'type': 'input_image', 'image_url': 'https://example.invalid/first.png'},
        {'type': 'input_image', 'image_url': 'https://example.invalid/second.png'}]}]
    try:
        call = service._call({'id': str(uuid4())}, 'lease', 3, 'rules', messages)
        if mode == 'completed':
            content, usage = await call
            assert content == 'OK' and usage['provider'] == 'openrouter'
            assert usage['user_credits'] == 25  # Actual $0.12 at the existing platform rate.
        else:
            with pytest.raises(PlannerRecoveryError, match='ECOM_PLAN_EXECUTION_UNCERTAIN'):
                await call
            assert service._finish.await_args.args[5] == 'uncertain'
        body, authorization, read_timeout = seen[0]
        assert len(seen) == 1 and authorization == 'Bearer platform-test-key'
        assert body['model'] == 'openai/gpt-6.1-sol' and body['reasoning'] == {'effort': 'medium'}
        assert body['messages'][1]['content'] == [{'type': 'text', 'text': '要有发财的感觉'},
            {'type': 'image_url', 'image_url': {'url': 'https://example.invalid/first.png'}},
            {'type': 'image_url', 'image_url': {'url': 'https://example.invalid/second.png'}}]
        assert messages[1]['content'][0]['type'] == 'input_text'  # Input stays unchanged.
        assert read_timeout == 30
        service._reserve.assert_awaited_once()
    finally:
        await gateway.close()


async def test_unregistered_planner_model_rejected_before_database():
    service = planner()
    service.owner.image_skill_snapshot = ({'skill_key': 'ecommerce-main-images'},)
    service.settings.ecom_image_planning_enabled = True
    service.settings.ecom_image_planning_model = 'openai/not-a-registered-model'
    result = await service.run({'references': [], 'image_count': 5})
    assert result.error_message == 'ECOM_IMAGE_PLANNING_MODEL_MISMATCH'
    service._reserve.assert_not_awaited()


def planner():
    owner = SimpleNamespace(db=SimpleNamespace(), user_id=str(uuid4()), org_id=str(uuid4()),
        task_id=str(uuid4()), cancellation_event=asyncio.Event(), execution_budget=None,
        image_skill_snapshot=())
    service = EcommerceImagePlanner(owner)
    service.settings = SimpleNamespace(ecom_image_planning_model='gpt-5-6-luna',
        ecom_image_planning_reasoning='medium', ecom_image_planning_stage_timeout=30,
        ecom_image_planning_input_credits_per_million=11.2,
        ecom_image_planning_output_credits_per_million=67.2)
    service._save_attempt = AsyncMock()
    from datetime import datetime, timedelta, timezone
    service._reserve = AsyncMock(return_value={"outcome": "execute", "ordinal": 1, "remaining_attempts": 2,
        "deadline": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()})
    service._finish = AsyncMock()
    return service


@pytest.mark.parametrize('model,json_mode,legacy', [
    ('kimi-k3', True, False), ('kimi-k3', False, True), ('gemini-3.8-flash', False, False),
    ('gpt-6-luna', False, False),
])
async def test_page_json_mode_only_applies_to_kimi_structured_stages(monkeypatch, model, json_mode, legacy):
    service = planner()
    service.page_execution = True
    service.settings.ecom_image_planning_model = model
    if not legacy:
        service.settings.ecom_analysis_transport = 'inline' if model == 'kimi-k3' else 'kie_upload'
        service.settings.ecom_analysis_json_output = json_mode
        service.settings.ecom_image_planning_reasoning = 'high' if model == 'kimi-k3' else 'medium'
    seen = []
    async def stream(messages, **kwargs):
        seen.append(kwargs)
        yield StreamChunk(content='output', prompt_tokens=100, completion_tokens=10)
    monkeypatch.setattr('services.agent.image.ecommerce_planner.service.get_model_gateway',
        lambda: SimpleNamespace(open_chat=lambda request: SimpleNamespace(stream_chat=stream,
            last_result=SimpleNamespace(status='completed', usage={}), close=AsyncMock())))
    for stage in (1, 2, 3):
        await service._call({'id': str(uuid4())}, 'lease', stage, 'rules', [])
    assert ['response_format' in options for options in seen] == [json_mode, False, json_mode]
    assert all(options['reasoning_effort'] == (None if legacy else service.settings.ecom_image_planning_reasoning)
        for options in seen)


async def test_planner_rejects_unactivated_skill_before_database_or_model():
    result = await planner().run({'references': [], 'image_count': 5})
    assert result.status == 'error' and result.error_message == 'ECOM_PLAN_SKILL_REQUIRED'


async def test_platform_planner_model_credential_does_not_change_data_scope(monkeypatch):
    service = planner()
    requests = []
    async def stream(messages, **kwargs):
        yield StreamChunk(content='output', prompt_tokens=100, completion_tokens=10)
    session = SimpleNamespace(stream_chat=stream, last_result=SimpleNamespace(status='completed', usage={}),
        close=AsyncMock())
    monkeypatch.setattr('services.agent.image.ecommerce_planner.service.get_model_gateway',
        lambda: SimpleNamespace(open_chat=lambda request: requests.append(request) or session))
    content, usage = await service._call({'id': str(uuid4())}, 'lease', 1, 'rules', [])
    assert content == 'output' and usage['user_credits'] > 0
    assert requests[0].org_id is None and requests[0].db is None
    assert requests[0].task_id == service.owner.task_id
    assert service.scope.scope.org_id == service.owner.org_id
    session.close.assert_awaited_once()


async def test_planner_provider_error_records_safe_diagnostics(monkeypatch):
    service = planner()
    async def stream(*args, **kwargs):
        raise KieAuthenticationError('provider message', status_code=401, error_code='401')
        yield
    session = SimpleNamespace(stream_chat=stream, close=AsyncMock())
    monkeypatch.setattr('services.agent.image.ecommerce_planner.service.get_model_gateway',
        lambda: SimpleNamespace(open_chat=lambda request: session))
    with pytest.raises(KieAuthenticationError):
        await service._call({'id': str(uuid4())}, 'lease', 1, 'rules', [])
    facts = service._finish.await_args.args[4]
    assert facts['error_type'] == 'KieAuthenticationError' and facts['http_status'] == 401
    assert facts['provider_error_code'] == '401' and facts['error_code'] == 'KIE_AUTHENTICATION_FAILED'
    assert service._finish.await_args.args[5] == 'rejected'
    service._reserve.assert_awaited_once()
    session.close.assert_awaited_once()


@pytest.mark.parametrize('stage', [1, 2, 3])
async def test_completed_empty_planner_output_enters_bounded_validation_repair(monkeypatch, stage):
    service = planner()
    seen = []
    sessions = []
    replies = iter(['', '完整执行稿' if stage == 2 else '{"status":"ready"}'])
    def open_chat(request):
        reply = next(replies)
        async def stream(messages, **kwargs):
            seen.append(messages)
            yield StreamChunk(content=reply, prompt_tokens=16026,
                completion_tokens=0 if not reply else 10)
        session = SimpleNamespace(stream_chat=stream,
            last_result=SimpleNamespace(status='completed', usage={}), close=AsyncMock())
        sessions.append(session)
        return session
    monkeypatch.setattr('services.agent.image.ecommerce_planner.service.get_model_gateway',
        lambda: SimpleNamespace(open_chat=open_chat))
    evidence = {'input_snapshot': {'image_count': 5}, 'product_selling_points': {'status': 'ready'},
        'visual_direction': '已保存的风格规范'}
    raw = [{'parts': [{'text': '帮我生成5张主图，要求带那种发财风格的，这个是存钱本'}]}]
    refs = [{'source_id': 'source-first', 'role': 'product'},
        {'source_id': 'source-second', 'role': 'style_reference'}]
    urls = ['https://example.invalid/first.png', 'https://example.invalid/second.png']
    args = ({'id': str(uuid4())}, 'lease')
    if stage == 3:
        output, usage = await service._stage_images(*args, 'original', 'integration', evidence, raw, refs, urls)
    else:
        output, usage = await service._stage(*args, stage, 'original', 'integration', evidence, raw, refs, urls)
    assert output == ('完整执行稿' if stage == 2 else {'status': 'ready'})
    assert usage['output_tokens'] == 10 and len(sessions) == 2
    repair = [a for a in service._save_attempt.await_args_list if a.kwargs.get('charge')]
    assert len(repair) == 1 and repair[0].args[3]['validation_error'] == 'ECOM_PLAN_EMPTY_OUTPUT'
    assert repair[0].args[3]['input_tokens'] == 16026 and repair[0].args[3]['output_tokens'] == 0
    assert 'ECOM_PLAN_EMPTY_OUTPUT' in seen[1][0]['content']
    assert seen[0][1] == seen[1][1]  # Empty reply repair keeps raw text and image order intact.
    for session in sessions:
        session.close.assert_awaited_once()


@pytest.mark.parametrize('stage', [1, 2, 3])
async def test_empty_planner_output_stops_after_three_attempts(stage):
    service = planner()
    service._call = AsyncMock(return_value=('', {'user_credits': 1}))
    evidence = {'input_snapshot': {}, 'product_selling_points': {}, 'visual_direction': ''}
    args = ({'id': str(uuid4())}, 'lease')
    with pytest.raises(ValueError, match='PLANNER_IMAGE_JSON_INVALID' if stage == 3 else 'PLANNER_JSON_VALIDATION_FAILED'):
        if stage == 3:
            await service._stage_images(*args, 'original', 'integration', evidence, [], [], [])
        else:
            await service._stage(*args, stage, 'original', 'integration', evidence, [], [], [])
    assert service._call.await_count == 3
    assert service._save_attempt.await_count == 3


async def test_stage_three_final_contract_follows_original_and_exposes_complete_schema():
    from services.agent.image.ecommerce_planner.prompt_resources import resources, wrapper
    from services.agent.image.ecommerce_planner.designs import DesignsOutput
    service = planner()
    service._call = AsyncMock(return_value=('{"status":"ready"}', {}))
    bodies, _schema = resources()
    ref = {'source_id': 'message-uuid:1', 'role': 'product'}
    evidence = {'input_snapshot': {}, 'product_selling_points': {}, 'visual_direction': ''}
    await service._stage_images({'id': str(uuid4())}, 'lease', bodies[2], wrapper(3), evidence, [], [ref], ['image-url'])
    prompt, messages = service._call.await_args.args[3:5]
    assert prompt.startswith(bodies[2]) and prompt.endswith(wrapper(3))
    payload = json.loads(messages[1]['content'][0]['text'])
    schema = payload['output_json_schema']
    assert schema == DesignsOutput.model_json_schema()
    assert schema['additionalProperties'] is False
    assert schema['$defs']['ImageDesign']['additionalProperties'] is False
    assert not {'scheme_markdown', 'references', 'positive_prompt', 'negative_prompt', 'aspect_ratio'} & set(schema['$defs']['ImageDesign']['properties'])
    assert payload['reference_inventory'] == [{'number': 1, 'source_ref': 'image_1', 'role': 'product'}]
    assert 'message-uuid' not in messages[1]['content'][0]['text']


def test_reference_order_repair_identifies_exact_missing_literal():
    from services.agent.image.ecommerce_planner.contracts import validate_images, LABELS
    reference = {'message_id': str(uuid4()), 'content_index': 1, 'source_id': '', 'role': 'product'}
    reference['source_id'] = f"{reference['message_id']}:1"
    image = {'position': 1, 'name': '主图', 'purpose': '展示', 'scheme_markdown': '完整稿',
        'references': [reference], 'positive_prompt': '\n'.join(f'【{label}】内容' for label in LABELS),
        'negative_prompt': '排除变形', 'aspect_ratio': '1:1'}
    with pytest.raises(ValueError) as raised:
        validate_images({'status': 'ready','questions': [], 'images': [image], 'review_records': []},
            {'image_count': 1, 'references': [reference], 'target_size': {'aspect_ratio': '1:1'}})
    assert 'PLANNER_REFERENCE_ORDER_TEXT_MISMATCH' in str(raised.value)
    assert f"输入图片1—{reference['source_id']}" in str(raised.value)
    assert '第1张' in str(raised.value)


async def test_main_model_gets_planner_only_after_skill_activation():
    from config.chat_tools import get_core_tools
    from services.handlers.chat.execution_engine import _apply_skill_context
    from services.handlers.chat.tool_loop import prepare_tool_turn
    from tests.test_skill_runtime import Source, state, item, activate
    from tests.test_skill_runtime_actor import actor, prepared
    assert 'plan_ecommerce_images' not in {t['function']['name'] for t in get_core_tools()}
    tools = ('get_conversation_context', 'plan_ecommerce_images', 'generate_image')
    runtime = actor()
    runtime.skill_runtime = state(Source([item('ecommerce-main-images', tools=tools)], body='Use the planner.'),
        platform_tool_names=tools)
    await runtime.skill_runtime.initialize()
    p = prepared()
    p.execution_context = replace(p.execution_context, feature_flags={
        'ecom_image_planning_enabled': True, 'chat_image_async_enabled': True})
    assert (await runtime.skill_runtime.activate(activate('ecommerce-main-images')))['ok']
    _apply_skill_context(p, runtime.skill_runtime)
    advertised = prepare_tool_turn(core_tools=p.core_tools, discovered_names=p.tool_context.discovered_tools,
        org_id='org-1', turn=1, messages=p.messages, tool_context=p.tool_context,
        permission=p.permission, execution_context=p.execution_context)
    assert 'plan_ecommerce_images' in {t['function']['name'] for t in advertised}


@pytest.mark.parametrize('envelope', [False, True])
async def test_planner_failure_stops_main_loop_without_diy_fallback(monkeypatch, envelope):
    from tests.test_skill_runtime_actor import actor, prepared, handler, call
    from tests.test_chat_execution_engine import _request
    from services.handlers.chat.execution_engine import _run_loop
    from services.handlers.chat.execution_sink import CollectingExecutionSink
    from services.handlers.chat.stream_session import StreamTotals
    runtime, p, h = actor(), prepared(), handler()
    result = AgentResult('主图策划模型鉴权失败。', status='error', metadata={'stop_workflow': True})
    tc = call('plan_ecommerce_images', '{}', 'planner')
    summary = result.summary
    if envelope:
        from services.tools import ToolCall, ToolPolicy, build_legacy_catalog
        from services.tools.result import ToolResult
        from tests.test_tool_execution import context
        ctx = context(feature_flags={'ecom_image_planning_enabled': True, 'chat_image_async_enabled': True})
        result = ToolResult.wrap(result, call=ToolCall('planner','plan_ecommerce_images',{}), context=ctx,
            decision=ToolPolicy(build_legacy_catalog()).decide('plan_ecommerce_images',ctx,{}))
    h._execute_tool_calls.return_value = [(tc, result, True, summary)]
    reader = AsyncMock(return_value=('', '', [tc], set()))
    monkeypatch.setattr('services.handlers.chat.execution_engine._read_turn', reader)
    monkeypatch.setattr('services.handlers.chat.execution_engine.compact_tool_context', AsyncMock())
    totals, blocks = StreamTotals(), []
    await _run_loop(handler=h, request=_request(), prepared=p, cancellation_event=runtime.cancellation_event,
        sink=CollectingExecutionSink(), totals=totals, blocks=blocks, runtime=runtime)
    reader.assert_awaited_once()
    assert totals.text == summary
    assert h._execute_tool_calls.await_count == 1


@pytest.mark.parametrize('active,attempted', [(True, False), (False, True)])
async def test_ecommerce_image_cannot_bypass_plan_source(monkeypatch, active, attempted):
    from services.handlers.image_handler import ImageHandler
    from services.handlers.chat_image_request import ChatImageNotAcceptedError
    parent = {'user_id': str(uuid4()), 'org_id': str(uuid4()), 'conversation_id': str(uuid4()),
        'base_context_revision': 1}
    class Query:
        def __init__(self, data): self.data = data
        def __getattr__(self, name): return lambda *a, **kw: self
        def execute(self): return SimpleNamespace(data=self.data)
    class DB:
        def table(self, table): return Query(parent if table == 'tasks' else ([{'id': 'failed-plan'}] if attempted else []))
    db = DB()
    owner = SimpleNamespace(db=db, user_id=parent['user_id'], workspace_user_id=parent['user_id'],
        org_id=parent['org_id'], conversation_id=parent['conversation_id'], task_id=str(uuid4()),
        context_scope='user', execution_mode='interactive', image_execution_token=str(uuid4()),
        image_skill_snapshot=({'skill_key': 'ecommerce-main-images'},) if active else ())
    monkeypatch.setattr('core.config.get_settings', lambda: SimpleNamespace(
        chat_image_async_enabled=True, chat_image_allowed_user_ids='', ecom_image_planning_enabled=True))
    monkeypatch.setattr('core.db_scope.ScopedDatabaseClient', lambda *a, **kw: db)
    monkeypatch.setattr('services.tools.dispatcher.current_dispatch_call_id', lambda: 'call')
    with pytest.raises(ChatImageNotAcceptedError, match='ECOM_PLAN_SOURCE_REQUIRED'):
        await ImageHandler(db).accept_chat_image(owner, {'mode': 'image_to_image', 'prompt': 'DIY'})


@pytest.mark.parametrize('eof', [False, True])
async def test_kie_responses_nonobject_event_is_provider_error(eof):
    from services.adapters.kie.client import KieAPIError
    client=KieClient('test-credential')
    client._client=httpx.AsyncClient(base_url=client.BASE_URL,transport=httpx.MockTransport(
        lambda request:httpx.Response(200,headers={'content-type':'text/event-stream'},
            content='data: "provider error"'+('' if eof else '\n\n'))))
    adapter=KieChatAdapter(client,'gpt-6-luna')
    try:
        with pytest.raises(KieAPIError,match='KIE_RESPONSES_INVALID_EVENT'):
            _=[chunk async for chunk in adapter.stream_chat([{'role':'user','content':'test'}])]
    finally:
        await adapter.close()


@pytest.mark.parametrize('event', [
    {'type':'error','code':'upstream_error','message':'provider failed'},
    {'type':'response.failed','response':{'error':{'code':'upstream_error','message':'provider failed'}}},
])
async def test_kie_terminal_failure_retains_diagnostics_without_guessing_http_rejection(event):
    from services.adapters.kie.client import KieAPIError
    client=KieClient('test-credential')
    client._client=httpx.AsyncClient(base_url=client.BASE_URL,transport=httpx.MockTransport(
        lambda request:httpx.Response(200,headers={'content-type':'text/event-stream'},
            content='data: '+json.dumps(event)+'\n\n')))
    adapter=KieChatAdapter(client,'gpt-6-luna')
    try:
        with pytest.raises(KieAPIError,match='provider failed') as raised:
            _=[chunk async for chunk in adapter.stream_chat([{'role':'user','content':'test'}])]
        assert raised.value.error_code=='upstream_error'
        assert raised.value.status_code is None
    finally:
        await adapter.close()
