"""
AES-256-GCM 加解密工具

用于企业配置（org_configs）中 API Key 等敏感数据的加密存储。
格式：base64(nonce + ciphertext + tag)
"""

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def _get_key(key_b64: str) -> bytes:
    """解码 base64 密钥，校验 32 字节"""
    key = base64.b64decode(key_b64)
    if len(key) != 32:
        raise ValueError(f"加密密钥长度必须为 32 字节，实际 {len(key)} 字节")
    return key


def aes_encrypt(plaintext: str, key_b64: str) -> str:
    """
    AES-256-GCM 加密

    Args:
        plaintext: 明文字符串
        key_b64: base64 编码的 32 字节密钥

    Returns:
        base64(nonce + ciphertext + tag)
    """
    key = _get_key(key_b64)
    nonce = os.urandom(12)  # GCM 标准 96-bit nonce
    aesgcm = AESGCM(key)
    ct = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    return base64.b64encode(nonce + ct).decode("ascii")


def aes_decrypt(encrypted_b64: str, key_b64: str) -> str:
    """
    AES-256-GCM 解密

    Args:
        encrypted_b64: base64(nonce + ciphertext + tag)
        key_b64: base64 编码的 32 字节密钥

    Returns:
        明文字符串

    Raises:
        ValueError: 解密失败（密钥错误或数据被篡改）
    """
    key = _get_key(key_b64)
    raw = base64.b64decode(encrypted_b64)
    if len(raw) < 13:  # 12 nonce + 至少 1 byte
        raise ValueError("加密数据格式无效")
    nonce = raw[:12]
    ct = raw[12:]
    aesgcm = AESGCM(key)
    try:
        plaintext = aesgcm.decrypt(nonce, ct, None)
    except Exception:
        raise ValueError("解密失败：密钥错误或数据被篡改")
    return plaintext.decode("utf-8")


def generate_encrypt_key() -> str:
    """生成随机 AES-256 密钥（base64 编码），用于初始配置"""
    return base64.b64encode(os.urandom(32)).decode("ascii")
