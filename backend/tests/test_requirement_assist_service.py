"""AI 帮写共享核心服务测试。"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from core.exceptions import AppException
from schemas.ecom_requirement import RequirementAssistInput, RequirementImage
from services.agent.image.requirement_assist_service import (
    _PRIMARY_TIMEOUT_SECONDS, _TOTAL_TIMEOUT_SECONDS,
    InvalidRequirementOutput, RequirementAssistService, apply_conflict_gate,
    parse_requirement_result, validate_no_output_urls, validate_reference_ids,
)


def test_timeout_budget_allows_normal_multimodal_latency() -> None:
    assert _PRIMARY_TIMEOUT_SECONDS == 60.0
    assert _TOTAL_TIMEOUT_SECONDS == 100.0
    assert _TOTAL_TIMEOUT_SECONDS - _PRIMARY_TIMEOUT_SECONDS == 40.0


def _input(with_reference: bool = True) -> RequirementAssistInput:
    references = [RequirementImage(id="r1", original_url="https://cdn/r1.png", display_name="r1.png")] if with_reference else []
    return RequirementAssistInput(
        user_id="user-1", org_id=None, source_type="detail_project", source_id="project-1",
        product_images=[RequirementImage(id="p1", original_url="https://cdn/p1.png", display_name="p1.png")],
        reference_images=references, content_type="main_image", platform="taobao",
        language="zh-CN", aspect_ratio="1:1", quality="1k", image_count=5,
        user_requirement="突出400页", project_version=2,
    )


def _payload(*, conflict: bool = False) -> dict:
    conflicts = []
    if conflict:
        conflicts = [{
            "field": "页数", "user_value": "400页", "confirmed_value": "200页",
            "message": "页数待用户确认，当前不可作为卖点", "blocked_claims": ["400页"],
        }]
    return {
        "product_facts": {
            "product_name": "笔记本", "confirmed_attributes": ["200页"], "unclear_items": [],
        },
        "reference_analyses": [{
            "image_id": "r1", "primary_uses": ["background"], "summary": "浅色背景",
            "excluded_elements": ["参考商品"],
        }],
        "conflicts": conflicts,
        "suggestions": [
            {"id": item, "name": item, "style_name": "清新风", "brief_markdown": "## 产品信息\n200页笔记本"}
            for item in ("selling_point", "scene", "creative")
        ],
    }


def test_parse_requirement_result_accepts_fenced_json() -> None:
    content = f"```json\n{json.dumps(_payload(), ensure_ascii=False)}\n```"
    assert parse_requirement_result(content).product_facts.product_name == "笔记本"


def test_parse_requirement_result_rejects_invalid_schema() -> None:
    with pytest.raises(InvalidRequirementOutput, match="三方案协议"):
        parse_requirement_result('{"suggestions": []}')


def test_validate_reference_ids_rejects_unknown_id() -> None:
    result = parse_requirement_result(json.dumps(_payload(), ensure_ascii=False))
    with pytest.raises(InvalidRequirementOutput, match="未知参考图"):
        validate_reference_ids(result, _input(with_reference=False))


def test_validate_no_output_urls_rejects_generated_link() -> None:
    payload = _payload()
    payload["suggestions"][0]["brief_markdown"] = "访问 https://malicious.example"
    result = parse_requirement_result(json.dumps(payload, ensure_ascii=False))
    with pytest.raises(InvalidRequirementOutput, match="未授权 URL"):
        validate_no_output_urls(result)


def test_conflict_gate_removes_claim_and_evasive_marketing_but_keeps_original() -> None:
    payload = _payload(conflict=True)
    for suggestion in payload["suggestions"]:
        suggestion["brief_markdown"] = (
            "## 产品信息\n突出400页大容量\n暗示容量加倍\n"
            "## 用户需求原文\n突出400页大容量"
        )
    result = apply_conflict_gate(parse_requirement_result(json.dumps(payload, ensure_ascii=False)))
    brief = result.suggestions[0].brief_markdown
    assert "暗示容量加倍" not in brief
    assert "待确认：页数待用户确认" in brief
    assert brief.endswith("突出400页大容量")


@pytest.mark.asyncio
async def test_generate_returns_primary_model_result_and_closes_adapter() -> None:
    adapter = AsyncMock()
    adapter.chat_sync.return_value = SimpleNamespace(content=json.dumps(_payload(), ensure_ascii=False))
    settings = SimpleNamespace(
        image_enhance_vl_model="primary", image_enhance_fallback_model="fallback",
        dashscope_api_key="key", dashscope_base_url="https://example.com",
    )
    with (
        patch("services.agent.image.requirement_assist_service.get_settings", return_value=settings),
        patch("services.agent.image.requirement_assist_service.DashScopeChatAdapter", return_value=adapter),
    ):
        outcome = await RequirementAssistService().generate(_input())
    assert outcome.model == "primary"
    assert outcome.fallback_used is False
    adapter.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_generate_falls_back_after_invalid_primary_output() -> None:
    primary = AsyncMock()
    fallback = AsyncMock()
    primary.chat_sync.return_value = SimpleNamespace(content="invalid")
    fallback.chat_sync.return_value = SimpleNamespace(content=json.dumps(_payload(), ensure_ascii=False))
    settings = SimpleNamespace(
        image_enhance_vl_model="primary", image_enhance_fallback_model="fallback",
        dashscope_api_key="key", dashscope_base_url="https://example.com",
    )
    with (
        patch("services.agent.image.requirement_assist_service.get_settings", return_value=settings),
        patch(
            "services.agent.image.requirement_assist_service.DashScopeChatAdapter",
            side_effect=[primary, fallback],
        ),
    ):
        outcome = await RequirementAssistService().generate(_input())
    assert outcome.model == "fallback"
    assert outcome.fallback_used is True
    primary.close.assert_awaited_once()
    fallback.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_generate_maps_double_timeout_to_app_exception() -> None:
    service = RequirementAssistService()
    settings = SimpleNamespace(image_enhance_vl_model="primary", image_enhance_fallback_model="fallback")
    with (
        patch("services.agent.image.requirement_assist_service.get_settings", return_value=settings),
        patch.object(service, "_run_model", AsyncMock(side_effect=asyncio.TimeoutError)),
    ):
        with pytest.raises(AppException) as exc:
            await service.generate(_input())
    assert exc.value.code == "REQUIREMENT_ASSIST_TIMEOUT"
