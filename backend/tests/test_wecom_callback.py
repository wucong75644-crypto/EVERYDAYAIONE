"""
企微回调路由单元测试

覆盖：GET URL 验证、POST 消息接收解密、XML 解析、异步处理分发
"""

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from services.wecom.crypto import WXBizMsgCrypt

TOKEN = "test_token_123"
ENCODING_AES_KEY = "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG"
CORP_ID = "ww_test_corp"


@pytest.fixture
def crypt():
    return WXBizMsgCrypt(TOKEN, ENCODING_AES_KEY, CORP_ID)


@pytest.fixture
def client():
    """创建 FastAPI 测试客户端，只挂载 wecom 路由"""
    from fastapi import FastAPI
    from api.deps import get_request_db
    from api.routes.wecom import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    db = MagicMock()
    db.rpc.return_value.execute.return_value.data = {
        "outcome": "enqueued",
    }
    app.dependency_overrides[get_request_db] = lambda: db
    return TestClient(app)


class TestVerifyUrl:
    """GET /api/wecom/callback URL 验证"""

    def test_verify_url_success(self, client, crypt):
        """正确签名 → 返回解密后的 echostr"""
        # 加密 echostr
        ret, encrypted_echo = crypt._encrypt("echo_test_12345")
        assert ret == 0

        # 计算签名
        timestamp, nonce = "1234567890", "nonce_abc"
        ret, signature = crypt._compute_signature(timestamp, nonce, encrypted_echo)
        assert ret == 0

        with patch("api.routes.wecom._get_crypt", return_value=(crypt, CORP_ID)):
            resp = client.get(
                "/api/wecom/callback/org-1",
                params={
                    "msg_signature": signature,
                    "timestamp": timestamp,
                    "nonce": nonce,
                    "echostr": encrypted_echo,
                },
            )

        assert resp.status_code == 200
        assert resp.text == "echo_test_12345"

    def test_verify_url_bad_signature(self, client, crypt):
        """签名错误 → 403"""
        ret, encrypted_echo = crypt._encrypt("echo")
        assert ret == 0

        with patch("api.routes.wecom._get_crypt", return_value=(crypt, CORP_ID)):
            resp = client.get(
                "/api/wecom/callback/org-1",
                params={
                    "msg_signature": "wrong_signature",
                    "timestamp": "123",
                    "nonce": "abc",
                    "echostr": encrypted_echo,
                },
            )

        assert resp.status_code == 403

    def test_verify_url_missing_params(self, client):
        """缺少参数 → 422"""
        resp = client.get("/api/wecom/callback/org-1")
        assert resp.status_code == 422


class TestReceiveMessage:
    """POST /api/wecom/callback 消息接收"""

    def test_receive_text_message(self, client, crypt):
        """接收文本消息 → 解密成功 → 返回 success"""
        # 构建明文 XML
        plaintext_xml = (
            "<xml>"
            "<MsgType><![CDATA[text]]></MsgType>"
            "<Content><![CDATA[你好AI]]></Content>"
            "<FromUserName><![CDATA[user_001]]></FromUserName>"
            "<MsgId>12345</MsgId>"
            "</xml>"
        )

        # 加密
        ret, encrypted = crypt._encrypt(plaintext_xml)
        assert ret == 0

        timestamp, nonce = "1234567890", "nonce_xyz"
        ret, signature = crypt._compute_signature(timestamp, nonce, encrypted)
        assert ret == 0

        post_xml = (
            f"<xml><Encrypt><![CDATA[{encrypted}]]></Encrypt>"
            f"<ToUserName><![CDATA[corp]]></ToUserName></xml>"
        )

        with patch("api.routes.wecom._get_crypt", return_value=(crypt, CORP_ID)):
            resp = client.post(
                "/api/wecom/callback/org-1",
                params={
                    "msg_signature": signature,
                    "timestamp": timestamp,
                    "nonce": nonce,
                },
                content=post_xml,
            )

        assert resp.status_code == 200
        assert resp.text == "success"

    def test_receive_bad_signature(self, client, crypt):
        """签名错误 → 403"""
        ret, encrypted = crypt._encrypt("<xml>test</xml>")
        post_xml = f"<xml><Encrypt><![CDATA[{encrypted}]]></Encrypt></xml>"

        with patch("api.routes.wecom._get_crypt", return_value=(crypt, CORP_ID)):
            resp = client.post(
                "/api/wecom/callback/org-1",
                params={
                    "msg_signature": "bad_sig",
                    "timestamp": "123",
                    "nonce": "abc",
                },
                content=post_xml,
            )

        assert resp.status_code == 403

    def test_receive_invalid_xml(self, client, crypt):
        """无效 XML → 403"""
        with patch("api.routes.wecom._get_crypt", return_value=(crypt, CORP_ID)):
            resp = client.post(
                "/api/wecom/callback/org-1",
                params={
                    "msg_signature": "sig",
                    "timestamp": "123",
                    "nonce": "abc",
                },
                content="not xml at all",
            )

        assert resp.status_code == 403


class TestParseCallbackXml:
    def test_event_type_skipped(self):
        """event 类型消息被跳过"""
        from services.wecom.callback_inbox_worker import _parse_callback_message

        xml = (
            "<xml>"
            "<MsgType><![CDATA[event]]></MsgType>"
            "<Event><![CDATA[subscribe]]></Event>"
            "</xml>"
        )

        assert _parse_callback_message(
            xml, org_id="org-1", corp_id="corp",
        ) is None

    def test_text_message_parsed(self):
        from services.wecom.callback_inbox_worker import _parse_callback_message

        xml = (
            "<xml>"
            "<MsgType><![CDATA[text]]></MsgType>"
            "<Content><![CDATA[测试内容]]></Content>"
            "<FromUserName><![CDATA[user_abc]]></FromUserName>"
            "<MsgId>67890</MsgId>"
            "</xml>"
        )

        msg = _parse_callback_message(
            xml, org_id="org-1", corp_id="corp",
        )
        assert msg is not None
        assert msg.msgid == "67890"
        assert msg.wecom_userid == "user_abc"
        assert msg.text_content == "测试内容"
        assert msg.channel == "app"
        assert msg.chattype == "single"

    def test_invalid_xml_rejected(self):
        from services.wecom.callback_inbox_worker import _parse_callback_message
        with pytest.raises(Exception):
            _parse_callback_message(
                "not xml", org_id="org-1", corp_id="corp",
            )
