"""
认证服务

处理用户注册、登录、验证码等业务逻辑。
"""

from typing import Optional
from uuid import uuid4

from loguru import logger


from core.config import get_settings
from core.exceptions import (
    AuthenticationError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from core.security import (
    TokenMaterial,
    create_refresh_token,
    create_token_material,
    create_token_material_from_refresh,
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

        user_id = str(uuid4())
        material = create_token_material(user_id)
        try:
            result = self.db.rpc("register_web_identity", {
                "p_user_id": user_id,
                "p_phone": phone,
                "p_nickname": nickname or f"用户{phone[-4:]}",
                "p_password_hash": hash_password(password) if password else None,
                **self._refresh_params(material),
            }).execute()
        except Exception as exc:
            if "WEB_AUTH_PHONE_CONFLICT" in str(exc):
                raise ConflictError("该手机号已注册") from exc
            raise
        if not result.data:
            raise ValidationError("注册失败，请稍后重试")
        user = result.data
        if str(user["id"]) != user_id:
            raise AuthenticationError("注册身份校验失败")
        logger.info(f"User registered | user_id={user['id']}")

        return {
            "token": material.response(),
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

        user = self._lookup_candidate(phone)
        if not user:
            raise NotFoundError("用户", phone)
        if user["status"] != "active":
            raise AuthenticationError("账号已被禁用")
        material = create_token_material(str(user["id"]))
        user = self._commit_login(user, material)
        logger.info(f"User logged in by phone code | user_id={user['id']}")

        return {
            "token": material.response(),
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
        user = self._lookup_candidate(phone)
        if not user:
            raise AuthenticationError("用户名或密码错误")
        if not user.get("password_hash"):
            raise AuthenticationError("该账号未设置密码，请使用验证码登录")
        if not verify_password(password, user["password_hash"]):
            raise AuthenticationError("用户名或密码错误")
        if user["status"] != "active":
            raise AuthenticationError("账号已被禁用")
        material = create_token_material(str(user["id"]))
        user = self._commit_login(user, material)
        logger.info(f"User logged in by password | user_id={user['id']}")

        return {
            "token": material.response(),
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

        candidate = self._lookup_candidate(phone, org_name)
        if (
            not candidate
            or candidate["status"] != "active"
            or candidate.get("org_status") != "active"
            or candidate.get("member_status") != "active"
            or not candidate.get("password_hash")
            or not verify_password(password, candidate["password_hash"])
        ):
            raise AuthenticationError(LOGIN_FAILED)

        org_id = str(candidate["org_id"])
        material = create_token_material(str(candidate["id"]))
        user = self._commit_login(candidate, material, org_id)
        logger.info(
            f"User logged in via org | user_id={user['id']} | org_id={org_id}"
        )

        return {
            "token": material.response(),
            "user": self._format_user_response(user),
            "org": {
                "org_id": org_id,
                "org_name": candidate["org_name"],
                "org_role": candidate["org_role"],
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
            if not self._lookup_candidate(phone):
                raise NotFoundError("用户", phone)
            if not await self._verify_code(phone, code, "reset_password"):
                raise ValidationError("验证码错误或已过期")
            result = self.db.rpc("reset_web_password", {
                "p_phone": phone,
                "p_password_hash": hash_password(new_password),
            }).execute()
            if result.data is not True:
                raise NotFoundError("用户", phone)
            logger.info("User password reset")

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
        raw_refresh, refresh_hash, refresh_expires_at = create_refresh_token()
        result = self.db.rpc("rotate_web_refresh_token", {
            "p_old_hash": hash_refresh_token(raw_refresh_token),
            "p_new_hash": refresh_hash,
            "p_new_expires_at": refresh_expires_at.isoformat(),
        }).execute()
        outcome = (result.data or {}).get("outcome")
        errors = {
            "invalid": "无效的刷新令牌",
            "reuse": "刷新令牌已失效，请重新登录",
            "expired": "刷新令牌已过期，请重新登录",
            "inactive": "账号已被禁用",
        }
        if outcome != "rotated":
            raise AuthenticationError(errors.get(outcome, "无效的刷新令牌"))
        user_id = str(result.data["user_id"])
        token = create_token_material_from_refresh(
            user_id, raw_refresh, refresh_hash, refresh_expires_at,
        )
        logger.info(f"Token refreshed | user_id={user_id}")
        return {"token": token.response()}

    def revoke_refresh_token(self, raw_refresh_token: str) -> None:
        """通过认证门面幂等吊销精确 refresh token。"""
        self.db.rpc("revoke_web_refresh_token", {
            "p_token_hash": hash_refresh_token(raw_refresh_token),
        }).execute()

    def _lookup_candidate(
        self, phone: str, org_name: str | None = None,
    ) -> dict[str, object] | None:
        result = self.db.rpc("lookup_web_auth_candidate", {
            "p_phone": phone,
            "p_org_name": org_name,
        }).execute()
        return result.data

    def _commit_login(
        self,
        candidate: dict[str, object],
        material: TokenMaterial,
        org_id: str | None = None,
    ) -> dict[str, object]:
        try:
            result = self.db.rpc("commit_web_login", {
                "p_user_id": str(candidate["id"]),
                "p_org_id": org_id,
                **self._refresh_params(material),
            }).execute()
        except Exception as exc:
            if "WEB_AUTH_PRINCIPAL_INACTIVE" in str(exc):
                raise AuthenticationError("账号或企业状态已变更") from exc
            raise
        if not result.data:
            raise AuthenticationError("账号已被禁用")
        return result.data

    @staticmethod
    def _refresh_params(material: TokenMaterial) -> dict[str, str]:
        return {
            "p_refresh_hash": material.refresh_token_hash,
            "p_refresh_expires_at": material.refresh_expires_at.isoformat(),
        }

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
