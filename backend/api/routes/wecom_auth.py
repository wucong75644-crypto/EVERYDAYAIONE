"""
企微 OAuth 扫码登录路由

提供企微扫码登录、OAuth 回调、账号绑定/解绑接口。
"""

import base64
import json
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from loguru import logger

from api.deps import CurrentUserId, Database, OptionalUserId
from core.config import get_settings
from services.wecom_oauth_service import WecomOAuthService

router = APIRouter(prefix="/auth/wecom", tags=["企微登录"])


def _get_oauth_service(db: Database) -> WecomOAuthService:
    """获取 OAuth 服务实例"""
    return WecomOAuthService(db)


@router.get("/qr-url", summary="获取企微扫码登录 URL")
async def get_qr_url(
    user_id: OptionalUserId,
    db: Database,
    org_id: str = Query(default=None, description="企业 ID（per-org 扫码登录）"),
    svc: WecomOAuthService = Depends(_get_oauth_service),
):
    """
    生成企微扫码登录 URL + state token。

    - org_id 不为空：使用该企业自己的 corp_id + agent_id 生成二维码
    - org_id 为空 + 已登录：绑定流程
    - org_id 为空 + 未登录：返回 503（需要指定企业）
    """
    settings = get_settings()

    if not settings.wecom_oauth_redirect_uri:
        raise HTTPException(status_code=503, detail="OAuth 回调地址未配置")

    corp_id = None
    agent_id = None

    if org_id:
        # per-org 模式：从 org_configs 读取该企业的自建应用凭证
        from services.org.config_resolver import OrgConfigResolver
        resolver = OrgConfigResolver(db)
        org = db.table("organizations").select("wecom_corp_id, status").eq("id", org_id).maybe_single().execute()
        if not org or not org.data:
            raise HTTPException(status_code=404, detail="企业不存在")
        if org.data.get("status") != "active":
            raise HTTPException(status_code=400, detail="企业已停用")
        corp_id = org.data.get("wecom_corp_id")
        agent_id = resolver.get(org_id, "wecom_agent_id")
        if not corp_id or not agent_id:
            raise HTTPException(status_code=400, detail="该企业未配置企微自建应用（Corp ID 或 Agent ID 缺失）")
    else:
        # 兼容旧逻辑：全局配置（bind 模式或无 org 参数）
        if not user_id:
            raise HTTPException(status_code=400, detail="请通过企业专属链接登录")
        corp_id = settings.wecom_corp_id
        agent_id = str(settings.wecom_agent_id) if settings.wecom_agent_id else None
        if not corp_id or not agent_id:
            raise HTTPException(status_code=503, detail="企微配置缺失")

    state_type = "bind" if (user_id and not org_id) else "login"
    try:
        state = await svc.generate_state(state_type, user_id=user_id, org_id=org_id)
    except Exception as e:
        logger.warning(f"Generate OAuth state failed | error={e}")
        raise HTTPException(status_code=503, detail="服务暂时不可用，请稍后重试")

    return svc.build_qr_url(state, corp_id=corp_id, agent_id=agent_id)


@router.get("/callback", summary="企微 OAuth 回调")
async def oauth_callback(
    code: str = Query(..., description="企微授权码"),
    state: str = Query(..., description="防 CSRF state token"),
    svc: WecomOAuthService = Depends(_get_oauth_service),
):
    """
    企微扫码后的 OAuth 回调端点。

    1. 校验 state（Redis 原子消费）
    2. 用 code 换取企微 userid
    3. 登录或创建用户
    4. 302 重定向到前端回调页（带 token 或 error）
    """
    settings = get_settings()
    frontend_url = settings.frontend_url or ""

    try:
        # 1. 校验 state
        state_data = await svc.validate_state(state)
        state_org_id = state_data.get("org_id")

        # 2. 获取该企业的凭证（per-org 模式）
        org_corp_id = None
        org_agent_secret = None
        if state_org_id:
            from services.org.config_resolver import OrgConfigResolver
            resolver = OrgConfigResolver(svc.db)
            org = svc.db.table("organizations").select("wecom_corp_id").eq("id", state_org_id).maybe_single().execute()
            org_corp_id = (org.data or {}).get("wecom_corp_id") if org else None
            org_agent_secret = resolver.get(state_org_id, "wecom_agent_secret")

        # 3. 用 code 换取 userid
        wecom_info = await svc.exchange_code(
            code,
            org_id=state_org_id,
            corp_id=org_corp_id,
            agent_secret=org_agent_secret,
        )
        wecom_userid = wecom_info["userid"]

        # 4. 根据 state 类型处理
        if state_data["type"] == "bind" and state_data.get("user_id"):
            result = await svc.bind_account(
                user_id=state_data["user_id"],
                wecom_userid=wecom_userid,
            )
        else:
            result = await svc.login_or_create(
                wecom_userid,
                org_id=state_org_id,
                corp_id=org_corp_id,
            )

        # 5. 成功 → 重定向到前端（带 token + user + org）
        token_json = json.dumps(result["token"])
        user_json = json.dumps(result["user"])
        token_b64 = base64.b64encode(token_json.encode()).decode()
        user_b64 = base64.b64encode(user_json.encode()).decode()

        redirect_url = f"{frontend_url}/auth/wecom/callback?token={token_b64}&user={user_b64}"

        # 附加企业信息
        org_info = result.get("org")
        if org_info:
            org_b64 = base64.b64encode(json.dumps(org_info).encode()).decode()
            redirect_url += f"&org={org_b64}"

        return RedirectResponse(url=redirect_url, status_code=302)

    except ValueError as e:
        # 业务错误 → 重定向到前端（带 error）
        error_msg = str(e)
        error_code = _classify_error(error_msg)
        params = urlencode({"error": error_code, "message": error_msg})
        redirect_url = f"{frontend_url}/auth/wecom/callback?{params}"
        logger.warning(f"Wecom OAuth callback failed | error={error_msg}")
        return RedirectResponse(url=redirect_url, status_code=302)

    except Exception as e:
        logger.exception(f"Wecom OAuth callback unexpected error | error={e}")
        params = urlencode({"error": "api_error", "message": "登录失败，请重试"})
        redirect_url = f"{frontend_url}/auth/wecom/callback?{params}"
        return RedirectResponse(url=redirect_url, status_code=302)


@router.delete("/binding", summary="解绑企微账号")
async def unbind_wecom(
    user_id: CurrentUserId,
    svc: WecomOAuthService = Depends(_get_oauth_service),
):
    """
    解绑当前用户的企微账号。

    - 仅企微创建且无手机号的用户不允许解绑（解绑后无法登录）
    """
    try:
        return await svc.unbind_account(user_id)
    except ValueError as e:
        error_msg = str(e)
        status = 404 if "未绑定" in error_msg else 400
        raise HTTPException(status_code=status, detail=error_msg)


@router.get("/binding-status", summary="查询企微绑定状态")
async def get_binding_status(
    user_id: CurrentUserId,
    svc: WecomOAuthService = Depends(_get_oauth_service),
):
    """查询当前用户的企微绑定状态"""
    return await svc.get_binding_status(user_id)


@router.post("/sync-employees", summary="同步企微通讯录")
async def sync_employees(
    user_id: CurrentUserId,
    db: Database,
):
    """
    手动触发企微通讯录同步（部门 + 员工）。

    需要管理员权限，且自建应用需有通讯录读取权限。
    """
    # 权限校验：仅管理员可触发
    user = db.table("users").select("role").eq("id", user_id).single().execute()
    if not user.data or user.data["role"] not in ("admin", "super_admin"):
        raise HTTPException(status_code=403, detail="仅管理员可执行同步")

    from services.wecom.employee_sync_service import EmployeeSyncService
    svc = EmployeeSyncService(db)
    result = await svc.sync_all()

    if result["errors"]:
        logger.warning(f"Employee sync had errors | errors={result['errors']}")

    return {
        "success": len(result["errors"]) == 0,
        "departments": result["departments"],
        "employees": result["employees"],
        "departed": result["departed"],
        "errors": result["errors"],
    }


def _classify_error(error_msg: str) -> str:
    """根据错误消息分类错误码"""
    if "过期" in error_msg or "无效" in error_msg:
        return "state_invalid"
    if "企业成员" in error_msg:
        return "not_member"
    if "禁用" in error_msg:
        return "user_disabled"
    if "已绑定" in error_msg:
        return "already_bound"
    return "api_error"
