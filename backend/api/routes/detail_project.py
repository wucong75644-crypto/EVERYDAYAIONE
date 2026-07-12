"""主图详情页草稿接口。"""

from fastapi import APIRouter, Depends
from loguru import logger

from api.deps import OrgCtx, ScopedDB
from core.exceptions import AppException
from schemas.detail_project import (
    DetailImageAttachRequest, DetailImageCategoryPatch, DetailImageOrderRequest,
    DetailProjectEnvelope, DetailProjectSettingsPatch, DetailProjectVersionRequest,
)
from services.detail_project_service import DetailProjectService


router = APIRouter(prefix="/detail-projects", tags=["主图详情页"])


def get_detail_project_service(ctx: OrgCtx, db: ScopedDB) -> DetailProjectService:
    return DetailProjectService(db, ctx.user_id, ctx.org_id)


@router.get("/current", response_model=DetailProjectEnvelope)
def get_current_detail_project(
    service: DetailProjectService = Depends(get_detail_project_service),
) -> DetailProjectEnvelope:
    return DetailProjectEnvelope(data={"project": service.get_current()})


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
