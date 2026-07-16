"""电商图 AI 帮写路由契约测试。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from api.routes.ecom_requirement import generate_requirement_suggestions
from schemas.ecom_requirement import (
    ProductFacts, RequirementAssistResult, RequirementSettings,
    RequirementSource, RequirementSuggestion, RequirementSuggestionsRequest,
)
from services.agent.image.requirement_assist_service import RequirementAssistOutcome


def _request() -> RequirementSuggestionsRequest:
    return RequirementSuggestionsRequest(
        source=RequirementSource(type="detail_project", project_id="project-1"),
        settings=RequirementSettings(platform="taobao", requirement="清新自然"),
    )


def _result() -> RequirementAssistResult:
    return RequirementAssistResult(
        product_facts=ProductFacts(product_name="笔记本"),
        suggestions=[
            RequirementSuggestion(
                id=suggestion_id, name=suggestion_id,
                style_name="清新风", brief_markdown="通用创作简报",
            )
            for suggestion_id in ("selling_point", "scene", "creative")
        ],
    )


@pytest.mark.asyncio
async def test_route_adapts_project_and_returns_unified_envelope() -> None:
    ctx = SimpleNamespace(user_id="user-1", org_id="org-1")
    db = MagicMock()
    adapted = SimpleNamespace(
        project_version=3, product_images=[1], reference_images=[2],
    )
    adapter = MagicMock()
    adapter.adapt.return_value = adapted
    service = MagicMock()
    service.generate = AsyncMock(return_value=RequirementAssistOutcome(
        result=_result(), model="qwen-vl-max", fallback_used=False, latency_ms=32000,
    ))
    rate_limiter = MagicMock()
    rate_limiter.check = AsyncMock()

    with (
        patch("api.routes.ecom_requirement.DetailProjectRequirementAdapter", return_value=adapter),
        patch("api.routes.ecom_requirement.RequirementAssistService", return_value=service),
        patch("api.routes.ecom_requirement.RequirementAssistRateLimiter", return_value=rate_limiter),
        patch("api.routes.ecom_requirement.DetailProjectService"),
    ):
        response = await generate_requirement_suggestions(_request(), ctx, db)

    assert response.success is True
    assert response.data.product_facts.product_name == "笔记本"
    assert response.meta.project_version == 3
    assert response.meta.model == "qwen-vl-max"
    adapter.adapt.assert_called_once()
    service.generate.assert_awaited_once_with(adapted)
    rate_limiter.check.assert_awaited_once_with("user-1")
