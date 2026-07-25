"""Public organization identity routes used before login."""

from uuid import UUID

from fastapi import APIRouter, HTTPException

from api.deps import Database


router = APIRouter()


@router.get("/public/{org_id}/name", summary="获取企业名称（公开）")
async def get_org_name_public(org_id: str, db: Database):
    """Return the display name required by the organization login page."""
    try:
        UUID(org_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="企业不存在") from exc
    result = db.rpc(
        "get_public_organization_name", {"p_org_id": org_id},
    ).execute()
    if not result or not result.data:
        raise HTTPException(status_code=404, detail="企业不存在")
    if result.data["status"] != "active":
        raise HTTPException(status_code=400, detail="企业已停用")
    return {"name": result.data["name"]}
