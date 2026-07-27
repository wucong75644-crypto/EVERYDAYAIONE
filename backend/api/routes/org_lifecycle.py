"""Platform organization suspension and restoration routes."""

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger

from api.deps import CurrentUserId
from core.exceptions import AppException
from services.org.org_service import OrgService
from .org_dependencies import get_platform_org_service


router = APIRouter()


@router.post("/admin/{org_id}/suspend", summary="停用企业（超管）")
async def suspend_org(
    org_id: str,
    user_id: CurrentUserId,
    svc: OrgService = Depends(get_platform_org_service),
):
    """平台 Scope 原子停用 active 企业，企业数据保持不变。"""
    try:
        return {"success": True, "data": svc.suspend_organization(org_id)}
    except AppException as error:
        raise HTTPException(
            status_code=error.status_code, detail=error.message,
        )
    except Exception as error:
        logger.error(
            "Organization suspension failed | actor_id={} | org_id={} | "
            "error_type={}",
            user_id, org_id, type(error).__name__,
        )
        raise HTTPException(
            status_code=503, detail="服务暂时不可用，请稍后重试",
        )


@router.post("/admin/{org_id}/restore", summary="恢复企业（超管）")
async def restore_org(
    org_id: str,
    user_id: CurrentUserId,
    svc: OrgService = Depends(get_platform_org_service),
):
    """平台 Scope 原子恢复 suspended 企业，不改变成员或 Secret 状态。"""
    try:
        return {"success": True, "data": svc.restore_organization(org_id)}
    except AppException as error:
        raise HTTPException(
            status_code=error.status_code, detail=error.message,
        )
    except Exception as error:
        logger.error(
            "Organization restoration failed | actor_id={} | org_id={} | "
            "error_type={}",
            user_id, org_id, type(error).__name__,
        )
        raise HTTPException(
            status_code=503, detail="服务暂时不可用，请稍后重试",
        )
