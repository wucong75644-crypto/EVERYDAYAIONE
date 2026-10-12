"""Organization-scoped research and text-only editorial API."""

import asyncio
from uuid import UUID
from fastapi import APIRouter, HTTPException, Response, Query
from psycopg.types.json import Jsonb
from api.deps import Database, OrgCtx
from core.config import get_settings
from core.db_scope import DatabaseScope, DatabaseAccessKind
from services.agent.agent_reach.contracts import ReachError
from services.agent.agent_reach.access_budget import AccessBudgetService
from services.skills.contracts import SkillError
from services.skills.scheduled import scheduled_skill_options
from services.social_ops.contracts import (
    ProfileData,
    ProfileUpdate,
    ResearchCreate,
    BudgetConfig,
    Produce,
    DraftData,
    TopicUpdate,
)
from services.social_ops.repository import Repository, digest
from services.social_ops.production import create_run
from services.social_ops.validation import check_draft

router = APIRouter(prefix="/social-ops", tags=["社媒运营"])


def repository(ctx, db, response):
    response.headers["Cache-Control"] = "no-store"
    if not get_settings().social_ops_enabled:
        raise HTTPException(503, "社媒运营尚未启用")
    if not ctx.org_id:
        raise HTTPException(403, "需要组织上下文")
    return Repository(
        db.pool,
        DatabaseScope(
            actor_user_id=ctx.user_id,
            org_id=ctx.org_id,
            access_kind=DatabaseAccessKind.RUNTIME,
        ),
    )


def call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except ReachError as e:
        raise HTTPException(
            {"NOT_FOUND": 404, "VERSION_CONFLICT": 409, "ACCESS_DENIED": 403}.get(
                e.code, 422
            ),
            {"code": e.code, "message": str(e)},
        ) from None


@router.get("/profiles")
def profiles(ctx: OrgCtx, db: Database, response: Response):
    return call(repository(ctx, db, response).profiles)


@router.post("/profiles", status_code=201)
def create_profile(data: ProfileData, ctx: OrgCtx, db: Database, response: Response):
    return call(repository(ctx, db, response).save_profile, data)


@router.patch("/profiles/{id}")
def update_profile(
    id: UUID, data: ProfileUpdate, ctx: OrgCtx, db: Database, response: Response
):
    return call(repository(ctx, db, response).save_profile, data, str(id))


@router.get("/profiles/{id}/revisions")
def profile_history(id: UUID, ctx: OrgCtx, db: Database, response: Response):
    repo = repository(ctx, db, response)
    with repo.cursor() as c:
        c.execute(
            "SELECT * FROM social_profile_revisions WHERE profile_id=%s AND org_id=%s ORDER BY revision DESC",
            (id, ctx.org_id),
        )
        return c.fetchall()


@router.post("/research-jobs", status_code=201)
def create_job(data: ResearchCreate, ctx: OrgCtx, db: Database, response: Response):
    return call(repository(ctx, db, response).create_job, data)


@router.get("/research-jobs")
def jobs(ctx: OrgCtx, db: Database, response: Response):
    return call(repository(ctx, db, response).jobs)


@router.get("/research-jobs/{id}")
def job(id: UUID, ctx: OrgCtx, db: Database, response: Response):
    return call(repository(ctx, db, response).job, str(id))


@router.get("/research-jobs/{id}/sources")
def sources(
    id: UUID,
    ctx: OrgCtx,
    db: Database,
    response: Response,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    return call(
        repository(ctx, db, response).sources, str(id), limit=limit, offset=offset
    )


@router.post("/research-jobs/{id}/{action}")
def transition(id: UUID, action: str, ctx: OrgCtx, db: Database, response: Response):
    return call(repository(ctx, db, response).transition, str(id), action)


@router.get("/connections/{id}/budget")
def budget(id: UUID, ctx: OrgCtx, db: Database, response: Response):
    repo = repository(ctx, db, response)
    connection = call(repo.load, str(id), "xiaohongshu")
    return call(AccessBudgetService(db.pool, repo.scope).status, connection)


@router.put("/connections/{id}/budget")
def configure_budget(
    id: UUID, data: BudgetConfig, ctx: OrgCtx, db: Database, response: Response
):
    repo = repository(ctx, db, response)
    connection = call(repo.load, str(id), "xiaohongshu")
    return call(AccessBudgetService(db.pool, repo.scope).configure, connection, data)


@router.post("/connections/{id}/recover")
def recover_budget(id: UUID, ctx: OrgCtx, db: Database, response: Response):
    repo = repository(ctx, db, response)
    connection = call(repo.load, str(id), "xiaohongshu")
    call(AccessBudgetService(db.pool, repo.scope).recover, connection)
    return {"status": "recovered"}


@router.get("/skill-options")
async def skills(ctx: OrgCtx, db: Database, response: Response):
    repository(ctx, db, response)
    try:
        return await scheduled_skill_options(
            db, owner=ctx.user_id, org=ctx.org_id, task_id=None
        )
    except SkillError as e:
        raise HTTPException(422, str(e)) from None


@router.post("/skill-drafts", status_code=201)
def import_skills(ctx: OrgCtx, db: Database, response: Response):
    repo = repository(ctx, db, response)
    call(repo.assert_admin)
    from services.social_ops.skill_bundle import install_drafts

    try:
        return install_drafts(db, repo.scope, get_settings())
    except SkillError as e:
        raise HTTPException(422, str(e)) from None


@router.post("/production-runs", status_code=202)
async def produce(data: Produce, ctx: OrgCtx, db: Database, response: Response):
    repo = repository(ctx, db, response)
    try:
        return await create_run(db, repo, data)
    except ReachError as e:
        raise HTTPException(
            409 if e.code == "VERSION_CONFLICT" else 422,
            {"code": e.code, "message": str(e)},
        ) from None
    except SkillError as e:
        raise HTTPException(422, str(e)) from None


@router.get("/research-jobs/{id}/production-runs")
def runs(id: UUID, ctx: OrgCtx, db: Database, response: Response):
    repo = repository(ctx, db, response)
    call(repo.job, str(id))
    with repo.cursor() as c:
        c.execute(
            """SELECT id,kind,status,stage_index,outputs,result,error,created_at FROM social_production_runs
            WHERE job_id=%s AND org_id=%s ORDER BY created_at DESC LIMIT 100""",
            (id, ctx.org_id),
        )
        return c.fetchall()


@router.post("/production-runs/{id}/resume")
def resume_run(id: UUID, ctx: OrgCtx, db: Database, response: Response):
    repo = repository(ctx, db, response)
    with repo.cursor() as c:
        c.execute(
            "UPDATE social_production_runs SET status='queued',error='' WHERE id=%s AND org_id=%s AND status='needs_input' RETURNING id,status",
            (id, ctx.org_id),
        )
        row = c.fetchone()
        if not row:
            raise HTTPException(409, "任务不存在或不可恢复")
        return row


@router.get("/research-jobs/{id}/contents")
def contents(id: UUID, ctx: OrgCtx, db: Database, response: Response):
    return call(repository(ctx, db, response).contents, str(id))


@router.get("/contents/{id}/revisions")
def content_history(id: UUID, ctx: OrgCtx, db: Database, response: Response):
    repo = repository(ctx, db, response)
    with repo.cursor() as c:
        c.execute(
            "SELECT * FROM social_content_revisions WHERE content_id=%s AND org_id=%s ORDER BY revision DESC",
            (id, ctx.org_id),
        )
        return c.fetchall()


@router.post("/contents/{id}/revisions")
def save_draft(
    id: UUID, data: DraftData, ctx: OrgCtx, db: Database, response: Response
):
    repo = repository(ctx, db, response)
    with repo.cursor() as c:
        c.execute(
            "SELECT job_id FROM social_contents WHERE id=%s AND org_id=%s",
            (id, ctx.org_id),
        )
        row = c.fetchone()
        if not row:
            raise HTTPException(404, "草稿不存在")
    sources = call(
        repo.sources, str(row["job_id"]), evidence_ids=data.evidence_ids, limit=100
    )
    payload = data.model_dump(mode="json", exclude={"expected_revision"})
    checks = check_draft(payload, sources)
    if "来源不存在或无权访问" in checks["hard_errors"]:
        raise HTTPException(422, "来源不存在或无权访问")

    def save():
        with repo.cursor() as c:
            return repo.save_content(
                c,
                row["job_id"],
                payload,
                {"edited_by": ctx.user_id, "input_hash": digest(payload)},
                checks,
                str(id),
                data.expected_revision,
            )

    return call(save)


@router.get("/topics")
def topics(job_id: UUID, ctx: OrgCtx, db: Database, response: Response):
    repo = repository(ctx, db, response)
    call(repo.job, str(job_id))
    with repo.cursor() as c:
        c.execute(
            "SELECT id,revision,data,run_id FROM social_topic_candidates WHERE job_id=%s AND org_id=%s ORDER BY created_at DESC,id DESC LIMIT 200",
            (job_id, ctx.org_id),
        )
        return c.fetchall()


@router.patch("/topics/{id}")
def update_topic(
    id: UUID, data: TopicUpdate, ctx: OrgCtx, db: Database, response: Response
):
    repo = repository(ctx, db, response)
    with repo.cursor() as c:
        c.execute(
            """UPDATE social_topic_candidates SET data=data||%s,revision=revision+1
            WHERE id=%s AND org_id=%s AND revision=%s RETURNING id,revision,data""",
            (
                Jsonb(data.model_dump(mode="json", exclude={"expected_revision"})),
                id,
                ctx.org_id,
                data.expected_revision,
            ),
        )
        row = c.fetchone()
        if not row:
            raise HTTPException(409, "选题版本冲突或不存在")
        return row


@router.post("/research-items/{id}/retry")
def retry_item(id: UUID, ctx: OrgCtx, db: Database, response: Response):
    return call(repository(ctx, db, response).retry_item, str(id))


@router.get("/source-snapshots/{id}")
def source_snapshot(id: UUID, ctx: OrgCtx, db: Database, response: Response):
    return call(repository(ctx, db, response).snapshot, str(id))
