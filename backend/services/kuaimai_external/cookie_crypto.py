"""
快麦 Web cookie 的加密包装（async 版）

复用 core/crypto 的 AES-256-GCM + per-org encrypt_key 机制（migration 103）。
透明加解密：credential_store 内部调用，调用方拿到的永远是明文。

Schema 兼容：
  - 存储格式：base64(nonce + ciphertext + tag)，带 `enc:` 前缀
  - 旧明文：无前缀 → 直接返回，过渡期兼容
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from core.config import get_settings
from core.crypto import aes_decrypt, aes_encrypt


_ENC_PREFIX = "enc:"

# 进程级缓存（async 友好的简单 dict，避免 lru_cache 不支持 await）
_KEY_CACHE: dict[str, str] = {}


async def _resolve_org_key(org_id: str) -> str:
    """
    解析 org 加密密钥（缓存）。

    优先从 organizations.encrypt_key 读，否则 fallback 到 settings 全局 key。
    """
    if org_id in _KEY_CACHE:
        return _KEY_CACHE[org_id]

    from core.database import get_async_db

    db = await get_async_db()
    resp = await (
        db.table("organizations")
        .select("encrypt_key")
        .eq("id", org_id)
        .maybe_single()
        .execute()
    )
    if resp and resp.data and resp.data.get("encrypt_key"):
        key = resp.data["encrypt_key"]
        _KEY_CACHE[org_id] = key
        return key

    # Fallback 到全局 key
    settings = get_settings()
    fallback = settings.org_config_encrypt_key
    if not fallback:
        raise RuntimeError(
            f"org {org_id} 缺少 encrypt_key 且全局 ORG_CONFIG_ENCRYPT_KEY 未配置"
        )
    _KEY_CACHE[org_id] = fallback
    return fallback


async def encrypt_cookie(*, org_id: str, plaintext: str) -> str:
    """加密 cookie 字符串，返回带 `enc:` 前缀的密文。"""
    if not plaintext:
        return ""
    key = await _resolve_org_key(org_id)
    ciphertext = aes_encrypt(plaintext, key)
    return _ENC_PREFIX + ciphertext


async def decrypt_cookie(*, org_id: str, stored: str) -> str:
    """
    解密 cookie 字符串。

    向后兼容：无 `enc:` 前缀 = 旧明文 → 直接返回。
    """
    if not stored:
        return ""

    if not stored.startswith(_ENC_PREFIX):
        logger.debug(f"decrypt_cookie 旧明文 | org={org_id}")
        return stored

    encrypted = stored[len(_ENC_PREFIX):]
    try:
        key = await _resolve_org_key(org_id)
        return aes_decrypt(encrypted, key)
    except Exception as e:
        logger.error(f"decrypt_cookie 失败 | org={org_id} | err={e}")
        raise


def is_encrypted(stored: str) -> bool:
    """判断 DB 里的值是否已加密（同步函数，调用方一般已有 string）。"""
    return bool(stored) and stored.startswith(_ENC_PREFIX)
