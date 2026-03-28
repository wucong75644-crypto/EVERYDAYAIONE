"""
企业管理路由

企业 CRUD、成员管理、邀请管理。
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.deps import CurrentUserId, Database
from core.exceptions import AppException
from services.org.config_resolver import OrgConfigResolver
from services.org.org_service import OrgService

router = APIRouter(prefix="/org", tags=["企业管理"])


def _get_org_service(db: Database) -> OrgService:
    return OrgService(db)


def _get_config_resolver(db: Database) -> OrgConfigResolver:
    return OrgConfigResolver(db)


# ── Request Models ──────────────────────────────────────────


class CreateOrgRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, description="企业全称")
    owner_phone: str = Field(..., pattern=r"^1[3-9]\d{9}$", description="企业Owner手机号")


class UpdateOrgRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    logo_url: Optional[str] = None
    features: Optional[dict] = None


class AddMemberRequest(BaseModel):
    user_id: str = Field(..., description="目标用户ID")
    role: str = Field("member", pattern="^(admin|member)$")


class ChangeMemberRoleRequest(BaseModel):
    role: str = Field(..., pattern="^(admin|member)$")


class CreateInvitationRequest(BaseModel):
    phone: str = Field(..., pattern=r"^1[3-9]\d{9}$", description="被邀请人手机号")
    role: str = Field("member", pattern="^(admin|member)$")


class AcceptInvitationRequest(BaseModel):
    invite_token: str


class SetConfigRequest(BaseModel):
    key: str = Field(..., min_length=1, max_length=100, description="配置键名")
    value: str = Field(..., min_length=1, description="配置值（明文，后端加密存储）")


# ── 企业 CRUD ───────────────────────────────────────────────


@router.get("", summary="我的企业列表")
async def list_my_orgs(
    user_id: CurrentUserId,
    svc: OrgService = Depends(_get_org_service),
):
    return svc.list_user_organizations(user_id)


@router.post("", summary="创建企业（超管）")
async def create_org(
    body: CreateOrgRequest,
    user_id: CurrentUserId,
    db: Database,
    svc: OrgService = Depends(_get_org_service),
):
    """仅超管可调用。创建企业并指定 owner（通过手机号查找）。"""
    user = db.table("users").select("role").eq("id", user_id).single().execute()
    if not user.data or user.data["role"] != "super_admin":
        raise HTTPException(status_code=403, detail="仅超级管理员可创建企业")

    try:
        owner = db.table("users").select("id, status").eq("phone", body.owner_phone).execute()
        if not owner.data:
            raise HTTPException(status_code=404, detail=f"手机号 {body.owner_phone} 未注册")
        if owner.data[0].get("status") != "active":
            raise HTTPException(status_code=400, detail=f"该用户已被禁用，无法设为企业 owner")
        owner_id = owner.data[0]["id"]
        org = svc.create_organization(body.name, owner_id)
        return {"success": True, "data": org}
    except AppException as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)


@router.get("/{org_id}", summary="获取企业信息")
async def get_org(
    org_id: str,
    user_id: CurrentUserId,
    svc: OrgService = Depends(_get_org_service),
):
    try:
        svc.require_role(org_id, user_id, ("owner", "admin", "member"))
        org = svc.get_organization(org_id)
        return org
    except AppException as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)


@router.patch("/{org_id}", summary="更新企业信息")
async def update_org(
    org_id: str,
    body: UpdateOrgRequest,
    user_id: CurrentUserId,
    svc: OrgService = Depends(_get_org_service),
):
    try:
        org = svc.update_organization(
            org_id, user_id,
            name=body.name, logo_url=body.logo_url, features=body.features,
        )
        return {"success": True, "data": org}
    except AppException as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)


# ── 成员管理 ────────────────────────────────────────────────


@router.get("/{org_id}/members", summary="成员列表")
async def list_members(
    org_id: str,
    user_id: CurrentUserId,
    svc: OrgService = Depends(_get_org_service),
):
    try:
        return svc.list_members(org_id, user_id)
    except AppException as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)


@router.post("/{org_id}/members", summary="添加成员")
async def add_member(
    org_id: str,
    body: AddMemberRequest,
    user_id: CurrentUserId,
    svc: OrgService = Depends(_get_org_service),
):
    try:
        member = svc.add_member(org_id, user_id, body.user_id, body.role)
        return {"success": True, "data": member}
    except AppException as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)


@router.delete("/{org_id}/members/{target_user_id}", summary="移除成员")
async def remove_member(
    org_id: str,
    target_user_id: str,
    user_id: CurrentUserId,
    svc: OrgService = Depends(_get_org_service),
):
    try:
        svc.remove_member(org_id, user_id, target_user_id)
        return {"success": True, "message": "成员已移除"}
    except AppException as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)


@router.patch("/{org_id}/members/{target_user_id}/role", summary="变更成员角色")
async def change_role(
    org_id: str,
    target_user_id: str,
    body: ChangeMemberRoleRequest,
    user_id: CurrentUserId,
    svc: OrgService = Depends(_get_org_service),
):
    try:
        result = svc.change_member_role(org_id, user_id, target_user_id, body.role)
        return {"success": True, "data": result}
    except AppException as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)


# ── 邀请管理 ────────────────────────────────────────────────


@router.post("/{org_id}/invitations", summary="创建邀请")
async def create_invitation(
    org_id: str,
    body: CreateInvitationRequest,
    user_id: CurrentUserId,
    svc: OrgService = Depends(_get_org_service),
):
    try:
        inv = svc.create_invitation(org_id, user_id, body.phone, body.role)
        return {"success": True, "data": inv}
    except AppException as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)


@router.post("/invitations/accept", summary="接受邀请")
async def accept_invitation(
    body: AcceptInvitationRequest,
    user_id: CurrentUserId,
    svc: OrgService = Depends(_get_org_service),
):
    try:
        result = svc.accept_invitation(body.invite_token, user_id)
        return {"success": True, "data": result}
    except AppException as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)


# ── 企业配置管理 ───────────────────────────────────────


@router.get("/{org_id}/configs", summary="查看企业已配置的 Key 列表")
async def list_org_configs(
    org_id: str,
    user_id: CurrentUserId,
    svc: OrgService = Depends(_get_org_service),
    resolver: OrgConfigResolver = Depends(_get_config_resolver),
):
    """列出企业已配置的 key（不返回值），admin+ 可用"""
    try:
        svc._require_role(org_id, user_id, ("owner", "admin"))
        keys = resolver.list_keys(org_id)
        return {"success": True, "data": keys}
    except AppException as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)


@router.put("/{org_id}/configs", summary="设置企业配置")
async def set_org_config(
    org_id: str,
    body: SetConfigRequest,
    user_id: CurrentUserId,
    svc: OrgService = Depends(_get_org_service),
    resolver: OrgConfigResolver = Depends(_get_config_resolver),
):
    """写入企业配置（AES 加密存储），admin+ 可用"""
    try:
        svc._require_role(org_id, user_id, ("owner", "admin"))
        resolver.set(org_id, body.key, body.value, updated_by=user_id)
        return {"success": True, "message": f"配置 {body.key} 已更新"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except AppException as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)


@router.delete("/{org_id}/configs/{config_key}", summary="删除企业配置")
async def delete_org_config(
    org_id: str,
    config_key: str,
    user_id: CurrentUserId,
    svc: OrgService = Depends(_get_org_service),
    resolver: OrgConfigResolver = Depends(_get_config_resolver),
):
    """删除企业配置，admin+ 可用"""
    try:
        svc._require_role(org_id, user_id, ("owner", "admin"))
        resolver.delete(org_id, config_key)
        return {"success": True, "message": f"配置 {config_key} 已删除"}
    except AppException as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)
