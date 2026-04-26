"""
安全相关工具

包含密码哈希、JWT Token 生成与验证、Refresh Token 等功能。
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import bcrypt
from jose import JWTError, jwt

from core.config import get_settings
from core.exceptions import InvalidTokenError, TokenExpiredError


def hash_password(password: str) -> str:
    """
    对密码进行哈希处理

    Args:
        password: 明文密码

    Returns:
        哈希后的密码字符串
    """
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(password.encode("utf-8"), salt)
    return hashed.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    验证密码是否正确

    Args:
        plain_password: 明文密码
        hashed_password: 哈希后的密码

    Returns:
        密码是否匹配
    """
    return bcrypt.checkpw(
        plain_password.encode("utf-8"),
        hashed_password.encode("utf-8"),
    )


def create_access_token(
    data: dict[str, Any],
    expires_delta: Optional[timedelta] = None,
) -> str:
    """
    创建 JWT Access Token

    Args:
        data: 要编码到 Token 中的数据
        expires_delta: 过期时间增量，默认使用配置中的值

    Returns:
        JWT Token 字符串
    """
    settings = get_settings()
    to_encode = data.copy()

    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(
            minutes=settings.jwt_access_token_expire_minutes
        )

    to_encode.update({"exp": expire, "iat": datetime.now(timezone.utc)})

    encoded_jwt = jwt.encode(
        to_encode,
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    return encoded_jwt


def decode_access_token(token: str) -> dict[str, Any]:
    """
    解码并验证 JWT Token

    Args:
        token: JWT Token 字符串

    Returns:
        解码后的 Token 数据

    Raises:
        TokenExpiredError: Token 已过期
        InvalidTokenError: Token 无效
    """
    settings = get_settings()

    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
        return payload
    except JWTError as e:
        error_message = str(e).lower()
        if "expired" in error_message:
            raise TokenExpiredError()
        raise InvalidTokenError()


def create_refresh_token() -> tuple[str, str, datetime]:
    """
    创建 Refresh Token（不可逆随机字符串 + SHA-256 哈希）

    Returns:
        (raw_token, token_hash, expires_at)
        - raw_token: 返回给客户端的明文 token
        - token_hash: 存入 DB 的 SHA-256 哈希
        - expires_at: 过期时间（UTC）
    """
    settings = get_settings()
    raw_token = secrets.token_urlsafe(48)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    expires_at = datetime.now(timezone.utc) + timedelta(
        days=settings.jwt_refresh_token_expire_days
    )
    return raw_token, token_hash, expires_at


def hash_refresh_token(raw_token: str) -> str:
    """对 refresh token 明文计算 SHA-256 哈希，用于 DB 查找"""
    return hashlib.sha256(raw_token.encode()).hexdigest()


def create_token_pair(db: Any, user_id: str) -> dict:
    """
    签发 access + refresh token 并将 refresh hash 存入 DB。

    这是所有登录/刷新路径的唯一 token 工厂，service 层不应自行拼装。

    Args:
        db: Supabase 数据库客户端
        user_id: 用户 ID

    Returns:
        符合 TokenResponse schema 的 dict
    """
    settings = get_settings()
    access_token = create_access_token({"sub": str(user_id)})
    raw_refresh, token_hash, refresh_expires_at = create_refresh_token()

    # refresh token 哈希存 DB（明文不落盘）
    db.table("refresh_tokens").insert({
        "user_id": user_id,
        "token_hash": token_hash,
        "expires_at": refresh_expires_at.isoformat(),
    }).execute()

    return {
        "access_token": access_token,
        "refresh_token": raw_refresh,
        "token_type": "bearer",
        "expires_in": settings.jwt_access_token_expire_minutes * 60,
        "refresh_expires_in": settings.jwt_refresh_token_expire_days * 86400,
    }


def generate_verification_code(length: int = 6) -> str:
    """
    生成数字验证码

    Args:
        length: 验证码长度，默认6位

    Returns:
        验证码字符串
    """
    import random

    return "".join(random.choices("0123456789", k=length))
