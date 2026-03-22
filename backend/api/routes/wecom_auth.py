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
    svc: WecomOAuthService = Depends(_get_oauth_service),
):
    """
    生成企微扫码登录 URL + state token。

    - 未登录：登录流程（state.type = "login"）
    - 已登录：绑定流程（state.type = "bind"）

    返回完整 URL（用于全页跳转）和拆分参数（用于 JS SDK 嵌入）。
    """
    settings = get_settings()

    if not settings.wecom_corp_id or not settings.wecom_agent_id:
        raise HTTPException(status_code=503, detail="企微配置缺失")

    if not settings.wecom_oauth_redirect_uri:
        raise HTTPException(status_code=503, detail="OAuth 回调地址未配置")

    state_type = "bind" if user_id else "login"
    try:
        state = await svc.generate_state(state_type, user_id=user_id)
    except Exception as e:
        logger.warning(f"Generate OAuth state failed | error={e}")
        raise HTTPException(status_code=503, detail="服务暂时不可用，请稍后重试")

    return svc.build_qr_url(state)


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

        # 2. 用 code 换取 userid
        wecom_info = await svc.exchange_code(code)
        wecom_userid = wecom_info["userid"]

        # 3. 根据 state 类型处理
        if state_data["type"] == "bind" and state_data.get("user_id"):
            result = await svc.bind_account(
                user_id=state_data["user_id"],
                wecom_userid=wecom_userid,
            )
        else:
            result = await svc.login_or_create(wecom_userid)

        # 4. 成功 → 重定向到前端（带 token + user）
        token_json = json.dumps(result["token"])
        user_json = json.dumps(result["user"])
        token_b64 = base64.b64encode(token_json.encode()).decode()
        user_b64 = base64.b64encode(user_json.encode()).decode()

        redirect_url = f"{frontend_url}/auth/wecom/callback?token={token_b64}&user={user_b64}"
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
