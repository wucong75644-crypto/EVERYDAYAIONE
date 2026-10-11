"""复用可信项目入口与限流；将用户编辑稿传到模型服务。"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from api.routes.ecom_requirement import generate_requirement_suggestions
from schemas.ecom_requirement import RequirementAssistResult, RequirementRevision, RequirementSettings, RequirementSource, RequirementSuggestionsRequest
from services.agent.image.requirement_assist_service import RequirementAssistOutcome

@pytest.mark.asyncio
async def test_route_adapts_trusted_images_and_revision_into_one_draft():
    result=RequirementAssistResult(product_description="普通印刷存钱本")
    revision=RequirementRevision(draft=result,supplement="米白背景",skipped_questions=["材质？"])
    body=RequirementSuggestionsRequest(
        source=RequirementSource(type="detail_project",project_id="project-1"),
        settings=RequirementSettings(content_type="default",image_count=14,requirement="原文"),revision=revision)
    adapted=SimpleNamespace(project_version=3,product_images=[1],reference_images=[2])
    adapter=MagicMock()
    adapter.adapt.return_value=adapted
    service=MagicMock()
    service.generate=AsyncMock(return_value=RequirementAssistOutcome(result,"kimi-k3",False,34000))
    limiter=MagicMock(check=AsyncMock())
    with (
        patch("api.routes.ecom_requirement.DetailProjectRequirementAdapter",return_value=adapter),
        patch("api.routes.ecom_requirement.RequirementAssistService",return_value=service),
        patch("api.routes.ecom_requirement.RequirementAssistRateLimiter",return_value=limiter),
        patch("api.routes.ecom_requirement.DetailProjectService"),
    ):
        response=await generate_requirement_suggestions(body,SimpleNamespace(user_id="u",org_id="org"),MagicMock())
    adapter.adapt.assert_called_once_with("project-1",body.settings,revision)
    service.generate.assert_awaited_once_with(adapted)
    limiter.check.assert_awaited_once_with("u")
    assert response.data.product_description=="普通印刷存钱本"
    assert response.meta.model=="kimi-k3" and response.meta.project_version==3
