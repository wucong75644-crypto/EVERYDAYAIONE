"""Shared authorization guard for Kuaimai administration routes."""

from fastapi import HTTPException


def require_kuaimai_admin(org_ctx) -> str:
    if not org_ctx.org_id:
        raise HTTPException(
            status_code=400,
            detail="必须在企业上下文中操作（X-Org-Id）",
        )
    if org_ctx.org_role not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="仅企业管理员可访问")
    return org_ctx.org_id
