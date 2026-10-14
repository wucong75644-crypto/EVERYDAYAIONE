"""Phase-five API; all reads/writes retain trusted organization context."""

import json
from uuid import UUID
from datetime import datetime
from fastapi import APIRouter, Response, HTTPException, UploadFile, File, Form, Query
from api.deps import OrgCtx, Database
from core.config import get_settings
from services.social_ops.contracts import (
    MetricCollect,
    ReviewCreate,
    SuggestionDecision,
    AnalyticsSchedule,
    Input,
)
from services.social_ops import metrics, metric_imports, reviews
from services.social_ops.analytics import CAPABILITIES, statistics
from api.routes.social_ops import repository, call
from services.agent.agent_reach.contracts import ReachError
from services.skills.contracts import SkillError
from pydantic import Field

router = APIRouter(prefix="/social-ops/analytics", tags=["社媒复盘"])


def repo_for(ctx, db, response):
    repo = repository(ctx, db, response)
    if not get_settings().social_ops_analytics_enabled:
        raise HTTPException(503, "数据回收与复盘尚未启用")
    return repo


@router.get("/capabilities")
def capabilities(ctx: OrgCtx, db: Database, response: Response):
    repo_for(ctx, db, response)
    return CAPABILITIES


@router.get("/contents")
def contents(
    ctx: OrgCtx,
    db: Database,
    response: Response,
    connection_id: UUID | None = None,
    profile_id: UUID | None = None,
    content_type: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
):
    if any(t and not t.tzinfo for t in (start, end)):
        raise HTTPException(422, "时间必须带时区")
    return call(
        metrics.published_contents,
        repo_for(ctx, db, response),
        connection_id,
        profile_id,
        content_type,
        start,
        end,
    )


@router.post("/contents/{id}/collect", status_code=202)
def collect(
    id: UUID, data: MetricCollect, ctx: OrgCtx, db: Database, response: Response
):
    return call(metrics.create_collection, repo_for(ctx, db, response), str(id), data)


@router.get("/contents/{id}/snapshots")
def history(id: UUID, ctx: OrgCtx, db: Database, response: Response):
    return call(metrics.snapshots, repo_for(ctx, db, response), [str(id)])


@router.get("/statistics")
def compare(
    ctx: OrgCtx,
    db: Database,
    response: Response,
    intent_ids: list[UUID] = Query(min_length=1, max_length=30),
    start: datetime | None = None,
    end: datetime | None = None,
):
    if any(t and not t.tzinfo for t in (start, end)):
        raise HTTPException(422, "时间必须带时区")
    return statistics(
        call(metrics.snapshots, repo_for(ctx, db, response), intent_ids, start, end)
    )


@router.get("/imports/template")
def template(ctx: OrgCtx, db: Database, response: Response):
    repo_for(ctx, db, response)
    return Response(
        ",".join(metric_imports.FIELDS) + "\n",
        media_type="text/csv",
        headers={
            "Content-Disposition": 'attachment; filename="social-metrics.csv"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/imports/preview")
async def import_preview(
    ctx: OrgCtx,
    db: Database,
    response: Response,
    file: UploadFile = File(),
    source: str = Form(min_length=1, max_length=1000),
    mapping: str = Form(default="{}", max_length=4000),
):
    repo = repo_for(ctx, db, response)
    try:
        fields = json.loads(mapping)
    except ValueError:
        raise HTTPException(422, "字段映射需为JSON对象") from None
    if not isinstance(fields, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in fields.items()
    ):
        raise HTTPException(422, "字段映射需为字符串对象")
    raw = await file.read(2 * 1024 * 1024 + 1)
    import asyncio

    return await asyncio.to_thread(
        call, metric_imports.preview, repo, raw, file.filename or "", source, fields
    )


class ConfirmImport(Input):
    preview_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


@router.post("/imports/{id}/confirm")
def import_confirm(
    id: UUID, data: ConfirmImport, ctx: OrgCtx, db: Database, response: Response
):
    return call(
        metric_imports.confirm, repo_for(ctx, db, response), str(id), data.preview_hash
    )


@router.post("/reviews", status_code=202)
async def review(data: ReviewCreate, ctx: OrgCtx, db: Database, response: Response):
    repo = repo_for(ctx, db, response)
    try:
        return await reviews.create_review(db, repo, data)
    except ReachError as e:
        raise HTTPException(
            409 if e.code == "VERSION_CONFLICT" else 422,
            {"code": e.code, "message": str(e)},
        ) from None
    except SkillError as e:
        raise HTTPException(422, str(e)) from None


@router.get("/reviews")
def list_reviews(ctx: OrgCtx, db: Database, response: Response):
    repo = repo_for(ctx, db, response)
    with repo.cursor() as c:
        c.execute(
            "SELECT id,kind,status,error,stage_index,result,created_at,input->'request' AS request FROM social_production_runs WHERE org_id=%s AND kind IN ('postmortem','comment_insights','strategy') ORDER BY created_at DESC LIMIT 100",
            (ctx.org_id,),
        )
        return c.fetchall()


@router.get("/reviews/{id}")
def review_detail(id: UUID, ctx: OrgCtx, db: Database, response: Response):
    repo = repo_for(ctx, db, response)
    with repo.cursor() as c:
        c.execute(
            "SELECT * FROM social_production_runs WHERE id=%s AND org_id=%s AND kind IN ('postmortem','comment_insights','strategy')",
            (id, ctx.org_id),
        )
        row = c.fetchone()
        if not row:
            raise HTTPException(404, "复盘不存在")
        c.execute(
            "SELECT * FROM social_review_suggestions WHERE run_id=%s AND org_id=%s ORDER BY id",
            (id, ctx.org_id),
        )
        return {**row, "suggestions": c.fetchall()}


@router.post("/suggestions/{id}/decision")
def decision(
    id: UUID, data: SuggestionDecision, ctx: OrgCtx, db: Database, response: Response
):
    return call(reviews.decide, repo_for(ctx, db, response), str(id), data)


@router.put("/schedules")
async def schedule(
    data: AnalyticsSchedule, ctx: OrgCtx, db: Database, response: Response
):
    from services.social_ops.analytics_schedule import save

    try:
        return await save(db, repo_for(ctx, db, response), data)
    except ReachError as e:
        raise HTTPException(422, {"code": e.code, "message": str(e)}) from None
    except SkillError as e:
        raise HTTPException(422, str(e)) from None


@router.get("/schedules")
def schedules(ctx: OrgCtx, db: Database, response: Response):
    repo = repo_for(ctx, db, response)
    with repo.cursor() as c:
        c.execute(
            "SELECT id,intent_id,enabled,next_at,next_review_at,error,config-'collection' AS config FROM social_analytics_schedules WHERE org_id=%s ORDER BY next_at",
            (ctx.org_id,),
        )
        return c.fetchall()


@router.post("/schedules/{id}/pause")
def pause_schedule(id: UUID, ctx: OrgCtx, db: Database, response: Response):
    repo = repo_for(ctx, db, response)
    with repo.cursor() as c:
        c.execute(
            "UPDATE social_analytics_schedules SET enabled=false WHERE id=%s AND org_id=%s RETURNING id,enabled",
            (id, ctx.org_id),
        )
        row = c.fetchone()
        if not row:
            raise HTTPException(404, "周期配置不存在")
        return row
