"""Kimi单份草稿：真实网关边界、失败关闭与合理推断保留。"""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
import pytest
from core.exceptions import AppException
from schemas.ecom_requirement import RequirementAssistInput, RequirementImage
from services.agent.image.requirement_assist_service import (
    InvalidRequirementOutput, RequirementAssistService, parse_requirement_result, validate_no_output_urls,
)

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
    assert request.timeout==120
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
