"""
认证服务

处理用户注册、登录、验证码等业务逻辑。
"""

from datetime import datetime, timezone
from typing import Optional

from loguru import logger


from core.config import get_settings
from core.exceptions import (
    AuthenticationError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from core.security import (
    create_token_pair,
    hash_password,
    hash_refresh_token,
    verify_password,
)
from services.sms_service import get_sms_service


class AuthService:
    """认证服务类"""

    def __init__(self, db):
        self.db = db
        self.settings = get_settings()

    async def register_by_phone(
        self,
        phone: str,
        code: str,
        nickname: Optional[str] = None,
        password: Optional[str] = None,
    ) -> dict:
        """
        手机号注册

        Args:
            phone: 手机号
            code: 验证码
            nickname: 昵称（可选）
            password: 密码（可选）

        Returns:
            包含 token 和用户信息的字典

        Raises:
            ConflictError: 手机号已注册
            ValidationError: 验证码错误
        """
        # 1. 验证验证码
        if not await self._verify_code(phone, code, "register"):
            raise ValidationError("验证码错误或已过期")

        # 2. 检查手机号是否已注册
        existing = self.db.table("users").select("id").eq("phone", phone).execute()
        if existing.data:
            raise ConflictError("该手机号已注册")

        # 3. 创建用户
        user_data = {
            "phone": phone,
            "nickname": nickname or f"用户{phone[-4:]}",
            "login_methods": ["phone"],
            "created_by": "phone",
            "role": "user",
            "credits": 100,
            "status": "active",
        }

        if password:
            user_data["password_hash"] = hash_password(password)

        result = self.db.table("users").insert(user_data).execute()

        if not result.data:
            logger.error(f"Failed to create user | phone={phone}")
            raise ValidationError("注册失败，请稍后重试")

        user = result.data[0]
        logger.info(f"User registered | user_id={user['id']} | phone={phone}")

        # 4. 记录积分赠送
        self.db.table("credits_history").insert({
            "user_id": user["id"],
            "change_amount": 100,
            "balance_after": 100,
            "change_type": "register_gift",
            "description": "新用户注册赠送积分",
        }).execute()

        # 5. 生成 token
        token = self._create_token_response(user["id"])

        return {
            "token": token,
            "user": self._format_user_response(user),
        }

    async def login_by_phone(self, phone: str, code: str) -> dict:
        """
        手机号验证码登录

        Args:
            phone: 手机号
            code: 验证码

        Returns:
            包含 token 和用户信息的字典

        Raises:
            NotFoundError: 用户不存在
            ValidationError: 验证码错误
        """
        # 1. 验证验证码
        if not await self._verify_code(phone, code, "login"):
            raise ValidationError("验证码错误或已过期")

        # 2. 查找用户
        result = self.db.table("users").select("*").eq("phone", phone).execute()

        if not result.data:
            raise NotFoundError("用户", phone)

        user = result.data[0]

        # 3. 检查账号状态
        if user["status"] != "active":
            raise AuthenticationError("账号已被禁用")

        # 4. 更新最后登录时间
        self.db.table("users").update({
            "last_login_at": datetime.now(timezone.utc).isoformat()
        }).eq("id", user["id"]).execute()

        logger.info(f"User logged in by phone code | user_id={user['id']} | phone={phone}")

        # 5. 生成 token
        token = self._create_token_response(user["id"])

        return {
            "token": token,
            "user": self._format_user_response(user),
        }

    async def login_by_password(self, phone: str, password: str) -> dict:
        """
        手机号密码登录

        Args:
            phone: 手机号
            password: 密码

        Returns:
            包含 token 和用户信息的字典

        Raises:
            AuthenticationError: 用户名或密码错误
        """
        # 1. 查找用户
        result = self.db.table("users").select("*").eq("phone", phone).execute()

        if not result.data:
            raise AuthenticationError("用户名或密码错误")

        user = result.data[0]

        # 2. 检查是否设置了密码
        if not user.get("password_hash"):
            raise AuthenticationError("该账号未设置密码，请使用验证码登录")

        # 3. 验证密码
        if not verify_password(password, user["password_hash"]):
            raise AuthenticationError("用户名或密码错误")

        # 4. 检查账号状态
        if user["status"] != "active":
            raise AuthenticationError("账号已被禁用")

        # 5. 更新最后登录时间
        self.db.table("users").update({
            "last_login_at": datetime.now(timezone.utc).isoformat()
        }).eq("id", user["id"]).execute()

        logger.info(f"User logged in by password | user_id={user['id']} | phone={phone}")

        # 6. 生成 token
        token = self._create_token_response(user["id"])

        return {
            "token": token,
            "user": self._format_user_response(user),
        }

    async def login_by_org_password(
        self, org_name: str, phone: str, password: str,
    ) -> dict:
        """
        企业密码登录

        流程：精确匹配企业名 → 查用户 → 校验成员资格 → 验证密码 → 设置 current_org_id

        Returns:
            包含 token、用户信息、企业信息的字典

        Raises:
            AuthenticationError: 企业/用户/密码/状态异常
        """
        LOGIN_FAILED = "企业名称、手机号或密码错误"

        # 1. 精确匹配企业
        org_result = (
            self.db.table("organizations")
            .select("id, name, status")
            .eq("name", org_name)
            .execute()
        )
        if not org_result.data:
            raise AuthenticationError(LOGIN_FAILED)

        org = org_result.data[0]
        if org["status"] != "active":
            raise AuthenticationError(LOGIN_FAILED)

        org_id = str(org["id"])

        # 2. 查找用户
        user_result = (
            self.db.table("users")
            .select("*")
            .eq("phone", phone)
            .execute()
        )
        if not user_result.data:
            raise AuthenticationError(LOGIN_FAILED)

        user = user_result.data[0]
        user_id = str(user["id"])

        # 3. 检查账号状态
        if user["status"] != "active":
            raise AuthenticationError(LOGIN_FAILED)

        # 4. 校验成员资格
        member_result = (
            self.db.table("org_members")
            .select("role, status")
            .eq("org_id", org_id)
            .eq("user_id", user_id)
            .single()
            .execute()
        )
        if not member_result.data:
            raise AuthenticationError(LOGIN_FAILED)
        if member_result.data["status"] != "active":
            raise AuthenticationError(LOGIN_FAILED)

        # 5. 验证密码
        if not user.get("password_hash"):
            raise AuthenticationError(LOGIN_FAILED)
        if not verify_password(password, user["password_hash"]):
            raise AuthenticationError(LOGIN_FAILED)

        # 6. 更新 current_org_id + last_login_at
        self.db.table("users").update({
            "current_org_id": org_id,
            "last_login_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", user_id).execute()

        logger.info(
            f"User logged in via org | user_id={user_id} | "
            f"org_id={org_id} | org_name={org_name}"
        )

        # 7. 返回 token + 用户信息 + 企业信息
        token = self._create_token_response(user_id)

        return {
            "token": token,
            "user": self._format_user_response(user),
            "org": {
                "org_id": org_id,
                "org_name": org["name"],
                "org_role": member_result.data["role"],
            },
        }

    async def send_verification_code(self, phone: str, purpose: str) -> bool:
        """
        发送验证码

        Args:
            phone: 手机号
            purpose: 用途 (register/login/reset_password/bind_phone)

        Returns:
            是否发送成功
        """
        try:
            sms_service = get_sms_service()
            return await sms_service.send_verification_code(phone, purpose)
        except (ValidationError, AuthenticationError, ConflictError, NotFoundError) as e:
            # 业务异常直接抛出
            raise
        except Exception as e:
            logger.error(f"Failed to send verification code | phone={phone} | purpose={purpose} | error={e}")
            from core.exceptions import AppException
            raise AppException(
                code="SMS_SEND_ERROR",
                message="发送验证码失败，请稍后重试",
                status_code=500
            )

    async def verify_code_only(self, phone: str, code: str, purpose: str) -> bool:
        """
        仅验证验证码（不消费，用于忘记密码第一步）

        Args:
            phone: 手机号
            code: 验证码
            purpose: 用途

        Returns:
            验证码是否正确

        Raises:
            ValidationError: 验证码错误
        """
        try:
            if not await self._verify_code(phone, code, purpose):
                raise ValidationError("验证码错误或已过期")
            return True
        except (ValidationError, AuthenticationError, ConflictError, NotFoundError) as e:
            # 业务异常直接抛出
            raise
        except Exception as e:
            logger.error(f"Failed to verify code | phone={phone} | purpose={purpose} | error={e}")
            from core.exceptions import AppException
            raise AppException(
                code="VERIFY_CODE_ERROR",
                message="验证码验证失败，请稍后重试",
                status_code=500
            )

    async def reset_password(
        self, phone: str, code: str, new_password: str
    ) -> dict:
        """
        重置密码

        Args:
            phone: 手机号
            code: 验证码
            new_password: 新密码

        Returns:
            成功消息

        Raises:
            NotFoundError: 用户不存在
            ValidationError: 验证码错误
        """
        try:
            # 1. 先检查用户是否存在
            result = self.db.table("users").select("id").eq("phone", phone).execute()

            if not result.data:
                raise NotFoundError("用户", phone)

            user_id = result.data[0]["id"]

            # 2. 验证验证码
            if not await self._verify_code(phone, code, "reset_password"):
                raise ValidationError("验证码错误或已过期")

            # 3. 更新密码
            password_hash = hash_password(new_password)
            self.db.table("users").update({
                "password_hash": password_hash
            }).eq("id", user_id).execute()

            logger.info(f"User password reset | user_id={user_id} | phone={phone}")

            return {"message": "密码重置成功"}
        except (ValidationError, AuthenticationError, ConflictError, NotFoundError) as e:
            # 业务异常直接抛出
            raise
        except Exception as e:
            logger.error(f"Failed to reset password | phone={phone} | error={e}")
            from core.exceptions import AppException
            raise AppException(
                code="RESET_PASSWORD_ERROR",
                message="密码重置失败，请稍后重试",
                status_code=500
            )

    async def _verify_code(self, phone: str, code: str, purpose: str) -> bool:
        """
        验证验证码

        Args:
            phone: 手机号
            code: 验证码
            purpose: 用途

        Returns:
            验证码是否正确
        """
        try:
            sms_service = get_sms_service()
            return await sms_service.verify_code(phone, code, purpose)
        except (ValidationError, AuthenticationError, ConflictError, NotFoundError) as e:
            # 业务异常直接抛出
            raise
        except Exception as e:
            logger.error(f"Failed to verify code internally | phone={phone} | purpose={purpose} | error={e}")
            from core.exceptions import AppException
            raise AppException(
                code="VERIFY_CODE_ERROR",
                message="验证码验证失败，请稍后重试",
                status_code=500
            )

    async def refresh_access_token(self, raw_refresh_token: str) -> dict:
        """
        用 refresh token 换取新的 access + refresh token（轮换模式）

        流程：
        1. 计算哈希 → 查 DB
        2. 校验：未吊销 + 未过期 + 用户有效
        3. 吊销旧 refresh → 签发新双 token
        """
        token_hash = hash_refresh_token(raw_refresh_token)

        # 查 DB
        result = (
            self.db.table("refresh_tokens")
            .select("id, user_id, expires_at, revoked")
            .eq("token_hash", token_hash)
            .maybe_single()
            .execute()
        )

        if not result.data:
            raise AuthenticationError("无效的刷新令牌")

        record = result.data

        if record["revoked"]:
            # 可能被盗用：吊销该用户所有 refresh token（安全降级）
            self.db.table("refresh_tokens").update({
                "revoked": True,
                "revoked_at": datetime.now(timezone.utc).isoformat(),
            }).eq("user_id", record["user_id"]).eq("revoked", False).execute()
            logger.warning(
                f"Refresh token reuse detected, revoking all tokens | "
                f"user_id={record['user_id']}"
            )
            raise AuthenticationError("刷新令牌已失效，请重新登录")

        # 检查过期
        expires_at = datetime.fromisoformat(str(record["expires_at"]))
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at < datetime.now(timezone.utc):
            raise AuthenticationError("刷新令牌已过期，请重新登录")

        # 检查用户状态
        user_result = (
            self.db.table("users")
            .select("id, status")
            .eq("id", record["user_id"])
            .maybe_single()
            .execute()
        )
        if not user_result.data or user_result.data["status"] != "active":
            raise AuthenticationError("账号已被禁用")

        user_id = str(record["user_id"])

        now = datetime.now(timezone.utc)

        # 吊销旧 refresh token
        self.db.table("refresh_tokens").update({
            "revoked": True,
            "revoked_at": now.isoformat(),
        }).eq("id", record["id"]).execute()

        # 写路径清理：删除该用户已吊销或已过期的 token 行（防表膨胀）
        self.db.table("refresh_tokens").delete().eq(
            "user_id", user_id
        ).eq("revoked", True).execute()

        self.db.table("refresh_tokens").delete().eq(
            "user_id", user_id
        ).lt("expires_at", now.isoformat()).execute()

        # 签发新双 token
        token = self._create_token_response(user_id)

        logger.info(f"Token refreshed | user_id={user_id}")
        return {"token": token}

    def revoke_user_refresh_tokens(self, user_id: str) -> None:
        """吊销用户所有 refresh token（用于登出/密码重置）"""
        self.db.table("refresh_tokens").update({
            "revoked": True,
            "revoked_at": datetime.now(timezone.utc).isoformat(),
        }).eq("user_id", user_id).eq("revoked", False).execute()

    def _create_token_response(self, user_id: str) -> dict:
        """创建双 token 响应（委托 security.create_token_pair）"""
        return create_token_pair(self.db, user_id)

    def _format_user_response(self, user: dict) -> dict:
        """格式化用户响应"""
        phone = user.get("phone")
        masked_phone = None
        if phone and len(phone) >= 7:
            masked_phone = f"{phone[:3]}****{phone[-4:]}"

        # 判断企微绑定状态：login_methods 包含 "wecom" 即为已绑定
        login_methods = user.get("login_methods") or []
        wecom_bound = "wecom" in login_methods

        return {
            "id": str(user["id"]),
            "nickname": user["nickname"],
            "avatar_url": user.get("avatar_url"),
            "phone": masked_phone,
            "role": user["role"],
            "credits": user["credits"],
            "created_at": str(user["created_at"]),
            "wecom_bound": wecom_bound,
        }
