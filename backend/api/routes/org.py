"""
企业管理路由

企业 CRUD、成员管理、邀请管理。
"""

from collections.abc import Mapping
import os
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger
from pydantic import BaseModel, Field

from api.deps import CurrentUserId, Database, ScopedDB
from core.exceptions import AppException
from services.configuration.bundles import SecretBundleResolver
from services.configuration.control_service import ConfigurationControlService
from services.configuration.envelope import LocalKEKProvider
from services.configuration.material_service import SecretMaterialService
from services.configuration.resolver import ConfigurationResolutionError
from services.org.config_resolver import OrgConfigResolver
from services.org.org_service import OrgService
from .org_lifecycle import router as lifecycle_router

router = APIRouter(prefix="/org", tags=["企业管理"])
router.include_router(lifecycle_router)


def _get_org_service(db: ScopedDB) -> OrgService:
    return OrgService(db)


def _get_config_resolver(db: ScopedDB) -> OrgConfigResolver:
    return OrgConfigResolver(db)


def _get_configuration_control(
    db: ScopedDB,
) -> ConfigurationControlService | None:
    if os.environ.get("CONFIG_CONTROL_PLANE_ENABLED") != "1":
        return None
    try:
        return ConfigurationControlService(
            db,
            SecretMaterialService(LocalKEKProvider.from_environment()),
        )
    except ValueError:
        logger.warning("Configuration control plane disabled: KEK is not configured")
        return None


def _get_secret_bundle_resolver(db: ScopedDB) -> SecretBundleResolver | None:
    if os.environ.get("CONFIG_CONTROL_PLANE_ENABLED") != "1":
        return None
    try:
        return SecretBundleResolver(
            db,
            SecretMaterialService(LocalKEKProvider.from_environment()),
        )
    except ValueError:
        logger.warning("Configuration bundle resolver disabled: KEK is not configured")
        return None


def _configuration_status(item: Mapping[str, object]) -> dict[str, object]:
    return {
        "config_key": item.get("key"),
        "configured": bool(item.get("configured")),
        "version": int(item.get("version") or 0),
        "source": item.get("source"),
        "updated_at": item.get("updated_at"),
    }


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
    # config_key/expected_version 是正式 Configuration Control Plane 合同。
    # key 保留旧 Web 管理台兼容路径，避免升级时旧客户端直接 422。
    config_key: Optional[str] = Field(None, min_length=1, max_length=100)
    key: Optional[str] = Field(None, min_length=1, max_length=100)
    value: Any
    expected_version: Optional[int] = Field(None, ge=0)


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

        # 权限模型 V1：自动初始化职位/角色/默认部门 + 把 owner 设为 boss
        # 设计文档: docs/document/TECH_组织架构与权限模型.md §四
        try:
            from services.permissions.initialization import initialize_organization
            await initialize_organization(db, org["id"], owner_id)
        except Exception as init_err:
            # 初始化失败不应该阻塞创建（但要明确记录，运维介入）
            logger.error(
                f"initialize_organization failed | org={org['id']} | error={init_err}"
            )

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
    control: ConfigurationControlService | None = Depends(_get_configuration_control),
    resolver: OrgConfigResolver = Depends(_get_config_resolver),
):
    """列出正式配置状态，绝不返回配置值或 Secret 材料。"""
    try:
        svc.require_role(org_id, user_id, ("owner", "admin"))
        if control is not None:
            statuses = control.list_organization_status(org_id=org_id)
            return {
                "success": True,
                "data": [_configuration_status(item) for item in statuses],
            }
        return {"success": True, "data": resolver.list_keys(org_id)}
    except AppException as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)


@router.put("/{org_id}/configs", summary="设置企业配置")
async def set_org_config(
    org_id: str,
    body: SetConfigRequest,
    user_id: CurrentUserId,
    svc: OrgService = Depends(_get_org_service),
    control: ConfigurationControlService | None = Depends(_get_configuration_control),
    resolver: OrgConfigResolver = Depends(_get_config_resolver),
):
    """正式配置走版本 CAS；旧 key/value 客户端暂走兼容存储。"""
    try:
        svc.require_role(org_id, user_id, ("owner", "admin"))
        if body.config_key is not None:
            if control is None:
                raise HTTPException(status_code=503, detail="正式配置控制面尚未配置 KEK")
            if body.expected_version is None:
                raise HTTPException(status_code=422, detail="expected_version 必填")
            result = control.set_organization(
                org_id=org_id,
                key=body.config_key,
                value=body.value,
                expected_version=body.expected_version,
            )
            return {"success": True, "data": _configuration_status(result)}
        if body.key is None:
            raise HTTPException(status_code=422, detail="配置键名必填")
        if not isinstance(body.value, str) or not body.value:
            raise HTTPException(status_code=422, detail="配置值不能为空")
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
    expected_version: Optional[int] = Query(None, ge=0),
    svc: OrgService = Depends(_get_org_service),
    control: ConfigurationControlService = Depends(_get_configuration_control),
    resolver: OrgConfigResolver = Depends(_get_config_resolver),
):
    """正式配置按版本 CAS 删除；无版本时保留旧删除路径。"""
    try:
        svc.require_role(org_id, user_id, ("owner", "admin"))
        if expected_version is not None:
            result = control.delete_organization(
                org_id=org_id,
                key=config_key,
                expected_version=expected_version,
            )
            return {"success": True, "data": _configuration_status(result)}
        resolver.delete(org_id, config_key)
        return {"success": True, "message": f"配置 {config_key} 已删除"}
    except AppException as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)


@router.post("/{org_id}/configs/test-erp", summary="测试 ERP 连接")
async def test_erp_connection(
    org_id: str,
    user_id: CurrentUserId,
    svc: OrgService = Depends(_get_org_service),
    bundle_resolver: SecretBundleResolver | None = Depends(_get_secret_bundle_resolver),
    control: ConfigurationControlService | None = Depends(_get_configuration_control),
    resolver: OrgConfigResolver = Depends(_get_config_resolver),
):
    """使用正式 erp.runtime Bundle 测试连接，并按版本 CAS 回写刷新令牌。"""
    try:
        svc.require_role(org_id, user_id, ("owner", "admin"))
        if bundle_resolver is None or control is None:
            creds = resolver.get_erp_credentials(org_id)
            import asyncio as _asyncio
            async def _persist_legacy(oid: str, access: str, refresh: str) -> None:
                await _asyncio.to_thread(resolver.update_erp_token, oid, access, refresh)
            from services.kuaimai.client import KuaiMaiClient
            client = KuaiMaiClient(
                app_key=creds["kuaimai_app_key"],
                app_secret=creds["kuaimai_app_secret"],
                access_token=creds["kuaimai_access_token"],
                refresh_token=creds["kuaimai_refresh_token"],
                org_id=org_id,
                token_persister=_persist_legacy,
            )
            try:
                await client.load_cached_token()
                await client.request_with_retry(
                    "erp.shop.list.query", {"pageNo": 1, "pageSize": 1},
                )
                return {"success": True, "message": "ERP 连接测试成功"}
            except Exception:
                return {"success": False, "message": "ERP 连接失败，请检查凭证或稍后重试"}
            finally:
                await client.close()
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
                value={"access_token": access, "refresh_token": refresh},
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
            await client.load_cached_token()
            await client.request_with_retry(
                "erp.shop.list.query", {"pageNo": 1, "pageSize": 1}
            )
            return {"success": True, "message": "ERP 连接测试成功"}
        except Exception:
            return {"success": False, "message": "ERP 连接失败，请检查凭证或稍后重试"}
        finally:
            await client.close()
    except (ConfigurationResolutionError, KeyError, ValueError):
        return {"success": False, "message": "ERP 配置不完整或不可用"}
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
    bundle_resolver: SecretBundleResolver | None = Depends(_get_secret_bundle_resolver),
    resolver: OrgConfigResolver = Depends(_get_config_resolver),
):
    """使用正式 wecom.bot Bundle 测试 WSS 连接。"""
    try:
        svc.require_role(org_id, user_id, ("owner", "admin"))
        if bundle_resolver is None:
            bot_id = resolver.get(org_id, "wecom_bot_id")
            bot_secret = resolver.get(org_id, "wecom_bot_secret")
            if not bot_id or not bot_secret:
                return {"success": False, "message": "企微机器人 Bot ID 或 Secret 未配置"}
            from services.wecom.ws_client import verify_bot_credentials
            ok, msg = await verify_bot_credentials(bot_id, bot_secret)
            return {"success": ok, "message": msg}
        bundle = bundle_resolver.wecom_bot_admin_test()
        credentials = bundle.values.get("wecom.bot_credentials")
        if not isinstance(credentials, Mapping):
            raise ConfigurationResolutionError("CONFIG_BUNDLE_INCOMPLETE")

        from services.wecom.ws_client import verify_bot_credentials
        ok, _ = await verify_bot_credentials(
            str(credentials["bot_id"]), str(credentials["bot_secret"]),
        )
        return {"success": ok, "message": "企微连接测试成功" if ok else "企微连接测试失败"}
    except (ConfigurationResolutionError, KeyError, ValueError):
        return {"success": False, "message": "企微配置不完整或不可用"}
    except AppException as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)
