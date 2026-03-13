"""
企业微信消息加解密（Python 3 版本）

基于企微官方 SDK 适配，提供三个核心接口：
- VerifyURL：回调 URL 验证（GET 请求解密 echostr）
- DecryptMsg：解密收到的消息（POST 请求解密 XML）
- EncryptMsg：加密回复消息（如需 XML 被动回复时使用）

依赖：pycryptodome（AES-CBC 加解密）

@copyright: Copyright (c) 1998-2014 Tencent Inc.（原始 SDK）
"""

import base64
import hashlib
import random
import socket
import string
import struct
import time
import xml.etree.ElementTree as ET

from Crypto.Cipher import AES
from loguru import logger

from . import ierror


class WXBizMsgCrypt:
    """企业微信消息加解密器"""

    def __init__(self, token: str, encoding_aes_key: str, corp_id: str):
        """
        Args:
            token: 企微后台设置的 Token
            encoding_aes_key: 企微后台设置的 EncodingAESKey（43 字符）
            corp_id: 企业 ID（CorpID）
        """
        try:
            self.key = base64.b64decode(encoding_aes_key + "=")
            assert len(self.key) == 32
        except Exception:
            raise ValueError("EncodingAESKey 无效，必须为 43 位 Base64 字符串")
        self.token = token
        self.corp_id = corp_id

    def verify_url(
        self,
        msg_signature: str,
        timestamp: str,
        nonce: str,
        echostr: str,
    ) -> tuple[int, str | None]:
        """
        验证回调 URL（GET 请求）。

        Returns:
            (errcode, 解密后的 echostr) — errcode=0 表示成功
        """
        ret, signature = self._compute_signature(timestamp, nonce, echostr)
        if ret != 0:
            return ret, None
        if signature != msg_signature:
            return ierror.WXBizMsgCrypt_ValidateSignature_Error, None

        ret, plaintext = self._decrypt(echostr)
        if ret != 0:
            return ret, None
        return 0, plaintext

    def decrypt_msg(
        self,
        post_data: str,
        msg_signature: str,
        timestamp: str,
        nonce: str,
    ) -> tuple[int, str | None]:
        """
        解密收到的消息（POST 请求）。

        Args:
            post_data: POST 请求体（加密 XML）
            msg_signature: 签名（query 参数）
            timestamp: 时间戳（query 参数）
            nonce: 随机串（query 参数）

        Returns:
            (errcode, 解密后的 XML 明文) — errcode=0 表示成功
        """
        # 从 XML 中提取 Encrypt 字段
        ret, encrypt_text = self._extract_encrypt(post_data)
        if ret != 0:
            return ret, None

        # 验签
        ret, signature = self._compute_signature(timestamp, nonce, encrypt_text)
        if ret != 0:
            return ret, None
        if signature != msg_signature:
            return ierror.WXBizMsgCrypt_ValidateSignature_Error, None

        # 解密
        ret, plaintext = self._decrypt(encrypt_text)
        if ret != 0:
            return ret, None
        return 0, plaintext

    def encrypt_msg(
        self,
        reply_msg: str,
        nonce: str,
        timestamp: str | None = None,
    ) -> tuple[int, str | None]:
        """
        加密回复消息（如需被动回复 XML 时使用）。

        Returns:
            (errcode, 加密后的 XML) — errcode=0 表示成功
        """
        ret, encrypt_text = self._encrypt(reply_msg)
        if ret != 0:
            return ret, None

        if timestamp is None:
            timestamp = str(int(time.time()))

        ret, signature = self._compute_signature(timestamp, nonce, encrypt_text)
        if ret != 0:
            return ret, None

        resp_xml = (
            "<xml>"
            f"<Encrypt><![CDATA[{encrypt_text}]]></Encrypt>"
            f"<MsgSignature><![CDATA[{signature}]]></MsgSignature>"
            f"<TimeStamp>{timestamp}</TimeStamp>"
            f"<Nonce><![CDATA[{nonce}]]></Nonce>"
            "</xml>"
        )
        return 0, resp_xml

    # ── 内部方法 ──────────────────────────────────────────

    def _compute_signature(
        self, timestamp: str, nonce: str, encrypt: str,
    ) -> tuple[int, str | None]:
        """SHA1 签名计算"""
        try:
            sort_list = sorted([self.token, timestamp, nonce, encrypt])
            sha = hashlib.sha1("".join(sort_list).encode("utf-8"))
            return 0, sha.hexdigest()
        except Exception as e:
            logger.warning(f"Wecom crypto: signature compute failed | error={e}")
            return ierror.WXBizMsgCrypt_ComputeSignature_Error, None

    def _encrypt(self, text: str) -> tuple[int, str | None]:
        """AES-CBC 加密"""
        try:
            text_bytes = text.encode("utf-8")
            # 16 字节随机前缀 + 4 字节消息长度（网络字节序）+ 明文 + corp_id
            rand_bytes = "".join(
                random.sample(string.ascii_letters + string.digits, 16)
            ).encode("utf-8")
            payload = (
                rand_bytes
                + struct.pack("!I", len(text_bytes))
                + text_bytes
                + self.corp_id.encode("utf-8")
            )
            # PKCS#7 填充（block_size=32）
            pad_len = 32 - (len(payload) % 32)
            payload += bytes([pad_len]) * pad_len

            cipher = AES.new(self.key, AES.MODE_CBC, self.key[:16])
            encrypted = cipher.encrypt(payload)
            return 0, base64.b64encode(encrypted).decode("utf-8")
        except Exception as e:
            logger.warning(f"Wecom crypto: encrypt failed | error={e}")
            return ierror.WXBizMsgCrypt_EncryptAES_Error, None

    def _decrypt(self, text: str) -> tuple[int, str | None]:
        """AES-CBC 解密"""
        try:
            cipher = AES.new(self.key, AES.MODE_CBC, self.key[:16])
            plain_bytes = cipher.decrypt(base64.b64decode(text))
        except Exception as e:
            logger.warning(f"Wecom crypto: decrypt failed | error={e}")
            return ierror.WXBizMsgCrypt_DecryptAES_Error, None

        try:
            # 去 PKCS#7 填充
            pad = plain_bytes[-1]
            content = plain_bytes[16:-pad]  # 去掉 16 字节随机前缀

            # 解析消息体长度
            msg_len = socket.ntohl(struct.unpack("I", content[:4])[0])
            msg_content = content[4 : 4 + msg_len].decode("utf-8")
            from_corp_id = content[4 + msg_len :].decode("utf-8")
        except Exception as e:
            logger.warning(f"Wecom crypto: buffer parse failed | error={e}")
            return ierror.WXBizMsgCrypt_IllegalBuffer, None

        if from_corp_id != self.corp_id:
            return ierror.WXBizMsgCrypt_ValidateAppid_Error, None

        return 0, msg_content

    @staticmethod
    def _extract_encrypt(xml_text: str) -> tuple[int, str | None]:
        """从 XML 中提取 Encrypt 字段"""
        try:
            tree = ET.fromstring(xml_text)
            encrypt_node = tree.find("Encrypt")
            if encrypt_node is None or not encrypt_node.text:
                return ierror.WXBizMsgCrypt_ParseXml_Error, None
            return 0, encrypt_node.text
        except Exception as e:
            logger.warning(f"Wecom crypto: XML parse failed | error={e}")
            return ierror.WXBizMsgCrypt_ParseXml_Error, None
