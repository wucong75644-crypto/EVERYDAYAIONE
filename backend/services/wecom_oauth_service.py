"""
企微 OAuth 扫码登录服务

处理企微 OAuth 全流程：state 管理、code 换 userid、登录/创建用户、账号绑定与合并。
"""

import json
import secrets
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import quote

import httpx
from loguru import logger


from core.config import get_settings
from core.exceptions import (
    ExternalServiceError,
    PermissionDeniedError,
    ValidationError,
)
from core.redis import get_redis
from services.wecom.access_token_manager import get_access_token

# 企微 OAuth API
GETUSERINFO_URL = "https://qyapi.weixin.qq.com/cgi-bin/auth/getuserinfo"
QR_LOGIN_BASE_URL = "https://login.work.weixin.qq.com/wwlogin/sso/login"

# Redis key 前缀 + TTL
OAUTH_STATE_PREFIX = "wecom:oauth:state:"
OAUTH_STATE_TTL = 300  # 5 分钟
OAUTH_HANDOFF_PREFIX = "wecom:oauth:handoff:"
OAUTH_HANDOFF_TTL = 60


class WecomOAuthService:
    """企微 OAuth 扫码登录服务"""

    def __init__(self, db):
        self.db = db
        self.settings = get_settings()

    # ----------------------------------------------------------------
    # State 管理
    # ----------------------------------------------------------------

    async def generate_state(
        self,
        state_type: str = "login",
        user_id: Optional[str] = None,
        org_id: Optional[str] = None,
    ) -> str:
        """
        生成 OAuth state token 并存入 Redis。

        Args:
            state_type: "login"（扫码登录）或 "bind"（账号绑定）
            user_id: bind 模式下的当前用户 ID
            org_id: 企业 ID（per-org 扫码登录时必传）

        Returns:
            state token 字符串
        """
        state = secrets.token_urlsafe(32)
        value = json.dumps({
            "type": state_type,
            "user_id": user_id,
            "org_id": org_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })

        redis = await get_redis()
        if not redis:
            raise RuntimeError("Redis 不可用，无法生成 OAuth state")

        await redis.set(f"{OAUTH_STATE_PREFIX}{state}", value, ex=OAUTH_STATE_TTL)
        logger.debug(f"OAuth state generated | type={state_type} | user_id={user_id} | org_id={org_id}")
        return state

    async def validate_state(self, state: str) -> dict:
        """
        校验并消费 state token（原子操作，防重放）。

        Args:
            state: 待校验的 state token

        Returns:
            {"type": "login"|"bind", "user_id": str|None}

        Raises:
            ValueError: state 无效或已过期
        """
        redis = await get_redis()
        if not redis:
            raise ExternalServiceError("Redis", "登录服务暂时不可用")

        key = f"{OAUTH_STATE_PREFIX}{state}"
        value = await redis.getdel(key)
        if not value:
            raise ValidationError("登录链接已失效，请重新扫码")

        return json.loads(value)

    async def create_handoff(self, payload: dict) -> str:
        """Store a short-lived one-time login result and return its opaque code."""
        redis = await get_redis()
        if not redis:
            raise ExternalServiceError("Redis", "登录服务暂时不可用")
        code = secrets.token_urlsafe(32)
        await redis.set(
            f"{OAUTH_HANDOFF_PREFIX}{code}",
            json.dumps(payload, separators=(",", ":"), ensure_ascii=False),
            ex=OAUTH_HANDOFF_TTL,
        )
        return code

    async def consume_handoff(self, code: str) -> dict:
        """Atomically consume one login result; replay and expiry fail closed."""
        redis = await get_redis()
        if not redis:
            raise ExternalServiceError("Redis", "登录服务暂时不可用")
        value = await redis.getdel(f"{OAUTH_HANDOFF_PREFIX}{code}")
        if not value:
            raise ValidationError("登录交接码已失效，请重新扫码")
        try:
            payload = json.loads(value)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValidationError("登录交接数据无效，请重新扫码") from exc
        if not isinstance(payload, dict) or not payload.get("token") or not payload.get("user"):
            raise ValidationError("登录交接数据无效，请重新扫码")
        return payload

    # ----------------------------------------------------------------
    # 企微 API 调用
    # ----------------------------------------------------------------

    async def exchange_code(
        self,
        code: str,
        org_id: Optional[str] = None,
        corp_id: Optional[str] = None,
        agent_secret: Optional[str] = None,
    ) -> dict:
        """
        用 OAuth code 换取企微 userid。

        Args:
            code: 企微授权码
            org_id: 企业 ID（per-org 模式）
            corp_id: 企微 corp_id（per-org 模式）
            agent_secret: 自建应用 secret（per-org 模式）

        Returns:
            {"userid": str, "user_ticket": str|None}

        Raises:
            ValueError: 非企业成员或 API 调用失败
        """
        if org_id and corp_id and agent_secret:
            access_token = await get_access_token(org_id, corp_id, agent_secret)
        else:
            # 兼容旧逻辑（bind 模式暂不走 per-org）
            s = self.settings
            access_token = await get_access_token(
                org_id or "system", s.wecom_corp_id or "", s.wecom_agent_secret or "",
            )
        if not access_token:
            raise ExternalServiceError("企微", "企业微信服务暂时不可用")

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    GETUSERINFO_URL,
                    params={"access_token": access_token, "code": code},
                )
                data = resp.json()
        except Exception as e:
            logger.error(f"Wecom OAuth: getuserinfo request failed | error={e}")
            raise ExternalServiceError("企微", "企业微信服务暂时不可用")

        errcode = data.get("errcode", 0)
        if errcode != 0:
            errmsg = data.get("errmsg", "unknown")
            logger.warning(f"Wecom OAuth: API error | errcode={errcode} | errmsg={errmsg}")
            raise ExternalServiceError("企微", "企业微信授权失败，请重试")

        # 非企业成员返回 openid 而非 userid
        userid = data.get("userid")
        if not userid:
            logger.warning(f"Wecom OAuth: non-member scan | openid={data.get('openid')}")
            raise PermissionDeniedError("仅限企业成员使用扫码登录")

        return {
            "userid": userid,
            "user_ticket": data.get("user_ticket"),
        }

    # ----------------------------------------------------------------
    # QR URL 生成
    # ----------------------------------------------------------------

    def build_qr_url(
        self,
        state: str,
        corp_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> dict:
        """
        构建企微扫码登录 URL 和拆分参数。

        Args:
            state: OAuth state token
            corp_id: 企微 corp_id（per-org 模式传入，否则用系统默认）
            agent_id: 自建应用 agent_id（per-org 模式传入）

        Returns:
            包含 qr_url 和拆分参数的字典
        """
        corp_id = corp_id or self.settings.wecom_corp_id
        agent_id = agent_id or str(self.settings.wecom_agent_id or "")
        redirect_uri = self.settings.wecom_oauth_redirect_uri

        qr_url = (
            f"{QR_LOGIN_BASE_URL}"
            f"?login_type=CorpApp"
            f"&appid={corp_id}"
            f"&agentid={agent_id}"
            f"&redirect_uri={quote(redirect_uri, safe='')}"
            f"&state={state}"
        )

        return {
            "qr_url": qr_url,
            "state": state,
            "appid": corp_id,
            "agentid": agent_id,
            "redirect_uri": redirect_uri,
        }
