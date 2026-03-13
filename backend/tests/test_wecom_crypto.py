"""
WXBizMsgCrypt 加解密单元测试

覆盖：encrypt/decrypt 往返、verify_url、decrypt_msg、
      签名验证失败、corp_id 不匹配、无效 AES Key
"""

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import hashlib

import pytest

from services.wecom.crypto import WXBizMsgCrypt
from services.wecom.crypto import ierror

TOKEN = "test_token_123"
ENCODING_AES_KEY = "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG"
CORP_ID = "ww_test_corp_id"


@pytest.fixture
def crypt():
    return WXBizMsgCrypt(TOKEN, ENCODING_AES_KEY, CORP_ID)


class TestEncryptDecrypt:
    """加密/解密往返测试"""

    def test_roundtrip_basic(self, crypt):
        """基本文本加密后解密还原"""
        plaintext = "Hello 你好"
        ret, encrypted = crypt._encrypt(plaintext)
        assert ret == 0
        assert encrypted is not None

        ret, decrypted = crypt._decrypt(encrypted)
        assert ret == 0
        assert decrypted == plaintext

    def test_roundtrip_xml(self, crypt):
        """XML 格式消息加密后解密还原"""
        xml = (
            "<xml><ToUserName><![CDATA[test]]></ToUserName>"
            "<Content><![CDATA[消息内容]]></Content></xml>"
        )
        ret, encrypted = crypt._encrypt(xml)
        assert ret == 0

        ret, decrypted = crypt._decrypt(encrypted)
        assert ret == 0
        assert decrypted == xml

    def test_roundtrip_empty(self, crypt):
        """空字符串加密解密"""
        ret, encrypted = crypt._encrypt("")
        assert ret == 0

        ret, decrypted = crypt._decrypt(encrypted)
        assert ret == 0
        assert decrypted == ""

    def test_roundtrip_long_text(self, crypt):
        """长文本加密解密"""
        text = "A" * 10000
        ret, encrypted = crypt._encrypt(text)
        assert ret == 0

        ret, decrypted = crypt._decrypt(encrypted)
        assert ret == 0
        assert decrypted == text

    def test_wrong_corp_id_fails(self):
        """corp_id 不匹配应返回错误"""
        crypt1 = WXBizMsgCrypt(TOKEN, ENCODING_AES_KEY, "corp_a")
        crypt2 = WXBizMsgCrypt(TOKEN, ENCODING_AES_KEY, "corp_b")

        ret, encrypted = crypt1._encrypt("hello")
        assert ret == 0

        ret, decrypted = crypt2._decrypt(encrypted)
        assert ret == ierror.WXBizMsgCrypt_ValidateAppid_Error


class TestSignature:
    """签名计算测试"""

    def test_signature_deterministic(self, crypt):
        """相同输入产生相同签名"""
        ret1, sig1 = crypt._compute_signature("123", "abc", "enc")
        ret2, sig2 = crypt._compute_signature("123", "abc", "enc")
        assert ret1 == 0
        assert sig1 == sig2

    def test_signature_sha1(self, crypt):
        """验证签名算法为 SHA1"""
        timestamp, nonce, encrypt = "1234567890", "nonce123", "encrypt_data"
        ret, sig = crypt._compute_signature(timestamp, nonce, encrypt)

        # 手动计算
        sort_list = sorted([TOKEN, timestamp, nonce, encrypt])
        expected = hashlib.sha1("".join(sort_list).encode("utf-8")).hexdigest()

        assert ret == 0
        assert sig == expected


class TestVerifyUrl:
    """verify_url 回调验证测试"""

    def test_verify_url_success(self, crypt):
        """正确签名+echostr 应成功解密"""
        # 先加密一个 echostr
        ret, encrypted_echo = crypt._encrypt("echo_test_string")
        assert ret == 0

        # 计算正确签名
        timestamp, nonce = "1234567890", "test_nonce"
        ret, signature = crypt._compute_signature(timestamp, nonce, encrypted_echo)
        assert ret == 0

        # 验证
        ret, result = crypt.verify_url(signature, timestamp, nonce, encrypted_echo)
        assert ret == 0
        assert result == "echo_test_string"

    def test_verify_url_bad_signature(self, crypt):
        """错误签名应验证失败"""
        ret, encrypted = crypt._encrypt("echo")
        assert ret == 0

        ret, result = crypt.verify_url("bad_signature", "123", "abc", encrypted)
        assert ret == ierror.WXBizMsgCrypt_ValidateSignature_Error
        assert result is None


class TestDecryptMsg:
    """decrypt_msg 消息解密测试"""

    def test_decrypt_msg_success(self, crypt):
        """完整的消息解密流程"""
        plaintext_xml = "<xml><Content>hello</Content></xml>"
        ret, encrypted = crypt._encrypt(plaintext_xml)
        assert ret == 0

        # 构建加密 XML 信封
        timestamp, nonce = "1234567890", "test_nonce"
        ret, signature = crypt._compute_signature(timestamp, nonce, encrypted)
        assert ret == 0

        post_xml = (
            f"<xml><Encrypt><![CDATA[{encrypted}]]></Encrypt>"
            f"<ToUserName><![CDATA[test]]></ToUserName></xml>"
        )

        ret, result = crypt.decrypt_msg(post_xml, signature, timestamp, nonce)
        assert ret == 0
        assert result == plaintext_xml

    def test_decrypt_msg_bad_signature(self, crypt):
        """签名不匹配应失败"""
        ret, encrypted = crypt._encrypt("<xml>test</xml>")
        post_xml = f"<xml><Encrypt><![CDATA[{encrypted}]]></Encrypt></xml>"

        ret, result = crypt.decrypt_msg(post_xml, "wrong_sig", "123", "abc")
        assert ret == ierror.WXBizMsgCrypt_ValidateSignature_Error

    def test_decrypt_msg_invalid_xml(self, crypt):
        """无效 XML 应返回解析错误"""
        ret, result = crypt.decrypt_msg("not xml", "sig", "123", "abc")
        assert ret == ierror.WXBizMsgCrypt_ParseXml_Error


class TestEncryptMsg:
    """encrypt_msg 回复加密测试"""

    def test_encrypt_msg_roundtrip(self, crypt):
        """加密后的 XML 可以反向解密"""
        reply = "<xml><Content>reply</Content></xml>"
        nonce = "test_nonce"
        timestamp = "1234567890"

        ret, encrypted_xml = crypt.encrypt_msg(reply, nonce, timestamp)
        assert ret == 0
        assert "<Encrypt>" in encrypted_xml
        assert "<MsgSignature>" in encrypted_xml

        # 提取并验证
        import xml.etree.ElementTree as ET
        root = ET.fromstring(encrypted_xml)
        sig = root.find("MsgSignature").text
        ts = root.find("TimeStamp").text
        nc = root.find("Nonce").text

        ret, decrypted = crypt.decrypt_msg(encrypted_xml, sig, ts, nc)
        assert ret == 0
        assert decrypted == reply


class TestInvalidKey:
    """无效 AES Key 测试"""

    def test_short_key_raises(self):
        """太短的 EncodingAESKey 应抛出异常"""
        with pytest.raises(ValueError, match="EncodingAESKey 无效"):
            WXBizMsgCrypt(TOKEN, "short_key", CORP_ID)

    def test_invalid_base64_raises(self):
        """非法 Base64 应抛出异常"""
        with pytest.raises(ValueError, match="EncodingAESKey 无效"):
            WXBizMsgCrypt(TOKEN, "!!!!!invalid!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!", CORP_ID)
