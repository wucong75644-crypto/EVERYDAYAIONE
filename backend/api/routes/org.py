"""
企业管理路由

企业 CRUD、成员管理、邀请管理。
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
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


# ── 公开接口（无需认证）──────────────────────────────────────


@router.get("/public/{org_id}/name", summary="获取企业名称（公开）")
async def get_org_name_public(org_id: str, db: Database):
    """登录页显示企业名称，不需要认证"""
    result = (
        db.table("organizations")
        .select("name, status")
        .eq("id", org_id)
        .maybe_single()
        .execute()
    )
    if not result or not result.data:
        raise HTTPException(status_code=404, detail="企业不存在")
    if result.data["status"] != "active":
        raise HTTPException(status_code=400, detail="企业已停用")
    return {"name": result.data["name"]}


# ── Request Models ──────────────────────────────────────────


class CreateOrgRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, description="企业全称")
    owner_phone: str = Field(..., pattern=r"^1[3-9]\d{9}$", description="企业Owner手机号")


class UpdateOrgRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    logo_url: Optional[str] = None
    features: Optional[dict] = None
    wecom_corp_id: Optional[str] = Field(None, max_length=100)


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


@router.get("/admin/all", summary="所有企业列表（超管）")
async def list_all_orgs(
    user_id: CurrentUserId,
    db: Database,
    svc: OrgService = Depends(_get_org_service),
):
    """仅超管可调用。列出平台所有企业。"""
    user = db.table("users").select("role").eq("id", user_id).maybe_single().execute()
    if not user or not user.data or user.data["role"] != "super_admin":
        raise HTTPException(status_code=403, detail="仅超级管理员可查看")

    result = db.table("organizations").select(
        "id, name, status, owner_id, created_at"
    ).order("created_at", desc=True).execute()

    orgs = []
    for org in (result.data or []):
        # 查成员数
        members_result = db.table("org_members").select(
            "user_id"
        ).eq("org_id", org["id"]).eq("status", "active").execute()
        member_count = len(members_result.data) if members_result.data else 0
        orgs.append({
            **org,
            "member_count": member_count,
        })
    return orgs


@router.get("/admin/search-user", summary="搜索用户（超管）")
async def search_user(
    phone: str = Query(..., pattern=r"^1[3-9]\d{9}$", description="手机号"),
    user_id: CurrentUserId = None,
    db: Database = None,
):
    """超管通过手机号搜索用户（用于指定 owner / 添加成员）"""
    user = db.table("users").select("role").eq("id", user_id).maybe_single().execute()
    if not user or not user.data or user.data["role"] != "super_admin":
        raise HTTPException(status_code=403, detail="仅超级管理员可查看")

    result = db.table("users").select(
        "id, nickname, phone, status, created_at"
    ).eq("phone", phone).execute()
    if not result.data:
        return {"found": False, "user": None}

    u = result.data[0]
    return {
        "found": True,
        "user": {
            "id": u["id"],
            "nickname": u["nickname"],
            "phone": u["phone"][:3] + "****" + u["phone"][-4:] if u.get("phone") else None,
            "status": u["status"],
        },
    }


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
            wecom_corp_id=body.wecom_corp_id,
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


@router.get("/invitations/pending", summary="我的待接受邀请")
async def list_pending_invitations(
    user_id: CurrentUserId,
    db: Database,
):
    """查询当前用户手机号的待接受邀请"""
    # 获取用户手机号
    user = db.table("users").select("phone").eq("id", user_id).maybe_single().execute()
    if not user or not user.data or not user.data.get("phone"):
        return []

    phone = user.data["phone"]
    result = (
        db.table("org_invitations")
        .select("invite_token, role, expires_at, org_id")
        .eq("phone", phone)
        .eq("status", "pending")
        .execute()
    )

    invitations = []
    for inv in (result.data or []):
        # 查企业名
        org = db.table("organizations").select("name").eq("id", inv["org_id"]).maybe_single().execute()
        org_name = org.data["name"] if org and org.data else "未知企业"
        invitations.append({
            "invite_token": inv["invite_token"],
            "org_name": org_name,
            "role": inv["role"],
            "expires_at": inv["expires_at"],
        })
    return invitations


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
        svc.require_role(org_id, user_id, ("owner", "admin"))
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
        svc.require_role(org_id, user_id, ("owner", "admin"))
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
        svc.require_role(org_id, user_id, ("owner", "admin"))
        resolver.delete(org_id, config_key)
        return {"success": True, "message": f"配置 {config_key} 已删除"}
    except AppException as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)


@router.post("/{org_id}/configs/test-erp", summary="测试 ERP 连接")
async def test_erp_connection(
    org_id: str,
    user_id: CurrentUserId,
    svc: OrgService = Depends(_get_org_service),
    resolver: OrgConfigResolver = Depends(_get_config_resolver),
):
    """用企业配置的 ERP 凭证发一个简单查询，验证连接是否正常"""
    try:
        svc.require_role(org_id, user_id, ("owner", "admin"))
        creds = resolver.get_erp_credentials(org_id)

        from services.kuaimai.client import KuaiMaiClient
        client = KuaiMaiClient(
            app_key=creds["kuaimai_app_key"],
            app_secret=creds["kuaimai_app_secret"],
            access_token=creds["kuaimai_access_token"],
            refresh_token=creds["kuaimai_refresh_token"],
            org_id=org_id,
        )
        try:
            result = await client.request_with_retry(
                "erp.shop.list.query", {"pageNo": 1, "pageSize": 1}
            )
            return {
                "success": True,
                "message": "ERP 连接测试成功",
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"ERP 连接失败: {str(e)[:200]}",
            }
        finally:
            await client.close()
    except ValueError as e:
        return {"success": False, "message": str(e)}
    except AppException as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)


@router.get("/{org_id}/configs/wecom-status", summary="企微配置状态")
async def wecom_config_status(
    org_id: str,
    user_id: CurrentUserId,
    svc: OrgService = Depends(_get_org_service),
    resolver: OrgConfigResolver = Depends(_get_config_resolver),
):
    """返回企微各字段的有效配置来源（org/system/null）"""
    try:
        svc.require_role(org_id, user_id, ("owner", "admin"))
        org = svc.get_organization(org_id)
        keys = ["wecom_bot_id", "wecom_bot_secret", "wecom_agent_id", "wecom_agent_secret"]
        status: dict[str, dict] = {}
        # corp_id 在 organizations 表
        corp_id = org.get("wecom_corp_id")
        status["wecom_corp_id"] = {
            "configured": bool(corp_id),
            "source": "org" if corp_id else None,
        }
        # bot_id / bot_secret 可能在 org_configs 或 .env
        for k in keys:
            org_val = resolver._load_encrypted(org_id, k)
            if org_val:
                status[k] = {"configured": True, "source": "org"}
            elif resolver._get_default(k):
                status[k] = {"configured": True, "source": "system"}
            else:
                status[k] = {"configured": False, "source": None}
        return {"success": True, "data": status}
    except AppException as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)


@router.post("/{org_id}/configs/test-wecom", summary="测试企微机器人连接")
async def test_wecom_connection(
    org_id: str,
    user_id: CurrentUserId,
    svc: OrgService = Depends(_get_org_service),
    resolver: OrgConfigResolver = Depends(_get_config_resolver),
):
    """用企业配置的企微机器人凭证测试 WSS 连接"""
    try:
        svc.require_role(org_id, user_id, ("owner", "admin"))
        bot_id = resolver.get(org_id, "wecom_bot_id")
        bot_secret = resolver.get(org_id, "wecom_bot_secret")
        if not bot_id or not bot_secret:
            return {"success": False, "message": "企微机器人 Bot ID 或 Secret 未配置"}

        from services.wecom.ws_client import verify_bot_credentials
        ok, msg = await verify_bot_credentials(bot_id, bot_secret)
        return {"success": ok, "message": msg}
    except ValueError as e:
        return {"success": False, "message": str(e)}
    except AppException as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)
