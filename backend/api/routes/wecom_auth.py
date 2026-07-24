"""
企微 OAuth 扫码登录路由

提供企微扫码登录、OAuth 回调、账号绑定/解绑接口。
"""

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from loguru import logger
from pydantic import BaseModel, Field

from api.deps import CurrentUserId, Database, OptionalUserId
from core.config import get_settings
from core.exceptions import AppException
from services.wecom.oauth_identity_service import WecomOAuthIdentityService
from services.wecom_oauth_service import WecomOAuthService

router = APIRouter(prefix="/auth/wecom", tags=["企微登录"])


class OAuthHandoffRequest(BaseModel):
    code: str = Field(min_length=32, max_length=128)


def _get_oauth_service(db: Database) -> WecomOAuthService:
    """获取 OAuth 服务实例（登录路由为公开接口，使用无需登录的 Database）"""
    return WecomOAuthService(db)


@router.get("/qr-url", summary="获取企微扫码登录 URL")
async def get_qr_url(
    request: Request,
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
        identity = WecomOAuthIdentityService.for_login(
            db, org_id=org_id, request_id=_request_id(request),
        )
        config = identity.get_public_config()
        corp_id = config["corp_id"]
        agent_id = config["agent_id"]
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
    request: Request,
    code: str = Query(..., description="企微授权码"),
    state: str = Query(..., description="防 CSRF state token"),
    svc: WecomOAuthService = Depends(_get_oauth_service),
):
    """
    企微扫码后的 OAuth 回调端点。

    1. 校验 state（Redis 原子消费）
    2. 用 code 换取企微 userid
    3. 登录或创建用户
    4. 302 重定向到前端回调页（仅携带一次性交接码或错误）
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
        identity = None
        if state_org_id:
            identity = WecomOAuthIdentityService.for_login(
                svc.db,
                org_id=state_org_id,
                request_id=_request_id(request),
            )
            config = identity.get_exchange_config()
            org_corp_id = config["corp_id"]
            org_agent_secret = config["agent_secret"]

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
            actor_identity = WecomOAuthIdentityService.for_actor(
                svc.db,
                user_id=state_data["user_id"],
                request_id=_request_id(request),
            )
            result = actor_identity.bind_account(
                wecom_userid=wecom_userid,
                corp_id=settings.wecom_corp_id or "",
            )
        else:
            if identity is None or not org_corp_id:
                raise ValueError("请通过企业专属链接登录")
            result = identity.login_or_create(
                wecom_userid=wecom_userid,
                corp_id=org_corp_id,
            )

        # 5. 成功 → URL 仅携带一次性交接码，token/user 不进入日志和浏览器历史
        handoff = await svc.create_handoff(result)
        redirect_url = (
            f"{frontend_url}/auth/wecom/callback?"
            f"{urlencode({'handoff': handoff})}"
        )
        return RedirectResponse(url=redirect_url, status_code=302)

    except (AppException, ValueError) as e:
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


@router.post("/handoff", summary="消费企微 OAuth 一次性交接码")
async def consume_oauth_handoff(
    payload: OAuthHandoffRequest,
    svc: WecomOAuthService = Depends(_get_oauth_service),
):
    """原子消费登录结果；重复、过期或无效 code 均失败关闭。"""
    return await svc.consume_handoff(payload.code)


@router.delete("/binding", summary="解绑企微账号")
async def unbind_wecom(
    request: Request,
    user_id: CurrentUserId,
    svc: WecomOAuthService = Depends(_get_oauth_service),
):
    """
    解绑当前用户的企微账号。

    - 仅企微创建且无手机号的用户不允许解绑（解绑后无法登录）
    """
    try:
        identity = WecomOAuthIdentityService.for_actor(
            svc.db, user_id=user_id, request_id=_request_id(request),
        )
        return identity.unbind_account()
    except (AppException, ValueError) as e:
        error_msg = str(e)
        status = 404 if "未绑定" in error_msg else 400
        raise HTTPException(status_code=status, detail=error_msg)


@router.get("/binding-status", summary="查询企微绑定状态")
async def get_binding_status(
    request: Request,
    user_id: CurrentUserId,
    svc: WecomOAuthService = Depends(_get_oauth_service),
):
    """查询当前用户的企微绑定状态"""
    identity = WecomOAuthIdentityService.for_actor(
        svc.db, user_id=user_id, request_id=_request_id(request),
    )
    return identity.get_binding_status()


def _request_id(request: Request) -> str:
    return request.headers.get("X-Request-Id", "")


def _classify_error(error_msg: str) -> str:
    """根据错误消息分类错误码"""
    if "过期" in error_msg or "无效" in error_msg or "失效" in error_msg:
        return "state_invalid"
    if "企业成员" in error_msg:
        return "not_member"
    if "禁用" in error_msg:
        return "user_disabled"
    if "已绑定" in error_msg or "审核合并" in error_msg:
        return "already_bound"
    return "api_error"
