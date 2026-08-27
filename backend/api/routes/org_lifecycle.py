"""平台企业停用与恢复接口。"""

from fastapi import APIRouter, HTTPException

from api.deps import CurrentUserId, Database
from core.exceptions import AppException
from services.org.org_service import OrgService

router = APIRouter(prefix="/admin", tags=["企业生命周期"])


def _require_super_admin(db, user_id: str) -> None:
    result = db.table("users").select("role").eq("id", user_id).maybe_single().execute()
    if not result or not result.data or result.data.get("role") != "super_admin":
        raise HTTPException(status_code=403, detail="仅超级管理员可执行企业生命周期操作")


@router.post("/{org_id}/suspend", summary="停用企业")
async def suspend_org(org_id: str, user_id: CurrentUserId, db: Database):
    _require_super_admin(db, user_id)
    try:
        return {"success": True, "data": OrgService(db).suspend_organization(org_id)}
    except AppException as error:
        raise HTTPException(status_code=error.status_code, detail=error.message) from error


@router.post("/{org_id}/restore", summary="恢复企业")
async def restore_org(org_id: str, user_id: CurrentUserId, db: Database):
    _require_super_admin(db, user_id)
    try:
        return {"success": True, "data": OrgService(db).restore_organization(org_id)}
    except AppException as error:
        raise HTTPException(status_code=error.status_code, detail=error.message) from error
