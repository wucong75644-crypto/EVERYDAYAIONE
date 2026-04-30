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
    ConflictError,
    ExternalServiceError,
    PermissionDeniedError,
    ValidationError,
)
from core.redis import get_redis
from core.security import create_token_pair
from services.wecom.access_token_manager import get_access_token
from services.wecom_account_merge import (
    merge_users as _merge_users,
    _add_login_method,
)

# 企微 OAuth API
GETUSERINFO_URL = "https://qyapi.weixin.qq.com/cgi-bin/auth/getuserinfo"
QR_LOGIN_BASE_URL = "https://login.work.weixin.qq.com/wwlogin/sso/login"

# Redis key 前缀 + TTL
OAUTH_STATE_PREFIX = "wecom:oauth:state:"
OAUTH_STATE_TTL = 300  # 5 分钟


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
    # 登录 / 创建用户
    # ----------------------------------------------------------------

    async def login_or_create(
        self,
        wecom_userid: str,
        nickname: Optional[str] = None,
        org_id: Optional[str] = None,
        corp_id: Optional[str] = None,
    ) -> dict:
        """
        企微用户登录或自动创建账号。

        查找 wecom_user_mappings 中的映射：
        - 找到 → 直接登录（生成 JWT）+ 返回企业信息
        - 未找到 → 创建用户 + 映射 + 加入企业 → 登录

        Args:
            wecom_userid: 企微用户 ID
            nickname: 企微昵称（可选）
            org_id: 企业 ID（per-org 登录时传入）
            corp_id: 企微 corp_id（per-org 登录时传入）

        Returns:
            {"token": {...}, "user": {...}, "org": {...}|None}
        """
        if not corp_id:
            corp_id = self.settings.wecom_corp_id

        # 查找已有映射
        mapping = (
            self.db.table("wecom_user_mappings")
            .select("user_id")
            .eq("wecom_userid", wecom_userid)
            .eq("corp_id", corp_id)
            .limit(1)
            .execute()
        )

        if mapping.data:
            user_id = mapping.data[0]["user_id"]
            result = await self._login_existing_user(user_id, wecom_userid)
            # 确保已有用户在目标企业的 org_members 中
            if org_id:
                self._ensure_org_member(user_id, org_id)
        else:
            result = await self._create_and_login(wecom_userid, corp_id, nickname, org_id)

        # 附加企业信息
        user_id = result["user"]["id"]
        result["org"] = self._find_user_org(user_id, org_id)
        return result

    def _ensure_org_member(self, user_id: str, org_id: str) -> None:
        """确保用户在 org_members 中，不在则自动加入"""
        existing = (
            self.db.table("org_members")
            .select("user_id")
            .eq("user_id", user_id)
            .eq("org_id", org_id)
            .maybe_single()
            .execute()
        )
        if existing and existing.data:
            return
        try:
            self.db.table("org_members").insert({
                "org_id": org_id, "user_id": user_id,
                "role": "member", "status": "active",
            }).execute()
            logger.info(f"OAuth: auto added org member | user_id={user_id} | org_id={org_id}")
        except Exception as e:
            logger.warning(f"OAuth: ensure org member failed | user_id={user_id} | org_id={org_id} | error={e}")

    def _find_user_org(self, user_id: str, preferred_org_id: Optional[str] = None) -> Optional[dict]:
        """查用户所属企业，优先返回指定企业"""
        query = (
            self.db.table("org_members")
            .select("org_id, role")
            .eq("user_id", user_id)
            .eq("status", "active")
        )
        if preferred_org_id:
            query = query.eq("org_id", preferred_org_id)
        result = query.limit(1).execute()
        if not result.data:
            return None
        row = result.data[0]
        org_id = row["org_id"]
        # 查企业名称和状态
        org_result = (
            self.db.table("organizations")
            .select("id, name, status")
            .eq("id", org_id)
            .maybe_single()
            .execute()
        )
        org = org_result.data if org_result else None
        if not org or org.get("status") != "active":
            return None
        return {"org_id": org["id"], "name": org["name"], "role": row["role"]}

    async def _login_existing_user(self, user_id: str, wecom_userid: str) -> dict:
        """已有映射的用户直接登录"""
        result = (
            self.db.table("users")
            .select("*")
            .eq("id", user_id)
            .single()
            .execute()
        )

        if not result.data:
            logger.error(f"Wecom OAuth: mapped user not found | user_id={user_id}")
            raise ValidationError("用户账号异常，请联系管理员")

        user = result.data
        if user["status"] != "active":
            raise PermissionDeniedError("账号已被禁用")

        # 更新最后登录时间
        self.db.table("users").update({
            "last_login_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", user_id).execute()

        logger.info(f"Wecom OAuth login | user_id={user_id} | wecom_userid={wecom_userid}")

        token = self._create_token_response(user_id)
        return {"token": token, "user": self._format_user_response(user)}

    async def _create_and_login(
        self,
        wecom_userid: str,
        corp_id: str,
        nickname: Optional[str],
        org_id: Optional[str] = None,
    ) -> dict:
        """创建新用户 + 映射 + 加入企业 + 登录"""
        display_name = nickname or f"企微用户_{wecom_userid[:8]}"

        # 创建系统用户
        user_result = (
            self.db.table("users")
            .insert({
                "nickname": display_name,
                "login_methods": ["wecom"],
                "created_by": "wecom",
                "role": "user",
                "credits": 100,
                "status": "active",
            })
            .execute()
        )

        if not user_result.data:
            raise RuntimeError(f"创建用户失败 | wecom_userid={wecom_userid}")

        user = user_result.data[0]
        user_id = user["id"]

        # 记录注册积分
        self.db.table("credits_history").insert({
            "user_id": user_id,
            "change_amount": 100,
            "balance_after": 100,
            "change_type": "register_gift",
            "description": "企业微信扫码登录注册赠送积分",
        }).execute()

        # 创建映射
        mapping_data = {
            "wecom_userid": wecom_userid,
            "corp_id": corp_id,
            "user_id": user_id,
            "channel": "oauth",
            "wecom_nickname": display_name,
        }
        if org_id:
            mapping_data["org_id"] = org_id
        self.db.table("wecom_user_mappings").insert(mapping_data).execute()

        # 自动加入企业成员
        if org_id:
            try:
                self.db.table("org_members").insert({
                    "org_id": org_id,
                    "user_id": user_id,
                    "role": "member",
                    "status": "active",
                }).execute()
            except Exception as e:
                logger.error(
                    f"OAuth auto add org member failed | org_id={org_id} | "
                    f"user_id={user_id} | error={e}"
                )

        logger.info(
            f"Wecom OAuth: new user created | user_id={user_id} | "
            f"wecom_userid={wecom_userid}"
        )

        token = self._create_token_response(user_id)
        return {"token": token, "user": self._format_user_response(user)}

    # ----------------------------------------------------------------
    # 账号绑定
    # ----------------------------------------------------------------

    async def bind_account(
        self,
        user_id: str,
        wecom_userid: str,
        nickname: Optional[str] = None,
    ) -> dict:
        """
        将企微账号绑定到已有系统用户。

        场景：
        - wecom_userid 未映射 → 直接创建映射
        - wecom_userid 已映射到同一用户 → 已绑定，直接成功
        - wecom_userid 已映射到不同用户 → 执行账号合并

        Returns:
            {"token": {...}, "user": {...}, "merged": bool}
        """
        corp_id = self.settings.wecom_corp_id
        display_name = nickname or f"企微用户_{wecom_userid[:8]}"

        # 检查当前用户是否已绑定其他企微账号
        existing_bind = (
            self.db.table("wecom_user_mappings")
            .select("wecom_userid")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        if existing_bind.data:
            bound_wecom = existing_bind.data[0]["wecom_userid"]
            if bound_wecom == wecom_userid:
                # 已绑定同一企微账号
                return await self._login_existing_user(user_id, wecom_userid)
            raise ConflictError("该账号已绑定其他企微用户，请先解绑")

        # 查找 wecom_userid 是否已映射到其他用户
        mapping = (
            self.db.table("wecom_user_mappings")
            .select("user_id")
            .eq("wecom_userid", wecom_userid)
            .eq("corp_id", corp_id)
            .limit(1)
            .execute()
        )

        if mapping.data:
            other_user_id = mapping.data[0]["user_id"]
            if other_user_id == user_id:
                return await self._login_existing_user(user_id, wecom_userid)

            # 企微账号已映射到其他用户 → 合并
            await _merge_users(
                db=self.db,
                keep_user_id=user_id,
                remove_user_id=other_user_id,
                wecom_userid=wecom_userid,
                corp_id=corp_id,
                nickname=display_name,
            )
            result = await self._login_existing_user(user_id, wecom_userid)
            result["merged"] = True
            return result

        # 未映射 → 直接创建绑定
        self.db.table("wecom_user_mappings").insert({
            "wecom_userid": wecom_userid,
            "corp_id": corp_id,
            "user_id": user_id,
            "channel": "oauth",
            "wecom_nickname": display_name,
        }).execute()

        # 更新 login_methods
        _add_login_method(self.db, user_id, "wecom")

        logger.info(f"Wecom bind success | user_id={user_id} | wecom_userid={wecom_userid}")
        return await self._login_existing_user(user_id, wecom_userid)

    async def unbind_account(self, user_id: str) -> dict:
        """
        解绑企微账号。

        校验：不能是唯一登录方式（仅 wecom 无手机号时拒绝）。

        Returns:
            {"success": True, "message": "企微账号已解绑"}
        """
        # 检查是否有绑定
        mapping = (
            self.db.table("wecom_user_mappings")
            .select("id, wecom_userid")
            .eq("user_id", user_id)
            .execute()
        )
        if not mapping.data:
            raise ValidationError("当前账号未绑定企微")

        # 检查是否为唯一登录方式
        user = (
            self.db.table("users").select("phone, login_methods")
            .eq("id", user_id).single().execute()
        )
        if user.data:
            login_methods = user.data.get("login_methods") or []
            has_phone = bool(user.data.get("phone"))
            if not has_phone and login_methods == ["wecom"]:
                raise ValidationError("该账号仅通过企微创建，解绑后将无法登录，请先绑定手机号")

        # 删除映射
        self.db.table("wecom_user_mappings").delete().eq(
            "user_id", user_id
        ).execute()

        # 从 login_methods 移除 wecom
        if user.data:
            login_methods = user.data.get("login_methods") or []
            new_methods = [m for m in login_methods if m != "wecom"]
            if not new_methods:
                new_methods = ["phone"]
            self.db.table("users").update(
                {"login_methods": new_methods}
            ).eq("id", user_id).execute()

        logger.info(f"Wecom unbound | user_id={user_id}")
        return {"success": True, "message": "企微账号已解绑"}

    async def get_binding_status(self, user_id: str) -> dict:
        """
        查询企微绑定状态。

        Returns:
            {"bound": bool, "wecom_nickname": str|None, "bound_at": str|None}
        """
        mapping = (
            self.db.table("wecom_user_mappings")
            .select("wecom_nickname, bound_at")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )

        if not mapping.data:
            return {"bound": False, "wecom_nickname": None, "bound_at": None}

        row = mapping.data[0]
        return {
            "bound": True,
            "wecom_nickname": row.get("wecom_nickname"),
            "bound_at": row.get("bound_at"),
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

    # ----------------------------------------------------------------
    # 辅助方法（复用 auth_service 的 pattern）
    # ----------------------------------------------------------------

    def _create_token_response(self, user_id: str) -> dict:
        """创建双 token 响应（委托 security.create_token_pair）"""
        return create_token_pair(self.db, user_id)

    def _format_user_response(self, user: dict) -> dict:
        """格式化用户响应（与 auth_service 保持一致）"""
        phone = user.get("phone")
        masked_phone = None
        if phone and len(phone) >= 7:
            masked_phone = f"{phone[:3]}****{phone[-4:]}"

        login_methods = user.get("login_methods") or []
        wecom_bound = "wecom" in login_methods

        return {
            "id": user["id"],
            "nickname": user["nickname"],
            "avatar_url": user.get("avatar_url"),
            "phone": masked_phone,
            "role": user["role"],
            "credits": user["credits"],
            "created_at": user["created_at"],
            "wecom_bound": wecom_bound,
        }
