"""
快麦 Web cookie 的加密包装

复用 core/crypto 的 AES-256-GCM + per-org encrypt_key 机制（migration 103）。
透明加解密：credential_store 内部调用，调用方拿到的永远是明文。

Schema 兼容：
  - 存储格式：base64(nonce + ciphertext + tag)
  - 旧明文：base64 解码后长度 < 13 字节会抛 ValueError，
    所以我们用前缀 `enc:` 区分 加密 vs 明文，过渡期兼容。
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from loguru import logger

from core.config import get_settings
from core.crypto import aes_decrypt, aes_encrypt


_ENC_PREFIX = "enc:"


@lru_cache(maxsize=64)
def _resolve_org_key(org_id: str) -> str:
    """
    解析 org 加密密钥（缓存）。

    优先从 organizations.encrypt_key 读，否则 fallback 到 settings 全局 key。
    """
    from core.database import get_db

    db = get_db()
    resp = (
        db.table("organizations")
        .select("encrypt_key")
        .eq("id", org_id)
        .maybe_single()
        .execute()
    )
    if resp and resp.data and resp.data.get("encrypt_key"):
        return resp.data["encrypt_key"]

    # Fallback 到全局 key
    settings = get_settings()
    fallback = settings.org_config_encrypt_key
    if not fallback:
        raise RuntimeError(
            f"org {org_id} 缺少 encrypt_key 且全局 ORG_CONFIG_ENCRYPT_KEY 未配置"
        )
    return fallback


def encrypt_cookie(*, org_id: str, plaintext: str) -> str:
    """
    加密 cookie 字符串。

    Args:
        org_id: 所属企业
        plaintext: 明文 cookie

    Returns:
        带 `enc:` 前缀的密文字符串（用于跟旧明文区分）
    """
    if not plaintext:
        return ""
    key = _resolve_org_key(org_id)
    ciphertext = aes_encrypt(plaintext, key)
    return _ENC_PREFIX + ciphertext


def decrypt_cookie(*, org_id: str, stored: str) -> str:
    """
    解密 cookie 字符串。

    向后兼容：如果存的是旧明文（无 `enc:` 前缀），直接返回。
    生产部署后，旧明文会通过 backfill 脚本迁移成密文。

    Args:
        org_id: 所属企业
        stored: DB 里读出来的字符串（密文或旧明文）

    Returns:
        明文 cookie
    """
    if not stored:
        return ""

    if not stored.startswith(_ENC_PREFIX):
        # 旧明文，向后兼容
        logger.debug(f"decrypt_cookie 旧明文 | org={org_id}")
        return stored

    encrypted = stored[len(_ENC_PREFIX):]
    try:
        key = _resolve_org_key(org_id)
        return aes_decrypt(encrypted, key)
    except Exception as e:
        logger.error(f"decrypt_cookie 失败 | org={org_id} | err={e}")
        raise


def is_encrypted(stored: str) -> bool:
    """判断 DB 里的值是否已加密。"""
    return bool(stored) and stored.startswith(_ENC_PREFIX)
