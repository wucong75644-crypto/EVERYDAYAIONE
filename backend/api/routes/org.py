"""
企业管理路由

企业 CRUD、成员管理、邀请管理。
"""

from collections.abc import Mapping
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.deps import CurrentUserId, ScopedDB
from core.exceptions import AppException
from services.configuration.bundles import SecretBundleResolver
from services.configuration.control_service import ConfigurationControlService
from services.configuration.envelope import LocalKEKProvider
from services.configuration.material_service import SecretMaterialService
from services.configuration.resolver import ConfigurationResolutionError
from services.org.org_service import OrgService
from .org_public import router as public_router

router = APIRouter(prefix="/org", tags=["企业管理"])
router.include_router(public_router)


def _get_org_service(db: ScopedDB) -> OrgService:
    return OrgService(db)


def _get_configuration_control(
    db: ScopedDB,
) -> ConfigurationControlService:
    return ConfigurationControlService(
        db,
        SecretMaterialService(LocalKEKProvider.from_environment()),
    )


def _get_secret_bundle_resolver(db: ScopedDB) -> SecretBundleResolver:
    return SecretBundleResolver(
        db,
        SecretMaterialService(LocalKEKProvider.from_environment()),
    )


def _configuration_status(item: Mapping[str, object]) -> dict[str, object]:
    """Expose the stable, non-secret organization configuration contract."""
    return {
        "config_key": item.get("key"),
        "configured": bool(item.get("configured")),
        "version": int(item.get("version") or 0),
        "source": item.get("source"),
        "updated_at": item.get("updated_at"),
    }


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
    config_key: str = Field(..., min_length=1, max_length=100)
    value: object
    expected_version: int = Field(..., ge=0)


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
    svc: OrgService = Depends(_get_org_service),
):
    """仅超管可调用。创建企业并指定 owner（通过手机号查找）。"""
    try:
        owner_result = svc.search_user_by_phone(body.owner_phone)
        owner = owner_result.get("user") if owner_result.get("found") else None
        if not owner:
            raise HTTPException(status_code=404, detail=f"手机号 {body.owner_phone} 未注册")
        if owner.get("status") != "active":
            raise HTTPException(status_code=400, detail=f"该用户已被禁用，无法设为企业 owner")
        owner_id = owner["id"]
        org = svc.create_organization(body.name, owner_id)

        return {"success": True, "data": org}
    except AppException as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)


@router.get("/admin/all", summary="所有企业列表（超管）")
async def list_all_orgs(
    user_id: CurrentUserId,
    svc: OrgService = Depends(_get_org_service),
):
    """仅超管可调用。列出平台所有企业。"""
    try:
        return svc.list_all_organizations()
    except AppException as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)


@router.get("/admin/search-user", summary="搜索用户（超管）")
async def search_user(
    phone: str = Query(..., pattern=r"^1[3-9]\d{9}$", description="手机号"),
    user_id: CurrentUserId = None,
    svc: OrgService = Depends(_get_org_service),
):
    """超管通过手机号搜索用户（用于指定 owner / 添加成员）"""
    try:
        return svc.search_user_by_phone(phone)
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
    svc: OrgService = Depends(_get_org_service),
):
    """通过治理能力查询当前 Actor 的有效待接受邀请。"""
    return svc.list_pending_invitations()


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
    control: ConfigurationControlService = Depends(_get_configuration_control),
):
    """列出正式配置状态，绝不返回配置值或 Secret 材料。"""
    try:
        svc.require_role(org_id, user_id, ("owner", "admin"))
        statuses = control.list_organization_status(org_id=org_id)
        return {
            "success": True,
            "data": [_configuration_status(item) for item in statuses],
        }
    except AppException as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)


@router.put("/{org_id}/configs", summary="设置企业配置")
async def set_org_config(
    org_id: str,
    body: SetConfigRequest,
    user_id: CurrentUserId,
    svc: OrgService = Depends(_get_org_service),
    control: ConfigurationControlService = Depends(_get_configuration_control),
):
    """通过正式控制面以 CAS 写入企业配置。"""
    try:
        svc.require_role(org_id, user_id, ("owner", "admin"))
        result = control.set_organization(
            org_id=org_id,
            key=body.config_key,
            value=body.value,
            expected_version=body.expected_version,
        )
        return {"success": True, "data": _configuration_status(result)}
    except AppException as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)


@router.delete("/{org_id}/configs/{config_key}", summary="删除企业配置")
async def delete_org_config(
    org_id: str,
    config_key: str,
    user_id: CurrentUserId,
    expected_version: int = Query(..., ge=0),
    svc: OrgService = Depends(_get_org_service),
    control: ConfigurationControlService = Depends(_get_configuration_control),
):
    """通过正式控制面以 CAS 删除企业配置。"""
    try:
        svc.require_role(org_id, user_id, ("owner", "admin"))
        result = control.delete_organization(
            org_id=org_id,
            key=config_key,
            expected_version=expected_version,
        )
        return {"success": True, "data": _configuration_status(result)}
    except AppException as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)


@router.post("/{org_id}/configs/test-erp", summary="测试 ERP 连接")
async def test_erp_connection(
    org_id: str,
    user_id: CurrentUserId,
    svc: OrgService = Depends(_get_org_service),
    bundle_resolver: SecretBundleResolver = Depends(
        _get_secret_bundle_resolver
    ),
    control: ConfigurationControlService = Depends(_get_configuration_control),
):
    """使用正式 erp.runtime Bundle 测试连接。"""
    try:
        svc.require_role(org_id, user_id, ("owner", "admin"))
        bundle = bundle_resolver.erp_runtime()
        app = bundle.values.get("erp.app_credentials")
        token = bundle.values.get("erp.token_pair")
        if not isinstance(app, Mapping) or not isinstance(token, Mapping):
            raise ConfigurationResolutionError("CONFIG_BUNDLE_INCOMPLETE")

        import asyncio as _asyncio
        async def _persist(oid: str, access: str, refresh: str) -> None:
            await _asyncio.to_thread(
                control.set_organization,
                org_id=oid,
                key="erp.token_pair",
                value={
                    "access_token": access,
                    "refresh_token": refresh,
                },
                expected_version=bundle.versions["erp.token_pair"],
            )

        from services.kuaimai.client import KuaiMaiClient
        client = KuaiMaiClient(
            app_key=str(app["app_key"]),
            app_secret=str(app["app_secret"]),
            access_token=str(token["access_token"]),
            refresh_token=str(token["refresh_token"]),
            org_id=org_id,
            token_persister=_persist,
        )
        try:
            await client.load_cached_token()  # 从 Redis 拿最新热缓存
            await client.request_with_retry(
                "erp.shop.list.query", {"pageNo": 1, "pageSize": 1}
            )
            return {
                "success": True,
                "message": "ERP 连接测试成功",
            }
        except Exception:
            return {
                "success": False,
                "message": "ERP 连接失败，请检查凭证或稍后重试",
            }
        finally:
            await client.close()
    except (ConfigurationResolutionError, ValueError):
        return {"success": False, "message": "ERP 配置不完整或不可用"}
    except AppException as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)


@router.get("/{org_id}/configs/wecom-status", summary="企微配置状态")
async def wecom_config_status(
    org_id: str,
    user_id: CurrentUserId,
    svc: OrgService = Depends(_get_org_service),
    control: ConfigurationControlService = Depends(_get_configuration_control),
):
    """返回企微正式配置项的非秘密状态。"""
    try:
        svc.require_role(org_id, user_id, ("owner", "admin"))
        status = {
            str(item.get("key")): {
                "configured": bool(item.get("configured")),
                "source": item.get("source"),
                "version": int(item.get("version") or 0),
            }
            for item in control.list_organization_status(org_id=org_id)
            if str(item.get("key", "")).startswith("wecom.")
        }
        return {"success": True, "data": status}
    except AppException as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)


@router.post("/{org_id}/configs/test-wecom", summary="测试企微机器人连接")
async def test_wecom_connection(
    org_id: str,
    user_id: CurrentUserId,
    svc: OrgService = Depends(_get_org_service),
    bundle_resolver: SecretBundleResolver = Depends(
        _get_secret_bundle_resolver
    ),
):
    """使用正式 wecom.bot Bundle 测试 WSS 连接。"""
    try:
        svc.require_role(org_id, user_id, ("owner", "admin"))
        bundle = bundle_resolver.wecom_bot()
        credentials = bundle.values.get("wecom.bot_credentials")
        if not isinstance(credentials, Mapping):
            raise ConfigurationResolutionError("CONFIG_BUNDLE_INCOMPLETE")

        from services.wecom.ws_client import verify_bot_credentials
        ok, _ = await verify_bot_credentials(
            str(credentials["bot_id"]),
            str(credentials["bot_secret"]),
        )
        return {
            "success": ok,
            "message": "企微连接测试成功" if ok else "企微连接测试失败",
        }
    except (ConfigurationResolutionError, KeyError, ValueError):
        return {"success": False, "message": "企微配置不完整或不可用"}
    except AppException as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)
