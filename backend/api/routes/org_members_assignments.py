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
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

from api.deps import CurrentUserId, OrgCtx, ScopedDB, Database


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


class UpdateProfileRequest(BaseModel):
    """修改成员显示名（覆盖企微同步过来的）"""
    nickname: str = Field(..., min_length=1, max_length=50)


# ════════════════════════════════════════════════════════
# 工具函数
# ════════════════════════════════════════════════════════

def _require_org(org_ctx: Any) -> str:
    if not org_ctx.org_id:
        raise HTTPException(403, "此功能仅企业用户可用")
    return org_ctx.org_id


def _require_admin(db: Any, user_id: str, org_id: str) -> str:
    """要求当前 Runtime Actor 是 owner 或 admin。"""
    result = db.rpc("get_governed_actor_authority", {
        "p_org_id": org_id,
    }).execute()
    role = result.data if result else None
    if role not in ("owner", "admin"):
        raise HTTPException(403, "仅老板/管理员可管理成员任职")
    return str(role)


def _rpc_data(db: Any, name: str, params: Dict[str, Any]) -> Any:
    try:
        return db.rpc(name, params).execute().data
    except Exception as exc:
        message = str(exc)
        if "GOVERNANCE_AUTHORITY_DENIED" in message:
            raise HTTPException(403, "无权执行该成员任职变更") from exc
        if "GOVERNANCE_ARGUMENT_INVALID" in message:
            raise HTTPException(400, "成员任职参数无效") from exc
        raise


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

    result = _rpc_data(db, "list_governed_member_assignments", {
        "p_org_id": org_id,
    })
    return {"success": True, "data": list(result or [])}


@router.get("/me", summary="获取当前用户在本企业的成员信息")
async def get_my_member_info(
    user_id: CurrentUserId,
    org_ctx: OrgCtx,
    db: Database,
) -> Dict[str, Any]:
    """
    返回当前用户在本企业的精简信息：nickname / wecom_userid / 任职。

    任何企业成员都能调用（不需要管理员权限）。
    用途：TaskForm 的"推送给我自己"模式需要拿到当前用户的 wecom_userid
         构造 push_target；普通员工无权调 /wecom-collected 时走这个。
    """
    org_id = _require_org(org_ctx)

    # 校验是企业成员
    member_resp = (
        db.table("org_members")
        .select("user_id, role")
        .eq("org_id", org_id)
        .eq("user_id", user_id)
        .eq("status", "active")
        .limit(1)
        .execute()
    )
    if not member_resp.data:
        raise HTTPException(403, "您不是该组织成员")

    # 查 user 基本信息
    user_resp = (
        db.table("users")
        .select("id, nickname, avatar_url")
        .eq("id", user_id)
        .limit(1)
        .execute()
    )
    user = (user_resp.data[0] if user_resp.data else {}) or {}

    # 查 wecom_user_mappings 拿 wecom_userid（可能没有：纯 web 注册的成员）
    wecom_userid: Optional[str] = None
    try:
        wm_resp = (
            db.table("wecom_user_mappings")
            .select("wecom_userid")
            .eq("user_id", user_id)
            .eq("org_id", org_id)
            .limit(1)
            .execute()
        )
        if wm_resp.data:
            wecom_userid = wm_resp.data[0].get("wecom_userid")
    except Exception as e:
        logger.warning(f"get_my_member_info: lookup wecom_userid failed | {e}")

    return {
        "success": True,
        "data": {
            "user_id": user_id,
            "nickname": user.get("nickname") or "未知",
            "avatar_url": user.get("avatar_url"),
            "wecom_userid": wecom_userid,
        },
    }


@router.get("/wecom-collected", summary="列出已和机器人交互过的企微员工")
async def list_wecom_collected_members(
    user_id: CurrentUserId,
    org_ctx: OrgCtx,
    db: Database,
) -> Dict[str, Any]:
    """
    返回企业里所有"和机器人聊过天"的员工（即 wecom_user_mappings 收集到的）。

    数据来源：
    - wecom_user_mappings (org 内 + 已激活)
    - 关联 users / org_member_assignments / org_departments / org_positions

    用途：员工管理面板，给管理员展示真实交互过的员工，便于设置部门/职位。
    没和机器人交互过的员工不在此列表——他们首次发消息时会被自动收集。

    权限：仅老板/admin 可调用（同 _require_admin）
    """
    org_id = _require_org(org_ctx)
    _require_admin(db, user_id, org_id)

    result = list(_rpc_data(db, "list_governed_wecom_assignments", {
        "p_org_id": org_id,
    }) or [])
    return {"success": True, "data": result, "total": len(result)}


@router.get("/departments", summary="列出企业所有部门")
async def list_departments(
    user_id: CurrentUserId,
    org_ctx: OrgCtx,
    db: Database,
) -> Dict[str, Any]:
    org_id = _require_org(org_ctx)
    # 任意成员都能看部门列表（用于个人信息展示）
    result = db.rpc("list_runtime_org_departments", {
        "p_org_id": org_id,
    }).execute()
    return {"success": True, "data": list(result.data or [])}


@router.get("/positions", summary="列出企业所有职位")
async def list_positions(
    user_id: CurrentUserId,
    org_ctx: OrgCtx,
    db: Database,
) -> Dict[str, Any]:
    org_id = _require_org(org_ctx)
    result = db.rpc("list_runtime_org_positions", {
        "p_org_id": org_id,
    }).execute()
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

    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        return {"success": True, "message": "无变更"}
    _rpc_data(db, "update_governed_member_assignment", {
        "p_org_id": org_id,
        "p_target_user_id": target_user_id,
        "p_changes": changes,
    })

    logger.info(
        f"Member assignment updated | actor={user_id} | "
        f"target={target_user_id} | org={org_id} | changes={list(changes.keys())}"
    )

    return {"success": True}


@router.patch("/{target_user_id}/profile", summary="修改成员显示名")
async def update_member_profile(
    target_user_id: str,
    payload: UpdateProfileRequest,
    user_id: CurrentUserId,
    org_ctx: OrgCtx,
    db: Database,
) -> Dict[str, Any]:
    """设置企业内显示名，不修改用户个人昵称。"""
    org_id = _require_org(org_ctx)
    _require_admin(db, user_id, org_id)
    new_nickname = payload.nickname.strip()
    if not new_nickname:
        raise HTTPException(400, "昵称不能为空")
    try:
        db.rpc("update_governed_member_display_name", {
            "p_org_id": org_id,
            "p_target_user_id": target_user_id,
            "p_display_name": new_nickname,
        }).execute()
    except Exception as exc:
        if "GOVERNANCE_MEMBER_MISSING" in str(exc):
            raise HTTPException(404, "目标用户不属于本企业或已停用") from exc
        if "GOVERNANCE_AUTHORITY_DENIED" in str(exc):
            raise HTTPException(403, "无权修改该成员显示名") from exc
        raise

    logger.info(
        f"Member nickname updated | actor={user_id} | "
        f"target={target_user_id} | org={org_id} | new_nickname={new_nickname}"
    )

    return {"success": True}
