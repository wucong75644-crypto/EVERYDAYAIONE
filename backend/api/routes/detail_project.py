"""主图详情页草稿接口。"""

from fastapi import APIRouter, Depends, Query
from uuid import UUID
from loguru import logger

from api.deps import OrgCtx, ScopedDB
from core.exceptions import AppException
from schemas.detail_project import (
    DetailImageAttachRequest, DetailImageCategoryPatch, DetailImageOrderRequest,
    DetailProjectCreateRequest, DetailProjectEnvelope, DetailProjectSettingsPatch, DetailProjectVersionRequest, DetailRunRequest, DetailResumeRequest,
)
from services.detail_project_service import DetailProjectService


router = APIRouter(prefix="/detail-projects", tags=["主图详情页"])


def get_detail_project_service(ctx: OrgCtx, db: ScopedDB) -> DetailProjectService:
    return DetailProjectService(db, ctx.user_id, ctx.org_id)


@router.get("/current", response_model=DetailProjectEnvelope)
def get_current_detail_project(
    service: DetailProjectService = Depends(get_detail_project_service),
) -> DetailProjectEnvelope:
    row = service.db.table("detail_projects").select("id").eq("user_id", service.user_id).neq("status", "archived").order("updated_at", desc=True).limit(1).execute().data
    if not row:
        return DetailProjectEnvelope(data={"project": None})
    from services.detail_page_generation import DetailPageGeneration
    return DetailProjectEnvelope(data={"project": DetailPageGeneration(service.db,service.user_id,service.org_id).read(str(row[0]["id"]))})


@router.get("/capabilities", response_model=DetailProjectEnvelope)
def get_detail_capabilities(service: DetailProjectService = Depends(get_detail_project_service)):
    from core.config import get_settings
    from services.agent.image.ecommerce_planner.page_profile import capabilities
    from services.handlers.chat_image_request import image_capabilities
    settings = get_settings()
    return DetailProjectEnvelope(data={"enabled": settings.detail_page_generation_enabled,
        "prompt_models": capabilities(settings), "image_models": image_capabilities()})


@router.post("", response_model=DetailProjectEnvelope)
def create_detail_project(body: DetailProjectCreateRequest, service: DetailProjectService = Depends(get_detail_project_service)):
    return DetailProjectEnvelope(data={"project": service.create(str(body.request_id))})


@router.get("", response_model=DetailProjectEnvelope)
def list_detail_projects(cursor: str | None = Query(default=None,max_length=2048), limit: int = Query(default=30, ge=1, le=50),
    service: DetailProjectService = Depends(get_detail_project_service)):
    from services.detail_project_tasks import DetailProjectTasks
    return DetailProjectEnvelope(data=DetailProjectTasks(service).list(cursor, limit))


@router.get("/status", response_model=DetailProjectEnvelope)
def detail_project_status(ids: str = Query(max_length=3800), service: DetailProjectService = Depends(get_detail_project_service)):
    from services.detail_project_tasks import DetailProjectTasks
    try:
        values = list(dict.fromkeys(str(UUID(value)) for value in ids.split(",") if value))
    except ValueError as exc:
        raise AppException("DETAIL_TASK_IDS_INVALID", "任务编号无效", 400) from exc
    if not values or len(values)>100:
        raise AppException("DETAIL_TASK_IDS_INVALID", "一次最多刷新100个任务", 400)
    return DetailProjectEnvelope(data={"items": DetailProjectTasks(service).status(values)})


@router.get("/{project_id}", response_model=DetailProjectEnvelope)
def get_detail_project(project_id: UUID,service: DetailProjectService = Depends(get_detail_project_service)):
    from services.detail_page_generation import DetailPageGeneration
    return DetailProjectEnvelope(data={"project": DetailPageGeneration(service.db,service.user_id,service.org_id).read(str(project_id))})


@router.post("/{project_id}/analyze", response_model=DetailProjectEnvelope)
def start_detail_project(project_id: UUID,body: DetailRunRequest,service: DetailProjectService = Depends(get_detail_project_service)):
    from services.detail_page_generation import DetailPageGeneration
    return DetailProjectEnvelope(data={"project": DetailPageGeneration(service.db,service.user_id,service.org_id).start(
        str(project_id),body.version,str(body.request_id))})


@router.post("/{project_id}/archive", response_model=DetailProjectEnvelope)
def archive_detail_project(project_id: UUID,service: DetailProjectService = Depends(get_detail_project_service)):
    project = service.get_by_id(str(project_id))
    if project["status"] not in {"draft","completed","failed"}:
        raise AppException("DETAIL_RUN_ACTIVE", "请等待当前任务完成", 409)
    service.db.table("detail_projects").update({"status":"archived"}).eq("id",str(project_id)).eq("user_id",service.user_id).execute()
    return DetailProjectEnvelope(data={"project": None})


@router.post("/{project_id}/resume", response_model=DetailProjectEnvelope)
def resume_detail_project(project_id: UUID,body: DetailResumeRequest,service: DetailProjectService = Depends(get_detail_project_service)):
    from services.detail_page_generation import DetailPageGeneration
    generation=DetailPageGeneration(service.db,service.user_id,service.org_id)
    return DetailProjectEnvelope(data={"project":generation.resume(str(project_id),str(body.plan_id),str(body.request_id))})


@router.post("/{project_id}/stop-recovery", response_model=DetailProjectEnvelope)
def stop_detail_recovery(project_id: UUID,service: DetailProjectService = Depends(get_detail_project_service)):
    from services.detail_page_generation import DetailPageGeneration
    generation=DetailPageGeneration(service.db,service.user_id,service.org_id)
    return DetailProjectEnvelope(data={"project":generation.stop_recovery(str(project_id))})


@router.post("/current/images", response_model=DetailProjectEnvelope)
def attach_detail_project_image(
    body: DetailImageAttachRequest,
    service: DetailProjectService = Depends(get_detail_project_service),
) -> DetailProjectEnvelope:
    try:
        return DetailProjectEnvelope(
            data={"project": service.attach_image(body.workspace_path, body.category)}
        )
    except AppException:
        raise
    except Exception as exc:
        logger.error(f"Detail project image route failed | error={exc}")
        raise AppException("DETAIL_IMAGE_ATTACH_FAILED", "图片关联失败", 500) from exc


@router.post("/{project_id}/images", response_model=DetailProjectEnvelope)
def attach_project_image(project_id: UUID, body: DetailImageAttachRequest,
    service: DetailProjectService = Depends(get_detail_project_service)):
    return DetailProjectEnvelope(data={"project": service.attach_image(body.workspace_path, body.category, str(project_id))})


@router.patch("/{project_id}", response_model=DetailProjectEnvelope)
def update_detail_project(
    project_id: str, body: DetailProjectSettingsPatch,
    service: DetailProjectService = Depends(get_detail_project_service),
) -> DetailProjectEnvelope:
    settings = body.model_dump(exclude={"version"}, exclude_unset=True)
    return DetailProjectEnvelope(data={"project": service.update_settings(project_id, body.version, settings)})


@router.delete("/{project_id}/images/{image_id}", response_model=DetailProjectEnvelope)
def remove_detail_project_image(
    project_id: str, image_id: str, body: DetailProjectVersionRequest,
    service: DetailProjectService = Depends(get_detail_project_service),
) -> DetailProjectEnvelope:
    return DetailProjectEnvelope(data={"project": service.remove_image(project_id, image_id, body.version)})


@router.patch("/{project_id}/images/{image_id}", response_model=DetailProjectEnvelope)
def update_detail_project_image(
    project_id: str, image_id: str, body: DetailImageCategoryPatch,
    service: DetailProjectService = Depends(get_detail_project_service),
) -> DetailProjectEnvelope:
    project = service.update_category(project_id, image_id, body.version, body.category)
    return DetailProjectEnvelope(data={"project": project})


@router.put("/{project_id}/images/order", response_model=DetailProjectEnvelope)
def reorder_detail_project_images(
    project_id: str, body: DetailImageOrderRequest,
    service: DetailProjectService = Depends(get_detail_project_service),
) -> DetailProjectEnvelope:
    project = service.reorder_images(project_id, body.version, body.image_ids)
    return DetailProjectEnvelope(data={"project": project})
