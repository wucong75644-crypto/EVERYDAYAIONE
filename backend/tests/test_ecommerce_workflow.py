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


def test_workflow_prompt_reflects_preloaded_skill_and_server_publication():
    from services.agent.image.ecommerce_planner.workflow import EcommerceWorkflow
    workflow=EcommerceWorkflow.__new__(EcommerceWorkflow)
    workflow.value=SimpleNamespace(kind='main_images',skill={'skill_key':'ecommerce-main-images'},plan_id=None,mode='plan_only')
    workflow.server_selected=lambda:True
    workflow.server_skill_key=lambda:'ecommerce-main-images'
    prompt=json.loads(workflow.context_message()['content'].split('\n',1)[1])['instruction']
    assert '已激活，无需再次调用 activate_skill' in prompt
    assert '后台保存和发布' in prompt and '不调用 save_task_text' in prompt
    workflow.value.skill=None
    assert '先激活该 Skill' in workflow.context_message()['content']


@pytest.mark.asyncio
async def test_manual_entry_freezes_the_same_workflow_before_skill_activation():
    from services.agent.image.ecommerce_planner.workflow import EcommerceWorkflow,WorkflowBinding,KEY
    from services.skills.coordination import ActionContext
    from unittest.mock import Mock
    workflow=EcommerceWorkflow.__new__(EcommerceWorkflow)
    workflow.value=WorkflowBinding(kind='main_images',root_task_id=str(uuid4()),window_task_id=str(uuid4()),generation_run_id=str(uuid4()))
    skills=SimpleNamespace(action_context=ActionContext(action='other'),set_action=Mock(),manual_skill_id='ecommerce-main-images',
        workflow_method=lambda _:None,bind_workflow=Mock())
    workflow.runtime=SimpleNamespace(skill_coordination=True,skill_runtime=skills,workflow_projection=workflow.value.model_dump(mode='json'))
    workflow.request=SimpleNamespace(params={},replay_context=None)
    workflow.route={'kind':'main_images'}
    workflow.server_skill_key=lambda:None
    assert not workflow.requires_server_binding()  # No active method or plan yet.
    await workflow.coordinate(skills)
    assert workflow.request.params[KEY]==workflow.runtime.workflow_projection
    skills.bind_workflow.assert_called_once_with(workflow.runtime.workflow_projection)


@pytest.mark.asyncio
@pytest.mark.parametrize('text', ['把第一个方案生成图片，按保存原文执行。', '第一个和第三个做成图吧。', '就用刚才那套，做出来看看。'])
async def test_numbered_execution_keeps_ready_server_identity_without_reauthoring(monkeypatch,text):
    from unittest.mock import MagicMock,Mock
    from schemas.message import TextPart
    from services.agent.image.ecommerce_planner.workflow import EcommerceWorkflow,WorkflowBinding
    task,user,conv,prior,plan,item=[str(uuid4()) for _ in range(6)]
    old=WorkflowBinding(kind='main_images',root_task_id=prior,window_task_id=prior,generation_run_id=str(uuid4()),
        plan_id=plan,skill={'skill_key':'ecommerce-main-images'})
    q=SimpleNamespace(task_id=task,user_id=user,conversation_id=conv,params={},selected_skill=None,replay_context=None,
        content=[TextPart(text=text)])
    runtime=SimpleNamespace(skill_coordination=True,skill_runtime=None)
    workflow=EcommerceWorkflow(SimpleNamespace(db=Mock(),org_id=None),q,runtime,None)
    query=MagicMock()
    for method in ['select','eq','neq','order','limit','single','maybe_single']:getattr(query,method).return_value=query
    ready={'id':plan,'status':'ready','plan_revision':1,'items':[
        {'position':i,'item_id':item if i==1 else str(uuid4()),'name':f'方案{i}'} for i in (1,2,3)]}
    query.execute.side_effect=[SimpleNamespace(data={'id':task,'user_id':user,'org_id':None,'conversation_id':conv,
        'status':'running','input_message_id':str(uuid4()),'request_params':{}}),
        SimpleNamespace(data={'role':'user','content':[{'type':'text','text':q.content[0].text}]}),
        SimpleNamespace(data=[{'id':prior,'request_params':{'_ecom_workflow':old.model_dump(mode='json')}}]),
        SimpleNamespace(data={'id':plan,'status':'ready','current_stage':3,'image_count':3}),SimpleNamespace(data=ready)]
    workflow.scope=SimpleNamespace(table=lambda _:query)
    router=AsyncMock(return_value={'kind':'main_images','mode':'input','reuse_through_stage':0,'intent':'execute_saved'})
    monkeypatch.setattr('services.agent.image.ecommerce_planner.workflow.classify_workflow',router)
    await workflow.initialize()
    assert workflow.value.plan_id==plan and workflow.value.generation_run_id==old.generation_run_id
    assert workflow.value.mode=='input' and workflow.value.reset_window is False
    assert workflow.ready_execution==ready
    workflow.server_selected=lambda:True
    workflow.activated=AsyncMock()
    assert not await workflow.needs_planner([])
    body=json.loads(workflow.context_message()['content'].split('\n',1)[1])
    assert body['items'][0]['plan_source']=={'plan_id':plan,'revision':1,'item_id':item}


@pytest.mark.asyncio
async def test_manual_image_writing_uses_the_same_scope_router_as_other_entries(monkeypatch):
    from unittest.mock import MagicMock,Mock
    from schemas.message import TextPart,ImagePart
    from services.agent.image.ecommerce_planner.workflow import EcommerceWorkflow
    task,user,conv=[str(uuid4()) for _ in range(3)]
    q=SimpleNamespace(task_id=task,user_id=user,conversation_id=conv,params={},replay_context=None,
        selected_skill=SimpleNamespace(skill_id='ecommerce-main-images'),
        content=[TextPart(text='参考产品图写3套主图方案，先只写文字。'),ImagePart(name='原图',url='https://example.invalid/product.png')])
    runtime=SimpleNamespace(skill_coordination=True,skill_runtime=None)
    workflow=EcommerceWorkflow(SimpleNamespace(db=Mock(),org_id=None),q,runtime,None)
    query=MagicMock()
    for method in ['select','eq','neq','order','limit','single']:getattr(query,method).return_value=query
    query.execute.side_effect=[SimpleNamespace(data={'id':task,'user_id':user,'org_id':None,'conversation_id':conv,
        'status':'running','input_message_id':str(uuid4()),'request_params':{}}),
        SimpleNamespace(data={'role':'user','content':[p.model_dump(mode='json') for p in q.content]}),SimpleNamespace(data=[])]
    workflow.scope=SimpleNamespace(table=lambda _:query)
    router=AsyncMock(return_value={'kind':'main_images','mode':'new','reuse_through_stage':0,'intent':'write'})
    monkeypatch.setattr('services.agent.image.ecommerce_planner.workflow.classify_workflow',router)
    await workflow.initialize()
    assert workflow.value.kind=='main_images' and runtime.workflow_projection['kind']=='main_images'
    router.assert_awaited_once()


def test_current_user_message_as_explicit_source_is_included_exactly_once():
    from services.agent.image.ecommerce_planner.inputs import text_message_ids
    assert text_message_ids(['past','current'],'current')==['past','current']
    assert text_message_ids([],'current')==['current']
    # Other IDs remain subject to the native scoped resolver, never discarded.
    assert text_message_ids(['foreign','current'],'current')==['foreign','current']


def test_planner_snapshots_delivered_appends_verbatim_with_stable_source_ids():
    import hashlib
    from services.task_execution.user_changes import UserChanges,UserChange
    from services.agent.image.ecommerce_planner.inputs import with_user_changes,model_input,source_bindings
    owner,other=uuid4(),uuid4()
    text='背景改为浅米灰色，仍然只写文字。'
    def change(task,delivered):
        return UserChange(event_id=uuid4(),owner_task_id=task,message=text,
            message_sha256=hashlib.sha256(text.encode()).hexdigest(),delivered=delivered)
    accepted=change(owner,True)
    changes=UserChanges(root_task_id=owner,items=(accepted,change(other,True),change(owner,False)))
    original=[{'message_id':str(uuid4()),'parts':[{'content_index':0,'text':'原始要求16:9、2K。'}]}]
    messages=with_user_changes(original,changes,str(owner))
    assert len(messages)==2 and messages[1]['parts'][0]['text']==text
    assert messages[1]['message_sha256']==accepted.message_sha256
    assert with_user_changes(messages,changes,str(owner))==messages
    assert len(original)==1
    assert source_bindings({'messages':messages})['text_2']['source_id']==f'user_change:{accepted.event_id}:0'
    assert model_input({'messages':messages},messages,[])['raw_user_texts'][1]=={
        'source_ref':'text_2','text':text,'source_kind':'user_append'}


@pytest.mark.parametrize('model',['qwen3.8-max','qwen3.8-flash'])
def test_qwen_json_contract_is_explicit_and_invalid_formats_cannot_be_dropped(model):
    from services.adapters.dashscope.chat_adapter import DashScopeChatAdapter
    adapter=DashScopeChatAdapter(api_key='offline',model=model)
    options={'stream':True,'thinking_mode':'disabled','reasoning_effort':None}
    assert 'response_format' not in adapter._request_body([],**options)
    with pytest.raises(ValueError,match='QWEN_RESPONSE_FORMAT_UNSUPPORTED'):
        adapter._request_body([],**options,response_format={'type':'json_schema'})


@pytest.mark.parametrize('ids',[None,'current',['past','past'],['current','current'],[1],list(map(str,range(9)))])
def test_invalid_history_selector_is_not_silently_repaired(ids):
    from services.agent.image.ecommerce_planner.inputs import text_message_ids
    with pytest.raises(ValueError,match='ECOM_PLAN_HISTORY_INVALID'):
        text_message_ids(ids,'current')


@pytest.mark.parametrize('mode,page,expected', [('disabled',False,'qwen3.8-max'),
    ('enabled',False,'openai/gpt-6.1-sol'),(None,False,'openai/gpt-6.1-sol'),
    ('disabled',True,'openai/gpt-6.1-sol')])
def test_nonthinking_route_is_separate_config_and_does_not_mutate_shared_settings(mode,page,expected):
    service=planner();service.settings.ecom_image_planning_model='openai/gpt-6.1-sol'
    service.settings.ecom_image_planning_non_thinking_model='qwen3.8-max';service.page_execution=page
    model,registered=service._select_chat_model({'request_params':{'thinking_mode':mode}})
    assert model==expected and registered.model_id==expected
    assert service.settings.ecom_image_planning_model=='openai/gpt-6.1-sol'
    assert service.chat_thinking_mode==('disabled' if mode=='disabled' and not page else None)
    assert service._select_chat_model({'request_params':{}})[0]=='openai/gpt-6.1-sol'


@pytest.mark.parametrize('selected',['not-registered','openai/gpt-6.1-sol'])
def test_invalid_nonthinking_route_cannot_fallback_or_reserve_credits(selected):
    service=planner();service.settings.ecom_image_planning_non_thinking_model=selected
    with pytest.raises(Exception):service._select_chat_model({'request_params':{'thinking_mode':'disabled'}})
    service._reserve.assert_not_awaited()


@pytest.mark.parametrize('text,expected', [
    ('做一套韩系ins风格主图，符合抖音端口人群','抖音'),
    ('策划适合在淘宝上展示的主图','淘宝'),
    ('面向1688和小红书的用户',None),
    ('不要用于抖音主图',None),
    ('客户示例：“抖音主图”。只分析产品',None),
    ('产品名叫抖音联名本',None),
])
def test_platform_is_frozen_from_explicit_user_evidence(text, expected):
    from services.agent.image.ecommerce_planner.inputs import explicit_chat_platform
    assert explicit_chat_platform([{'parts':[{'text':text}]}]) == expected


def test_multiple_new_platforms_cannot_reuse_a_previous_single_platform():
    from services.agent.image.ecommerce_planner.inputs import explicit_chat_platform
    assert explicit_chat_platform([{'parts':[{'text':'面向1688和小红书的用户'}]}],fallback='抖音') is None
    assert explicit_chat_platform([{'parts':[{'text':'标题大一点'}]}],fallback='抖音') == '抖音'
    assert explicit_chat_platform([{'parts':[{'text':'不要用于抖音主图'}]}],fallback='抖音') is None


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
@pytest.mark.parametrize('thinking_mode', [None, 'disabled'])
async def test_openrouter_planner_uses_real_factory_images_receipt_and_fails_closed(monkeypatch, mode, thinking_mode):
    from services.adapters.openrouter.chat_adapter import OpenRouterChatAdapter
    from services.agent.execution_budget import ExecutionBudget
    from services.agent.image.ecommerce_planner.recovery import PlannerRecoveryError
    from services.model_gateway import ModelGateway
    service = planner()
    service.settings.ecom_image_planning_model = 'openai/gpt-6.1-sol'
    service.chat_thinking_mode = thinking_mode
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
        if thinking_mode == 'disabled':
            from services.model_reasoning import ReasoningUnsupportedError
            with pytest.raises(ReasoningUnsupportedError):
                await call
            assert seen == []
            service._reserve.assert_not_awaited()
            service._finish.assert_not_awaited()
            return
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
        assert body['model'] == 'openai/gpt-6.1-sol'
        assert body['reasoning'] == ({'enabled': False} if thinking_mode == 'disabled' else {'effort': 'medium'})
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


@pytest.mark.parametrize('stage', [1, 2, 3])
@pytest.mark.parametrize('model',['qwen3.8-max','qwen3.8-flash'])
async def test_qwen_planner_off_reaches_actual_adapter_and_keeps_image_input(monkeypatch, stage, model):
    from services.adapters.dashscope.chat_adapter import DashScopeChatAdapter
    from services.model_gateway import ModelGateway
    service=planner();service.settings.ecom_image_planning_model=model
    service.chat_thinking_mode='disabled';seen=[]
    def response(request):
        seen.append(json.loads(request.content))
        assert request.url.path.endswith('/chat/completions')
        return httpx.Response(200,headers={'content-type':'text/event-stream'},content=(
            'data: {"choices":[{"delta":{"content":"output"},"finish_reason":"stop"}]}\n\n'
            'data: {"choices":[],"usage":{"prompt_tokens":100,"completion_tokens":10}}\n\n'
            'data: [DONE]\n\n'))
    async def client(adapter):
        if adapter._client is None:
            adapter._client=httpx.AsyncClient(base_url=adapter._base_url,transport=httpx.MockTransport(response))
        return adapter._client
    monkeypatch.setattr(DashScopeChatAdapter,'_get_client',client)
    monkeypatch.setattr('services.circuit_breaker.is_provider_available',lambda _:True)
    gateway=ModelGateway(event_publisher=SimpleNamespace(publish=lambda event:None))
    monkeypatch.setattr('services.agent.image.ecommerce_planner.service.get_model_gateway',lambda:gateway)
    messages=[{'role':'user','content':[{'type':'input_text','text':'保留真实商品'},
        {'type':'input_image','image_url':'https://example.invalid/product.png'}]}]
    try:
        content,usage=await service._call({'id':str(uuid4())},'lease',stage,'rules',messages)
        assert content=='output' and usage['input_tokens']==100 and usage['output_tokens']==10
        body,=seen
        assert body['enable_thinking'] is False and not body.get('reasoning_effort')
        assert body.get('response_format')==({'type':'json_object'} if stage in {1,3} else None)
        assert body['messages'][0]['content'][1]=={'type':'image_url','image_url':{'url':'https://example.invalid/product.png'}}
        service._reserve.assert_awaited_once()
    finally:
        await gateway.close()


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

@pytest.mark.asyncio
@pytest.mark.parametrize('stage',[1,2,3])
async def test_completed_planning_stage_does_not_wait_for_a_second_model(monkeypatch,stage):
    service=planner()
    reply='完整视觉方向' if stage==2 else '{"status":"ready"}'
    service._call=AsyncMock(return_value=(reply,{'attempt_id':'completed-attempt'}))
    def no_review():
        raise AssertionError('A completed author stage must not start a separate evaluator')
    monkeypatch.setattr('services.model_gateway.get_model_gateway',no_review)
    args=({'id':str(uuid4())},'lease','original','integration',
        {'input_snapshot':{},'product_selling_points':{},'visual_direction':'原稿'},[],[],[])
    if stage==3:value,usage=await service._stage_images(*args)
    else:value,usage=await service._stage(args[0],args[1],stage,*args[2:])
    assert value==('完整视觉方向' if stage==2 else {'status':'ready'})
    assert usage['attempt_id']=='completed-attempt'
    service._call.assert_awaited_once();service._save_attempt.assert_not_awaited()


@pytest.mark.asyncio
async def test_ready_writing_plan_is_delivered_by_host_without_another_model_or_save_call(monkeypatch):
    from dataclasses import replace
    from schemas.message import TextPart
    from tests.test_skill_runtime_actor import actor,prepared,handler,call
    from tests.test_chat_execution_engine import _request
    from services.handlers.chat.execution_engine import _run_loop
    from services.handlers.chat.execution_sink import CollectingExecutionSink
    from services.handlers.chat.stream_session import StreamTotals
    runtime,p,h=actor(),prepared(),handler()
    q=replace(_request(),content=[TextPart(text='策划10张主图，先只写方案不要生成图片')])
    text='## 1. 原方案\n\n完整原文，负面条件和素材说明。'
    result=AgentResult('原生策划ready',metadata={'status':'ready','plan_id':str(uuid4()),
        'server_plan_owner_task_id':str(q.task_id),'server_plan_text':text})
    tc=call('plan_ecommerce_images','{}','planner')
    h._execute_tool_calls.return_value=[(tc,result,False,result.summary)]
    reader=AsyncMock(return_value=('', '', [tc],set()))
    monkeypatch.setattr('services.handlers.chat.execution_engine._read_turn',reader)
    monkeypatch.setattr('services.handlers.chat.execution_engine.compact_tool_context',AsyncMock())
    blocks=[];totals=StreamTotals()
    await _run_loop(handler=h,request=q,prepared=p,cancellation_event=runtime.cancellation_event,
        sink=CollectingExecutionSink(),totals=totals,blocks=blocks,runtime=runtime)
    reader.assert_awaited_once();h._execute_tool_calls.assert_awaited_once()
    assert blocks[-1]['text']==text and blocks[-1]['content_delivery_final'] is True and totals.text==text


@pytest.mark.asyncio
async def test_ready_native_text_recovers_after_tool_checkpoint_without_model_io(monkeypatch):
    from dataclasses import replace
    from schemas.message import TextPart
    from tests.test_skill_runtime_actor import actor,prepared,handler,call
    from tests.test_chat_execution_engine import _request
    from services.handlers.chat.execution_engine import _run_loop
    from services.handlers.chat.execution_sink import CollectingExecutionSink
    from services.handlers.chat.stream_session import StreamTotals
    from services.handlers.chat.native_plan_delivery import stage,KEY
    runtime,p,h=actor(),prepared(),handler()
    q=replace(_request(),content=[TextPart(text='只写10套图片方案，不要生成图片')])
    text='## 1. 第一套\n\n完整原文\r\n禁止变形。'
    result=AgentResult('ready',metadata={'status':'ready','plan_id':str(uuid4()),
        'server_plan_owner_task_id':str(q.task_id),'server_plan_text':text})
    stage(runtime,q,[(call('plan_ecommerce_images','{}','p'),result,False,'ready')])
    assert runtime.checkpoint_base[KEY]['phase']=='pending'
    reader=AsyncMock(side_effect=AssertionError('Recovery cannot sample the model'))
    monkeypatch.setattr('services.handlers.chat.execution_engine._read_turn',reader)
    blocks=[];totals=StreamTotals()
    await _run_loop(handler=h,request=q,prepared=p,cancellation_event=runtime.cancellation_event,
        sink=CollectingExecutionSink(),totals=totals,blocks=blocks,runtime=runtime)
    reader.assert_not_awaited();h._execute_tool_calls.assert_not_awaited()
    assert blocks[-1]['text']==text and runtime.checkpoint_base[KEY]['phase']=='committed'


@pytest.mark.parametrize('delivered',[False,True])
def test_ready_native_plan_publishes_after_consumed_append_but_waits_for_new_input(delivered):
    from dataclasses import replace
    from schemas.message import TextPart
    from tests.test_skill_runtime_actor import actor,call
    from tests.test_chat_execution_engine import _request
    from services.handlers.chat.native_plan_delivery import stage,KEY
    runtime=actor();q=replace(_request(),content=[TextPart(text='只写三套方案，不要生成图片')])
    runtime.task_user_changes=SimpleNamespace(items=(SimpleNamespace(owner_task_id=runtime.task_id,
        event_id=uuid4(),message_sha256='a'*64,message='补充背景颜色为浅灰色，仍然只写方案。',delivered=delivered),))
    result=AgentResult('ready',metadata={'status':'ready','plan_id':str(uuid4()),
        'server_plan_owner_task_id':str(q.task_id),'server_plan_text':'完整原文'})
    stage(runtime,q,[(call('plan_ecommerce_images','{}','p'),result,False,'ready')])
    assert (KEY in runtime.checkpoint_base) is delivered


@pytest.mark.parametrize('text',['先策划10张主图，再把方案导出PDF文件','策划10套方案并发送给同事','策划10套方案后保存到知识库'])
def test_native_ready_projection_does_not_end_compound_delivery_requests(text):
    from dataclasses import replace
    from schemas.message import TextPart
    from tests.test_skill_runtime_actor import actor,call
    from tests.test_chat_execution_engine import _request
    from services.handlers.chat.native_plan_delivery import stage,KEY
    runtime=actor();q=replace(_request(),content=[TextPart(text=text)])
    runtime.workflow_projection={'intent':'write','request_context':{'size':{},'continue_delivery':True}}
    result=AgentResult('ready',metadata={'status':'ready','plan_id':str(uuid4()),
        'server_plan_owner_task_id':str(q.task_id),'server_plan_text':'完整方案'})
    stage(runtime,q,[(call('plan_ecommerce_images','{}','p'),result,False,'ready')])
    assert KEY not in runtime.checkpoint_base


@pytest.mark.asyncio
@pytest.mark.parametrize('changed',['input','owner','body'])
async def test_native_plan_projection_cannot_replay_stale_or_tampered_text(changed):
    from dataclasses import replace
    from types import SimpleNamespace
    from schemas.message import TextPart
    from tests.test_skill_runtime_actor import actor,call
    from tests.test_chat_execution_engine import _request
    from services.handlers.chat.native_plan_delivery import stage,deliver,KEY
    from services.handlers.chat.execution_sink import CollectingExecutionSink
    from services.handlers.chat.stream_session import StreamTotals
    runtime=actor();q=replace(_request(),content=[TextPart(text='只写10套图片方案，不要生图')])
    result=AgentResult('ready',metadata={'status':'ready','plan_id':str(uuid4()),
        'server_plan_owner_task_id':str(q.task_id),'server_plan_text':'完整原文'})
    stage(runtime,q,[(call('plan_ecommerce_images','{}','p'),result,False,'ready')])
    if changed=='input':
        runtime.task_user_changes=SimpleNamespace(items=[SimpleNamespace(event_id=uuid4(),message_sha256='new-user-request')])
    elif changed=='owner':runtime.checkpoint_base[KEY]['owner_task_id']=str(uuid4())
    else:runtime.checkpoint_base[KEY]['text']='被改写的正文'
    blocks=[]
    if changed=='body':
        with pytest.raises(ValueError,match='CONTENT_CHANGED'):
            await deliver(runtime,q,blocks,CollectingExecutionSink(),StreamTotals())
    else:
        assert not await deliver(runtime,q,blocks,CollectingExecutionSink(),StreamTotals())
        assert runtime.checkpoint_base[KEY]['phase']=='superseded'
    assert blocks==[]


def test_registered_flash_off_profile_uses_the_same_registry_provider_and_reasoning_gate():
    service=planner();service.settings.ecom_image_planning_non_thinking_model='qwen3.8-flash'
    model,config=service._select_chat_model({'request_params':{'thinking_mode':'disabled'}})
    assert model==config.provider_model=='qwen3.8-flash'
    assert config.provider.value=='dashscope' and config.supports_vision
    assert service.chat_thinking_mode=='disabled'


@pytest.mark.asyncio
@pytest.mark.parametrize('on_model',['gpt-5-6-luna','removed-on-model'])
async def test_off_profile_does_not_require_unused_on_model_or_its_kie_billing(on_model):
    from services.agent.image.ecommerce_planner.workflow import WorkflowBinding,KEY
    service=planner();owner=service.owner
    owner.image_skill_snapshot=({'skill_key':'ecommerce-main-images'},)
    owner.context_scope='user';owner.workspace_user_id=owner.user_id
    owner.conversation_id=str(uuid4());owner.execution_mode='interactive';owner._current_tool_call_id='planner'
    service.settings.ecom_image_planning_enabled=True
    service.settings.ecom_image_planning_model=on_model
    service.settings.ecom_image_planning_non_thinking_model='qwen3.8-max'
    service.settings.ecom_image_planning_input_credits_per_million=None
    service.settings.ecom_image_planning_output_credits_per_million=None
    saved=WorkflowBinding(kind='main_images',root_task_id=owner.task_id,window_task_id=owner.task_id,
        generation_run_id=str(uuid4()),plan_id=str(uuid4()))
    parent={'user_id':owner.user_id,'org_id':owner.org_id,'conversation_id':owner.conversation_id,
        'status':'running','base_context_revision':0,'input_message_id':str(uuid4()),
        'request_params':{'thinking_mode':'disabled',KEY:saved.model_dump(mode='json')}}
    class Query:
        def __getattr__(self,name):return lambda *a,**kw:self
        def execute(self):return SimpleNamespace(data=parent)
    service.scope=SimpleNamespace(table=lambda _:Query())
    result=await service._run({'references':[]})
    assert result.error_message=='ECOM_PLAN_CONTINUATION_REQUIRED'
    assert service.selected_model=='qwen3.8-max' and service.chat_thinking_mode=='disabled'
    service._reserve.assert_not_awaited();service._finish.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['ordinary_image', 'unrelated', 'unavailable'])
async def test_manual_method_never_forces_product_design_into_server_planning(monkeypatch, kind):
    from unittest.mock import MagicMock, Mock
    from schemas.message import TextPart, ImagePart
    from services.agent.image.ecommerce_planner.workflow import EcommerceWorkflow
    task, user, conv = [str(uuid4()) for _ in range(3)]
    q = SimpleNamespace(task_id=task, user_id=user, conversation_id=conv, params={}, replay_context=None,
        selected_skill=SimpleNamespace(skill_id='ecommerce-main-images'),
        content=[TextPart(text='参考这个产品，帮我想3个新款，把提示词写出来，我先看看。'),
                 ImagePart(name='原图', url='https://example.invalid/product.png')])
    runtime = SimpleNamespace(skill_coordination=True, skill_runtime=None)
    workflow = EcommerceWorkflow(SimpleNamespace(db=Mock(), org_id=None), q, runtime, None)
    query = MagicMock()
    for method in ['select', 'eq', 'neq', 'order', 'limit', 'single']:
        getattr(query, method).return_value = query
    query.execute.side_effect = [SimpleNamespace(data={'id': task, 'user_id': user, 'org_id': None,
        'conversation_id': conv, 'status': 'running', 'input_message_id': str(uuid4()), 'request_params': {}}),
        SimpleNamespace(data={'role': 'user', 'content': [p.model_dump(mode='json') for p in q.content]}),SimpleNamespace(data=[])]
    workflow.scope = SimpleNamespace(table=lambda _: query)
    router = AsyncMock(return_value={'kind': kind, 'mode': 'new', 'reuse_through_stage': 0})
    monkeypatch.setattr('services.agent.image.ecommerce_planner.workflow.classify_workflow', router)
    await workflow.initialize()
    router.assert_awaited_once()
    assert workflow.value is None or workflow.value.kind == 'ordinary_image'
    workflow.activated = AsyncMock()
    assert not await workflow.needs_planner([])
    assert '_ecom_workflow' not in q.params


@pytest.mark.asyncio
@pytest.mark.parametrize('text,route', [
    ('刚才三个方案有什么区别？', {'kind':'unrelated','mode':'new','reuse_through_stage':0}),
    ('帮我重新画一只小猫。', {'kind':'ordinary_image','mode':'new','reuse_through_stage':0}),
    ('另外策划一套新的主图。', {'kind':'main_images','mode':'new','reuse_through_stage':0}),
])
async def test_ready_plan_is_not_executed_for_a_discussion_or_new_task(monkeypatch,text,route):
    from unittest.mock import MagicMock,Mock
    from schemas.message import TextPart
    from services.agent.image.ecommerce_planner.workflow import EcommerceWorkflow,WorkflowBinding,KEY
    task,user,conv,prior,plan=[str(uuid4()) for _ in range(5)]
    old=WorkflowBinding(kind='main_images',root_task_id=prior,window_task_id=prior,
        generation_run_id=str(uuid4()),plan_id=plan,skill={'skill_key':'ecommerce-main-images'})
    q=SimpleNamespace(task_id=task,user_id=user,conversation_id=conv,params={},selected_skill=None,
        replay_context=None,content=[TextPart(text=text)])
    workflow=EcommerceWorkflow(SimpleNamespace(db=Mock(),org_id=None),q,
        SimpleNamespace(skill_coordination=True,skill_runtime=None),None)
    query=MagicMock()
    for method in ['select','eq','neq','order','limit','single','maybe_single']:
        getattr(query,method).return_value=query
    query.execute.side_effect=[SimpleNamespace(data={'id':task,'user_id':user,'org_id':None,
        'conversation_id':conv,'status':'running','input_message_id':str(uuid4()),'request_params':{}}),
        SimpleNamespace(data={'role':'user','content':[{'type':'text','text':text}]}),
        SimpleNamespace(data=[{'id':prior,'request_params':{KEY:old.model_dump(mode='json')}}]),
        SimpleNamespace(data={'id':plan,'status':'ready','current_stage':3,'image_count':3})]
    workflow.scope=SimpleNamespace(table=lambda _:query)
    router=AsyncMock(return_value=route)
    monkeypatch.setattr('services.agent.image.ecommerce_planner.workflow.classify_workflow',router)
    await workflow.initialize()
    router.assert_awaited_once()
    assert workflow.ready_execution is None
    assert workflow.value is None or workflow.value.plan_id is None


@pytest.mark.asyncio
async def test_planning_does_not_silently_replace_a_frozen_user_count():
    from services.agent.image.ecommerce_planner.workflow import WorkflowBinding,KEY
    service=planner();owner=service.owner
    owner.image_skill_snapshot=({'skill_key':'ecommerce-main-images'},)
    owner.context_scope='user';owner.workspace_user_id=owner.user_id
    owner.conversation_id=str(uuid4());owner.execution_mode='interactive';owner._current_tool_call_id='planner'
    service.settings.ecom_image_planning_enabled=True
    service.settings.ecom_image_planning_non_thinking_model='qwen3.8-max'
    frozen=WorkflowBinding(kind='main_images',root_task_id=owner.task_id,window_task_id=owner.task_id,
        generation_run_id=str(uuid4()),intent='write',image_count=3)
    parent={'user_id':owner.user_id,'org_id':owner.org_id,'conversation_id':owner.conversation_id,
        'status':'running','base_context_revision':0,'input_message_id':str(uuid4()),
        'request_params':{'thinking_mode':'disabled',KEY:frozen.model_dump(mode='json')}}
    class Query:
        def __getattr__(self,name):return lambda *a,**kw:self
        def execute(self):return SimpleNamespace(data=parent)
    service.scope=SimpleNamespace(table=lambda _:Query())
    result=await service._run({'references':[],'image_count':10})
    assert result.error_message=='ECOM_IMAGE_COUNT_CONFLICT'
    service._reserve.assert_not_awaited();service._finish.assert_not_awaited()
