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


from services.social_ops.contracts import (
    CardEdit,
    MediaEdit,
    MediaImport,
    PackageCheck,
    PublishCreate,
    PublishConfirm,
    PublishVerify,
    MediaLocate,
)
from services.social_ops import cards, publishing
from fastapi.responses import FileResponse


@router.post("/contents/{id}/cards")
def render_cards(
    id: UUID, data: CardEdit, ctx: OrgCtx, db: Database, response: Response
):
    repo = repository(ctx, db, response)

    def save():
        with repo.cursor() as c:
            c.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (str(ctx.org_id) + str(data.client_request_id),),
            )
            c.execute(
                "SELECT * FROM social_content_revisions WHERE content_id=%s AND org_id=%s AND manifest->>'client_request_id'=%s",
                (id, ctx.org_id, str(data.client_request_id)),
            )
            old = c.fetchone()
            if old:
                if old["manifest"].get("request_hash") != digest(
                    data.model_dump(mode="json")
                ):
                    raise ReachError("VERSION_CONFLICT", "请求编号已绑定其他输入")
                return old
            return cards.render_revision(
                repo,
                c,
                str(id),
                data.expected_revision,
                [v.model_dump() for v in data.cards],
                data.visual.model_dump(),
                {
                    "client_request_id": str(data.client_request_id),
                    "request_hash": digest(data.model_dump(mode="json")),
                    "edited_by": ctx.user_id,
                },
            )

    return call(save)


@router.get("/assets/{id}/preview")
def preview_asset(id: UUID, ctx: OrgCtx, db: Database, response: Response):
    item, path = call(cards.asset, repository(ctx, db, response), str(id))
    return FileResponse(
        path,
        media_type="image/png",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.post("/contents/{id}/media")
def reorder_media(
    id: UUID, data: MediaEdit, ctx: OrgCtx, db: Database, response: Response
):
    repo = repository(ctx, db, response)

    def save():
        row = cards.content(repo, str(id), data.expected_revision)
        existing = {a["id"]: a for a in row["data"].get("assets", [])}
        ids = [str(v) for v in data.asset_ids]
        if len(set(ids)) != len(ids) or set(ids) != set(existing):
            raise ReachError("INVALID_ARGUMENTS", "排序必须保留当前整组素材且不重复")
        assets = [existing[v] for v in ids]
        for a in assets:
            cards.asset(repo, a["id"])
        payload = {
            **row["data"],
            "assets": assets,
            "cards": [a.get("metadata", {}).get("card", {}) for a in assets],
        }
        with repo.cursor() as c:
            return repo.save_content(
                c,
                row["job_id"],
                payload,
                {"edited_by": ctx.user_id, "operation": "reorder"},
                {"status": "needs_check", "ready_to_publish": False},
                str(id),
                data.expected_revision,
            )

    return call(save)


@router.post("/contents/{id}/media/import")
def replace_media(
    id: UUID, data: MediaImport, ctx: OrgCtx, db: Database, response: Response
):
    repo = repository(ctx, db, response)

    def save():
        from types import SimpleNamespace
        from services.file_resources import FileTargetResolver
        from PIL import Image
        import io
        import hashlib

        row = cards.content(repo, str(id), data.expected_revision)
        assets = list(row["data"].get("assets", []))
        if data.position > len(assets):
            raise ReachError("INVALID_ARGUMENTS", "素材位置不存在")
        owner = SimpleNamespace(
            user_id=ctx.user_id,
            workspace_user_id=ctx.user_id,
            org_id=ctx.org_id,
            execution_mode="interactive",
            context_scope="user",
        )
        from services.file_resources import FileTargetError
        from services.tools.resource_access import ResourceAccessError

        try:
            target = FileTargetResolver(owner).resolve(data.resource_ref)
            before = target.validate()
        except (FileTargetError, ResourceAccessError, PermissionError):
            raise ReachError(
                "ACCESS_DENIED", "素材引用已失效或不在授权工作区"
            ) from None
        if before[1] > 20 * 1024 * 1024:
            raise ReachError("MEDIA_LIMIT", "替换图片需小于20MB")
        raw = cards.read_image_bytes(target.path)
        if before != target.validate():
            raise ReachError("RESOURCE_CHANGED", "图片读取期间变化")
        try:
            with Image.open(io.BytesIO(raw)) as image:
                if image.size != (1080, 1440):
                    raise ReachError("INVALID_MEDIA", "替换图片需1080×1440")
                output = io.BytesIO()
                image.convert("RGB").save(output, "PNG")
        except (OSError, ValueError, Image.DecompressionBombError):
            raise ReachError("INVALID_MEDIA", "素材不是可解析的图片") from None
        with repo.cursor() as c:
            selected = cards.register(
                repo,
                c,
                str(id),
                data.expected_revision + 1,
                output.getvalue(),
                data.source,
                data.rights,
                {
                    "replacement": True,
                    "original_resource": {
                        "owner_id": ctx.user_id,
                        "workspace_path": str(
                            target.path.relative_to(FileTargetResolver(owner).root)
                        ),
                        "sha256": hashlib.sha256(raw).hexdigest(),
                    },
                },
            )
            if data.position == len(assets):
                assets.append(selected)
            else:
                assets[data.position] = selected
            if len(assets) > 9:
                raise ReachError("MEDIA_LIMIT", "最多9张图片")
            payload = {
                **row["data"],
                "assets": assets,
                "images_text_hash": digest(
                    {k: row["data"].get(k) for k in ("title", "body", "tags")}
                ),
            }
            return repo.save_content(
                c,
                row["job_id"],
                payload,
                {"edited_by": ctx.user_id, "operation": "replace"},
                {"status": "needs_check", "ready_to_publish": False},
                str(id),
                data.expected_revision,
            )

    return call(save)


@router.post("/contents/{id}/package-checks")
def check_package(
    id: UUID, data: PackageCheck, ctx: OrgCtx, db: Database, response: Response
):
    return call(publishing.check_package, repository(ctx, db, response), str(id), data)


@router.get("/contents/{id}/publish-intents")
def publish_intents(id: UUID, ctx: OrgCtx, db: Database, response: Response):
    repo = repository(ctx, db, response)
    call(cards.content, repo, str(id))
    with repo.cursor() as c:
        c.execute(
            "SELECT id FROM social_publish_intents WHERE content_id=%s AND org_id=%s ORDER BY created_at DESC LIMIT 100",
            (id, ctx.org_id),
        )
        ids = [row["id"] for row in c.fetchall()]
    return [call(publishing.get_intent, repo, id) for id in ids]


@router.post("/publish-intents", status_code=201)
def create_publish_intent(
    data: PublishCreate, ctx: OrgCtx, db: Database, response: Response
):
    return call(publishing.create_intent, repository(ctx, db, response), data)


@router.post("/publish-intents/{id}/confirm")
async def confirm_publish_intent(
    id: UUID, data: PublishConfirm, ctx: OrgCtx, db: Database, response: Response
):
    repo = repository(ctx, db, response)
    try:
        return await publishing.confirm_intent(db, repo, str(id), data.approval_hash)
    except ReachError as e:
        raise HTTPException(
            409 if e.code == "VERSION_CONFLICT" else 422,
            {"code": e.code, "message": str(e)},
        ) from None


@router.post("/publish-intents/{id}/verify")
def verify_publish_intent(
    id: UUID, data: PublishVerify, ctx: OrgCtx, db: Database, response: Response
):
    return call(publishing.verify_intent, repository(ctx, db, response), str(id), data)


@router.post("/publish-intents/{id}/cancel")
def cancel_publish_intent(id: UUID, ctx: OrgCtx, db: Database, response: Response):
    repo = repository(ctx, db, response)
    with repo.cursor() as c:
        c.execute(
            "UPDATE social_publish_intents SET state='cancelled',updated_at=now() WHERE id=%s AND org_id=%s AND state='awaiting_confirmation' RETURNING id,state",
            (id, ctx.org_id),
        )
        row = c.fetchone()
        if not row:
            raise HTTPException(409, "意图已派发或不可取消，不能保证撤回")
        return row


@router.post("/media-reference")
def media_reference(data: MediaLocate, ctx: OrgCtx, db: Database, response: Response):
    repository(ctx, db, response)
    from types import SimpleNamespace
    from services.file_resources import FileTargetResolver

    resolver = FileTargetResolver(
        SimpleNamespace(
            user_id=ctx.user_id,
            org_id=ctx.org_id,
            execution_mode="interactive",
            context_scope="user",
        )
    )
    try:
        target = resolver.resolve(data.path)
        return {"resource_ref": resolver.reference(target.path)}
    except (ValueError, PermissionError):
        raise HTTPException(422, "图片不在当前授权工作区或已变化") from None
