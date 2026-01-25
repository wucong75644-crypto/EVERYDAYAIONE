"""
认证服务

处理用户注册、登录、验证码等业务逻辑。
"""

from datetime import datetime, timezone
from typing import Optional

from loguru import logger
from supabase import Client

from core.config import get_settings
from core.exceptions import (
    AuthenticationError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from core.security import (
    create_access_token,
    hash_password,
    verify_password,
)
from services.sms_service import get_sms_service


class AuthService:
    """认证服务类"""

    def __init__(self, db: Client):
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

        # 5. 自动订阅默认模型
        await self._subscribe_default_models(user["id"])

        # 6. 生成 token
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

    async def send_verification_code(self, phone: str, purpose: str) -> bool:
        """
        发送验证码

        Args:
            phone: 手机号
            purpose: 用途 (register/login/reset_password/bind_phone)

        Returns:
            是否发送成功
        """
        sms_service = get_sms_service()
        return await sms_service.send_verification_code(phone, purpose)

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
        if not await self._verify_code(phone, code, purpose):
            raise ValidationError("验证码错误或已过期")
        return True

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
        sms_service = get_sms_service()
        return await sms_service.verify_code(phone, code, purpose)

    async def _subscribe_default_models(self, user_id: str) -> None:
        """为新用户订阅默认模型"""
        # 获取所有默认模型
        models = self.db.table("models").select("id").eq("is_default", True).execute()

        if models.data:
            subscriptions = [
                {"user_id": user_id, "model_id": model["id"]}
                for model in models.data
            ]
            self.db.table("user_subscriptions").insert(subscriptions).execute()

    def _create_token_response(self, user_id: str) -> dict:
        """创建 token 响应"""
        access_token = create_access_token({"sub": user_id})
        expires_in = self.settings.jwt_access_token_expire_minutes * 60

        return {
            "access_token": access_token,
            "token_type": "bearer",
            "expires_in": expires_in,
        }

    def _format_user_response(self, user: dict) -> dict:
        """格式化用户响应"""
        phone = user.get("phone")
        masked_phone = None
        if phone and len(phone) >= 7:
            masked_phone = f"{phone[:3]}****{phone[-4:]}"

        return {
            "id": user["id"],
            "nickname": user["nickname"],
            "avatar_url": user.get("avatar_url"),
            "phone": masked_phone,
            "role": user["role"],
            "credits": user["credits"],
            "created_at": user["created_at"],
        }
