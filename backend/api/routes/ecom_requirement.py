"""电商图 AI 帮写三套通用创作简报接口。"""

from fastapi import APIRouter
from loguru import logger

from api.deps import OrgCtx, ScopedDB
from schemas.ecom_requirement import (
    RequirementAssistMeta, RequirementSuggestionsEnvelope, RequirementSuggestionsRequest,
)
from services.agent.image.input_adapters import DetailProjectRequirementAdapter
from services.agent.image.requirement_assist_rate_limiter import RequirementAssistRateLimiter
from services.agent.image.requirement_assist_service import RequirementAssistService
from services.detail_project_service import DetailProjectService


router = APIRouter(prefix="/ecom-image", tags=["ecom-image"])


@router.post(
    "/requirement-suggestions",
    response_model=RequirementSuggestionsEnvelope,
    summary="生成三套电商图通用创作简报",
)
async def generate_requirement_suggestions(
    body: RequirementSuggestionsRequest,
    ctx: OrgCtx,
    db: ScopedDB,
) -> RequirementSuggestionsEnvelope:
    """读取可信详情项目输入，调用共享核心服务并返回三套方案。"""
    await RequirementAssistRateLimiter().check(ctx.user_id)
    detail_service = DetailProjectService(db, ctx.user_id, ctx.org_id)
    adapter = DetailProjectRequirementAdapter(detail_service, ctx.user_id, ctx.org_id)
    assist_input = adapter.adapt(body.source.project_id, body.settings)
    outcome = await RequirementAssistService().generate(assist_input)
    logger.info(
        f"Requirement assist succeeded | user_id={ctx.user_id} | org_id={ctx.org_id} | "
        f"source_id={body.source.project_id} | model={outcome.model} | "
        f"fallback={outcome.fallback_used} | latency_ms={outcome.latency_ms} | "
        f"product_images={len(assist_input.product_images)} | "
        f"reference_images={len(assist_input.reference_images)}"
    )
    return RequirementSuggestionsEnvelope(
        data=outcome.result,
        meta=RequirementAssistMeta(
            model=outcome.model,
            fallback_used=outcome.fallback_used,
            latency_ms=outcome.latency_ms,
            project_version=assist_input.project_version,
        ),
    )
