"""组织成员任职管理 REST API

这是权限模型 V1 的成员管理面板后端，提供：
- GET    /org-members/list        列出企业所有成员（含部门/职位）
- GET    /org-members/departments  列出企业所有部门
- GET    /org-members/positions    列出企业所有职位
- PATCH  /org-members/{user_id}/assignment  修改成员部门/职位/数据范围

权限要求：
- 列表查询：org_members.role IN ('owner', 'admin')
- 修改：org_members.role IN ('owner', 'admin')

设计文档: docs/document/TECH_组织架构与权限模型.md §九
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

from api.deps import CurrentUserId, OrgCtx, ScopedDB, Database
from services.permissions.checker import get_checker


router = APIRouter(prefix="/org-members", tags=["组织成员任职"])


# ════════════════════════════════════════════════════════
# Schemas
# ════════════════════════════════════════════════════════

class UpdateAssignmentRequest(BaseModel):
    """修改成员任职"""
    department_id: Optional[str] = None
    position_code: Optional[Literal["boss", "vp", "manager", "deputy", "member"]] = None
    job_title: Optional[str] = Field(None, max_length=50)
    data_scope: Optional[Literal["all", "dept_subtree", "self"]] = None
    data_scope_dept_ids: Optional[List[str]] = None  # 副总分管部门


# ════════════════════════════════════════════════════════
# 工具函数
# ════════════════════════════════════════════════════════

def _require_org(org_ctx: Any) -> str:
    if not org_ctx.org_id:
        raise HTTPException(403, "此功能仅企业用户可用")
    return org_ctx.org_id


def _require_admin(db: Any, user_id: str, org_id: str) -> str:
    """要求当前用户是 owner 或 admin（org_members.role）"""
    result = db.table("org_members") \
        .select("role") \
        .eq("org_id", org_id) \
        .eq("user_id", user_id) \
        .limit(1) \
        .execute()
    if not result.data:
        raise HTTPException(403, "您不是该组织成员")
    role = result.data[0]["role"]
    if role not in ("owner", "admin"):
        raise HTTPException(403, "仅老板/管理员可管理成员任职")
    return role


# ════════════════════════════════════════════════════════
# 路由
# ════════════════════════════════════════════════════════

@router.get("/list", summary="列出企业所有成员（含部门/职位）")
async def list_members_with_assignments(
    user_id: CurrentUserId,
    org_ctx: OrgCtx,
    db: Database,
) -> Dict[str, Any]:
    """返回企业所有成员，含部门/职位/数据范围"""
    org_id = _require_org(org_ctx)
    _require_admin(db, user_id, org_id)

    # 1. 查所有成员（org_members）
    members_resp = db.table("org_members") \
        .select("user_id, role, status") \
        .eq("org_id", org_id) \
        .eq("status", "active") \
        .execute()
    members = list(members_resp.data or [])

    if not members:
        return {"success": True, "data": []}

    user_ids = [m["user_id"] for m in members]

    # 2. 查 users
    users_resp = db.table("users") \
        .select("id, nickname, avatar_url, phone") \
        .in_("id", user_ids) \
        .execute()
    users_map = {u["id"]: u for u in (users_resp.data or [])}

    # 3. 查任职信息
    assignments_resp = db.table("org_member_assignments") \
        .select("user_id, department_id, position_id, job_title, data_scope, data_scope_dept_ids") \
        .eq("org_id", org_id) \
        .in_("user_id", user_ids) \
        .eq("is_primary", True) \
        .execute()
    assignments_map = {a["user_id"]: a for a in (assignments_resp.data or [])}

    # 4. 查部门和职位
    dept_ids = list({a["department_id"] for a in assignments_map.values() if a.get("department_id")})
    pos_ids = list({a["position_id"] for a in assignments_map.values() if a.get("position_id")})

    dept_map: Dict[str, Dict] = {}
    if dept_ids:
        depts_resp = db.table("org_departments") \
            .select("id, name, type") \
            .in_("id", dept_ids) \
            .execute()
        dept_map = {d["id"]: d for d in (depts_resp.data or [])}

    pos_map: Dict[str, Dict] = {}
    if pos_ids:
        pos_resp = db.table("org_positions") \
            .select("id, code, name") \
            .in_("id", pos_ids) \
            .execute()
        pos_map = {p["id"]: p for p in (pos_resp.data or [])}

    # 5. 拼装结果
    result = []
    for m in members:
        uid = m["user_id"]
        user = users_map.get(uid, {})
        a = assignments_map.get(uid, {})
        dept = dept_map.get(a.get("department_id"), {}) if a else {}
        pos = pos_map.get(a.get("position_id"), {}) if a else {}

        result.append({
            "user_id": uid,
            "nickname": user.get("nickname", "未知"),
            "avatar_url": user.get("avatar_url"),
            "phone": user.get("phone"),
            "org_role": m["role"],  # owner/admin/member
            "assignment": {
                "department_id": a.get("department_id"),
                "department_name": dept.get("name"),
                "department_type": dept.get("type"),
                "position_id": a.get("position_id"),
                "position_code": pos.get("code"),
                "position_name": pos.get("name"),
                "job_title": a.get("job_title"),
                "data_scope": a.get("data_scope") or "self",
                "data_scope_dept_ids": a.get("data_scope_dept_ids") or [],
            } if a else None,
        })

    return {"success": True, "data": result}


@router.get("/departments", summary="列出企业所有部门")
async def list_departments(
    user_id: CurrentUserId,
    org_ctx: OrgCtx,
    db: Database,
) -> Dict[str, Any]:
    org_id = _require_org(org_ctx)
    # 任意成员都能看部门列表（用于个人信息展示）
    result = db.table("org_departments") \
        .select("id, name, type, sort_order") \
        .eq("org_id", org_id) \
        .order("sort_order") \
        .execute()
    return {"success": True, "data": list(result.data or [])}


@router.get("/positions", summary="列出企业所有职位")
async def list_positions(
    user_id: CurrentUserId,
    org_ctx: OrgCtx,
    db: Database,
) -> Dict[str, Any]:
    org_id = _require_org(org_ctx)
    result = db.table("org_positions") \
        .select("id, code, name, level") \
        .eq("org_id", org_id) \
        .order("level") \
        .execute()
    return {"success": True, "data": list(result.data or [])}


@router.patch("/{target_user_id}/assignment", summary="修改成员部门/职位")
async def update_member_assignment(
    target_user_id: str,
    payload: UpdateAssignmentRequest,
    user_id: CurrentUserId,
    org_ctx: OrgCtx,
    db: Database,
) -> Dict[str, Any]:
    """修改成员的部门/职位/数据范围

    权限：仅老板/admin 可调用
    """
    org_id = _require_org(org_ctx)
    _require_admin(db, user_id, org_id)

    # 1. 查目标用户在该组织的现有任职
    existing_resp = db.table("org_member_assignments") \
        .select("id, department_id, position_id, data_scope") \
        .eq("org_id", org_id) \
        .eq("user_id", target_user_id) \
        .eq("is_primary", True) \
        .limit(1) \
        .execute()
    existing = existing_resp.data[0] if existing_resp.data else None

    # 2. 准备更新字段
    update: Dict[str, Any] = {}

    if payload.department_id is not None:
        # 校验部门归属于本组织
        dept_resp = db.table("org_departments") \
            .select("id") \
            .eq("id", payload.department_id) \
            .eq("org_id", org_id) \
            .limit(1) \
            .execute()
        if not dept_resp.data:
            raise HTTPException(400, "部门不存在或不属于本组织")
        update["department_id"] = payload.department_id

    if payload.position_code is not None:
        pos_resp = db.table("org_positions") \
            .select("id") \
            .eq("org_id", org_id) \
            .eq("code", payload.position_code) \
            .limit(1) \
            .execute()
        if not pos_resp.data:
            raise HTTPException(400, "职位不存在")
        update["position_id"] = pos_resp.data[0]["id"]

    if payload.job_title is not None:
        update["job_title"] = payload.job_title

    if payload.data_scope is not None:
        update["data_scope"] = payload.data_scope

    if payload.data_scope_dept_ids is not None:
        update["data_scope_dept_ids"] = payload.data_scope_dept_ids

    if not update:
        return {"success": True, "message": "无变更"}

    update["updated_at"] = datetime.now(timezone.utc).isoformat()
    # 权限版本号 +1，触发缓存失效
    update["perm_version"] = (existing or {}).get("perm_version", 0) + 1 if existing else 1

    # 3. 更新或创建
    if existing:
        db.table("org_member_assignments") \
            .update(update) \
            .eq("id", existing["id"]) \
            .execute()
    else:
        # 不存在则创建
        from uuid import uuid4
        # 没指定 position_code 时默认 member
        if "position_id" not in update:
            pos_resp = db.table("org_positions") \
                .select("id") \
                .eq("org_id", org_id) \
                .eq("code", "member") \
                .limit(1) \
                .execute()
            if pos_resp.data:
                update["position_id"] = pos_resp.data[0]["id"]
        if "data_scope" not in update:
            update["data_scope"] = "self"

        update.update({
            "id": str(uuid4()),
            "org_id": org_id,
            "user_id": target_user_id,
            "is_primary": True,
        })
        db.table("org_member_assignments").insert(update).execute()

    # 4. 清除该用户的权限缓存
    try:
        get_checker(db).invalidate_cache(target_user_id)
    except Exception:
        pass

    logger.info(
        f"Member assignment updated | actor={user_id} | "
        f"target={target_user_id} | org={org_id} | changes={list(update.keys())}"
    )

    return {"success": True}
