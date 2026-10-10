"""Kimi单份草稿：真实网关边界、失败关闭与合理推断保留。"""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from contextlib import asynccontextmanager
import pytest
from core.exceptions import AppException
from schemas.ecom_requirement import RequirementAssistInput, RequirementImage
from services.agent.image.requirement_assist_service import (
    InvalidRequirementOutput, RequirementAssistService, parse_requirement_result, validate_no_output_urls,
)

@pytest.fixture(autouse=True)
def prepared_media_boundary(monkeypatch):
    # This suite exercises model recovery. Real guarded media preparation and
    # raw-HTTP payloads are covered together in test_analysis_media.py.
    @asynccontextmanager
    async def prepared(*args, **kwargs):
        yield ["https://cdn/product.png", "https://cdn/ref.png"]
    monkeypatch.setattr("services.agent.image.requirement_assist_service.prepare_analysis_media", prepared)

def _input():
    return RequirementAssistInput(
        user_id="u",org_id=None,source_type="detail_project",source_id="p",
        product_images=[RequirementImage(id="p1",original_url="https://cdn/product.png",display_name="产品")],
        reference_images=[RequirementImage(id="r1",original_url="https://cdn/ref.png",display_name="参考",position=2)],
        content_type="default",platform="taobao",language="zh-CN",aspect_ratio="1:1",quality="1k",
        image_count=14,user_requirement="红金发财感觉，不要键盘",project_version=1,
    )

def _payload():
    return {"product_description":"红色存钱本，侧面搭扣",
            "selling_points":[{"feature":"搭扣","benefit":"方便收纳携带","benefit_basis":"inferred"}],
            "creative_requirements":[{"topic":"风格","text":"红金发财感觉","basis":"explicit"}],
            "supplement_questions":[{"question":"尺寸是多少？","why":"完善规格","can_skip":True}]}

@pytest.mark.parametrize("wrapper",["{}","结果如下：{}。","\u0060\u0060\u0060json\n{}\n\u0060\u0060\u0060"])
def test_parse_accepts_single_draft_and_preserves_inferences(wrapper):
    result=parse_requirement_result(wrapper.format(json.dumps(_payload(),ensure_ascii=False)))
    assert result.selling_points[0].benefit=="方便收纳携带"
    assert result.selling_points[0].benefit_basis=="inferred"

@pytest.mark.parametrize("content",['invalid','{"product_description":""}','{"suggestions":[{}, {}, {}]}'])
def test_rejects_invalid_or_old_three_scheme_protocol(content):
    with pytest.raises(InvalidRequirementOutput):
        parse_requirement_result(content)

def test_no_urls_validates_all_generated_fields():
    payload=_payload()
    payload["creative_requirements"][0]["text"]="参考 https://untrusted.test"
    with pytest.raises(InvalidRequirementOutput,match="未授权 URL"):
        validate_no_output_urls(parse_requirement_result(json.dumps(payload)))

def _session(content=None, error=None):
    captured = {}
    async def stream_chat(**kwargs):
        captured.update(kwargs)
        if error:
            raise error
        yield SimpleNamespace(content=content,prompt_tokens=3,completion_tokens=5,finish_reason="stop")
    return SimpleNamespace(stream_chat=stream_chat,close=AsyncMock(),captured=captured,
        last_result=SimpleNamespace(status="completed",partial_output=False))

@pytest.mark.asyncio
async def test_gateway_fixed_kimi_closes_and_receives_images_and_original_text():
    session=_session(json.dumps(_payload()))
    gateway=Mock(open_chat=Mock(return_value=session))
    with patch("services.model_gateway.get_model_gateway",return_value=gateway):
        outcome=await RequirementAssistService().generate(_input())
    request=gateway.open_chat.call_args.args[0]
    assert request.model_id=="kimi-k3"
    assert request.timeout==pytest.approx(120,abs=0.1)
    assert session.captured["reasoning_effort"]=="low"
    user_content=session.captured["messages"][1]["content"]
    assert [part["image_url"]["url"] for part in user_content if part["type"]=="image_url"]==[
        "https://cdn/product.png", "https://cdn/ref.png",
    ]
    assert _input().user_requirement in user_content[0]["text"]
    assert outcome.model=="kimi-k3" and not outcome.fallback_used
    session.close.assert_awaited_once()

@pytest.mark.asyncio
@pytest.mark.parametrize("error,code",[
    (asyncio.TimeoutError(),"REQUIREMENT_ASSIST_TIMEOUT"),
    (RuntimeError("provider failed"),"REQUIREMENT_ASSIST_UNAVAILABLE"),
])
async def test_model_failure_does_not_retry_or_switch_model(error,code):
    session=_session(error=error)
    gateway=Mock(open_chat=Mock(return_value=session))
    with patch("services.model_gateway.get_model_gateway",return_value=gateway):
        with pytest.raises(AppException) as exc:
            await RequirementAssistService().generate(_input())
    assert exc.value.code==code
    gateway.open_chat.assert_called_once()
    session.close.assert_awaited_once()

@pytest.mark.asyncio
async def test_invalid_output_returns_error_without_rewriting():
    session=_session('{"suggestions":[]}')
    gateway=Mock(open_chat=Mock(return_value=session))
    with patch("services.model_gateway.get_model_gateway",return_value=gateway):
        with pytest.raises(AppException) as exc:
            await RequirementAssistService().generate(_input())
    assert exc.value.code=="REQUIREMENT_ASSIST_INVALID_OUTPUT"
    gateway.open_chat.assert_called_once()
    session.close.assert_awaited_once()

@pytest.mark.asyncio
async def test_cancellation_propagates_and_releases_model_session():
    session=_session(error=asyncio.CancelledError())
    with patch("services.model_gateway.get_model_gateway",return_value=Mock(open_chat=Mock(return_value=session))):
        with pytest.raises(asyncio.CancelledError):
            await RequirementAssistService().generate(_input())
    session.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_media_download_rejection_recovers_same_input_with_one_shared_deadline(monkeypatch):
    from services.adapters.dashscope.chat_adapter import DashScopeAPIError
    from services.agent.image import requirement_assist_service as service
    clock = [100.0]
    failure = DashScopeAPIError.from_http(b'{"error":{"code":"InvalidURL.Timeout","message":"download timeout"}}',400)
    first = _session(error=failure)
    original = first.stream_chat
    async def rejected(**kwargs):
        async for chunk in original(**kwargs):
            yield chunk
    # Advance the clock when the provider returns, rather than sleeping 61 seconds.
    async def stream(**kwargs):
        clock[0] += 61
        async for chunk in rejected(**kwargs):
            yield chunk
    first.stream_chat = stream
    second = _session(json.dumps(_payload()))
    gateway = Mock(open_chat=Mock(side_effect=[first,second]))
    async def backoff(seconds): clock[0] += seconds
    monkeypatch.setattr(service.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(service.asyncio,'sleep',backoff)
    with patch('services.model_gateway.get_model_gateway',return_value=gateway):
        outcome=await RequirementAssistService().generate(_input())
    assert outcome.result.product_description==_payload()['product_description']
    requests=[call.args[0] for call in gateway.open_chat.call_args_list]
    assert [r.model_id for r in requests]==['kimi-k3','kimi-k3']
    assert [r.timeout for r in requests]==[120,58]
    assert first.captured['messages']==second.captured['messages']
    first.close.assert_awaited_once();second.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_repeated_rejection_is_bounded_and_provider_reason_remains_available(monkeypatch):
    from services.adapters.dashscope.chat_adapter import DashScopeAPIError
    failure=DashScopeAPIError.from_http(b'{"error":{"code":"InvalidURL.Timeout"},"request_id":"req-final"}',400)
    sessions=[_session(error=failure),_session(error=failure)]
    gateway=Mock(open_chat=Mock(side_effect=sessions))
    monkeypatch.setattr('services.agent.image.requirement_assist_service.asyncio.sleep',AsyncMock())
    with patch('services.model_gateway.get_model_gateway',return_value=gateway):
        with pytest.raises(AppException) as captured:
            await RequirementAssistService().generate(_input())
    assert captured.value.__cause__.request_id=='req-final'
    assert gateway.open_chat.call_count==2
    assert all(s.close.await_count==1 for s in sessions)


@pytest.mark.asyncio
async def test_real_gateway_and_http_adapter_recover_download_failure_without_changing_images(monkeypatch):
    import httpx
    from services.adapters.dashscope.chat_adapter import DashScopeChatAdapter
    from services.model_gateway import ModelGateway
    sent=[]
    def respond(request):
        sent.append(json.loads(request.content))
        if len(sent)==1:
            return httpx.Response(400,json={'error':{'code':'InvalidURL.Timeout','message':'download timeout'},'request_id':'req-download'})
        chunk={'choices':[{'delta':{'content':json.dumps(_payload())},'finish_reason':'stop'}],
               'usage':{'prompt_tokens':5,'completion_tokens':7}}
        return httpx.Response(200,headers={'content-type':'text/event-stream'},
            text='data: '+json.dumps(chunk)+'\n\ndata: [DONE]\n\n')
    def factory(model,**kwargs):
        adapter=DashScopeChatAdapter('test-only',model,stream_timeout=kwargs.get('stream_timeout'))
        adapter._client=httpx.AsyncClient(base_url='https://test.invalid',transport=httpx.MockTransport(respond))
        return adapter
    gateway=ModelGateway(adapter_factory=factory)
    monkeypatch.setattr('services.model_gateway.get_model_gateway',lambda:gateway)
    monkeypatch.setattr('services.agent.image.requirement_assist_service.asyncio.sleep',AsyncMock())
    outcome=await RequirementAssistService().generate(_input())
    assert outcome.result.product_description==_payload()['product_description']
    assert len(sent)==2 and sent[0]==sent[1]
    assert sent[1]['reasoning_effort']=='low'


@pytest.mark.asyncio
async def test_partial_response_does_not_replay_even_if_error_claims_rejection(monkeypatch):
    from services.adapters.dashscope.chat_adapter import DashScopeAPIError
    error=DashScopeAPIError.from_http(b'{"error":{"code":"InvalidURL.Timeout"}}',400)
    session=_session(error=error)
    session.last_result=SimpleNamespace(partial_output=True)
    gateway=Mock(open_chat=Mock(return_value=session))
    with patch('services.model_gateway.get_model_gateway',return_value=gateway):
        with pytest.raises(AppException): await RequirementAssistService().generate(_input())
    gateway.open_chat.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('defect',['comma','newline','field'])
async def test_completed_draft_repairs_once_without_images_or_rewriting_valid_content(defect):
    original=_payload()
    if defect=='comma':
        text=json.dumps(original,ensure_ascii=False)[:-1]+',}'
        fixed=json.dumps(original,ensure_ascii=False)
    elif defect=='newline':
        original['product_description']='红色存钱本\n侧面搭扣'
        text=json.dumps(original,ensure_ascii=False).replace('\\n','\n')
        fixed=json.dumps(original,ensure_ascii=False)
    else:
        broken=json.loads(json.dumps(original));broken['selling_points'][0]['benefit_basis']='推断'
        text=json.dumps(broken,ensure_ascii=False)
        fixed=json.dumps({'patches':[{'path':['selling_points',0,'benefit_basis'],'op':'set','value':'inferred'}]})
    sessions=[_session(text),_session(fixed)]
    gateway=Mock(open_chat=Mock(side_effect=sessions))
    with patch('services.model_gateway.get_model_gateway',return_value=gateway):
        outcome=await RequirementAssistService().generate(_input())
    assert outcome.result.model_dump()==original
    assert gateway.open_chat.call_count==2
    assert all(s.close.await_count==1 for s in sessions)
    messages=sessions[1].captured['messages']
    assert all(isinstance(m['content'],str) for m in messages)
    assert 'image_url' not in json.dumps(messages) and 'cdn/' not in json.dumps(messages)
    assert '红色存钱本' in json.dumps(messages,ensure_ascii=False)
    assert sessions[1].captured['reasoning_effort']=='low'
    assert sessions[1].captured['response_format']=={'type':'json_object'}


@pytest.mark.asyncio
@pytest.mark.parametrize('finish,status,code',[
    ('length','completed','REQUIREMENT_ASSIST_TRUNCATED'),
    ('content_filter','completed','REQUIREMENT_ASSIST_REFUSED'),
    (None,'completed','REQUIREMENT_ASSIST_INCOMPLETE'),
    ('stop','incomplete','REQUIREMENT_ASSIST_INCOMPLETE'),
])
async def test_even_valid_json_needs_a_normal_complete_receipt(finish,status,code):
    session=_session(json.dumps(_payload()))
    async def stream(**kwargs):
        yield SimpleNamespace(content=json.dumps(_payload()),finish_reason=finish)
    session.stream_chat=stream;session.last_result.status=status
    gateway=Mock(open_chat=Mock(return_value=session))
    with patch('services.model_gateway.get_model_gateway',return_value=gateway):
        with pytest.raises(AppException) as failure:await RequirementAssistService().generate(_input())
    assert failure.value.code==code
    gateway.open_chat.assert_called_once()
    session.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('wrong',['rewritten','extra_path','entire_draft','bad_again'])
async def test_format_repair_cannot_rewrite_or_escape_the_broken_field(wrong):
    broken=_payload();broken['selling_points'][0]['benefit_basis']='推断'
    if wrong=='rewritten':
        text=json.dumps(_payload(),ensure_ascii=False)[:-1]+',}'
        fixed=_payload();fixed['selling_points'][0]['benefit']='完全不同的卖点'
    else:
        text=json.dumps(broken)
        fixed=({'patches':[{'path':['product_description'],'op':'set','value':'新的商品'}]} if wrong=='extra_path' else
            _payload() if wrong=='entire_draft' else {'patches':[{'path':['selling_points',0,'benefit_basis'],'op':'set','value':'还是错误'}]})
    sessions=[_session(text),_session(json.dumps(fixed))]
    gateway=Mock(open_chat=Mock(side_effect=sessions))
    with patch('services.model_gateway.get_model_gateway',return_value=gateway):
        with pytest.raises(AppException) as failure:await RequirementAssistService().generate(_input())
    assert failure.value.code=='REQUIREMENT_ASSIST_INVALID_OUTPUT'
    assert gateway.open_chat.call_count==2


@pytest.mark.asyncio
async def test_transient_retry_and_format_repair_share_two_calls(monkeypatch):
    from services.adapters.dashscope.chat_adapter import DashScopeAPIError
    rejection=DashScopeAPIError.from_http(b'{"error":{"code":"InvalidURL.Timeout"}}',400)
    broken=json.dumps(_payload())[:-1]+',}'
    sessions=[_session(error=rejection),_session(broken)]
    gateway=Mock(open_chat=Mock(side_effect=sessions))
    monkeypatch.setattr('services.agent.image.requirement_assist_service.asyncio.sleep',AsyncMock())
    with patch('services.model_gateway.get_model_gateway',return_value=gateway):
        with pytest.raises(AppException) as failure:await RequirementAssistService().generate(_input())
    assert failure.value.code=='REQUIREMENT_ASSIST_INVALID_OUTPUT'
    assert gateway.open_chat.call_count==2


@pytest.mark.asyncio
async def test_format_repair_cannot_reset_the_120_second_budget(monkeypatch):
    from services.agent.image import requirement_assist_service as module
    real_monotonic=module.time.monotonic
    offset=[0]
    session=_session(json.dumps(_payload())[:-1]+',}')
    stream=session.stream_chat
    async def slow(**kwargs):
        async for chunk in stream(**kwargs):yield chunk
        offset[0]=121
    session.stream_chat=slow
    monkeypatch.setattr(module.time,'monotonic',lambda:real_monotonic()+offset[0])
    gateway=Mock(open_chat=Mock(return_value=session))
    with patch('services.model_gateway.get_model_gateway',return_value=gateway):
        with pytest.raises(AppException) as failure:await RequirementAssistService().generate(_input())
    assert failure.value.code=='REQUIREMENT_ASSIST_TIMEOUT'
    gateway.open_chat.assert_called_once()


def test_ambiguous_duplicate_fields_and_non_json_numbers_are_not_accepted():
    for content in ('{"product_description":"a","product_description":"b"}',
            '{"product_description":"a","selling_points":NaN}'):
        with pytest.raises(InvalidRequirementOutput):parse_requirement_result(content)


@pytest.mark.asyncio
async def test_format_diagnostics_do_not_log_body_images_or_unknown_field_names():
    from loguru import logger
    secret_marker='PRIVATE_DRAFT_MARKER'
    payload=_payload();payload['product_description']+=secret_marker
    extra='data:image/png;base64,PRIVATE_IMAGE_MARKER';payload[extra]='extra'
    sessions=[_session(json.dumps(payload)),_session(json.dumps({'patches':[{'path':[extra],'op':'remove'}]}))]
    captured=[];sink=logger.add(lambda message:captured.append(str(message)))
    try:
        with patch('services.model_gateway.get_model_gateway',return_value=Mock(open_chat=Mock(side_effect=sessions))):
            outcome=await RequirementAssistService().generate(_input())
        assert secret_marker in outcome.result.product_description
        log=''.join(captured)
        assert 'output_sha256' in log and '<extra>' in log
        assert secret_marker not in log and 'PRIVATE_IMAGE_MARKER' not in log and 'cdn/product' not in log
    finally:logger.remove(sink)
