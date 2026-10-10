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
    return SimpleNamespace(stream_chat=stream_chat,close=AsyncMock(),captured=captured)

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
