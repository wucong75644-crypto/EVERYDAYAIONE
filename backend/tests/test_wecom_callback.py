"""
企微回调路由单元测试

覆盖：GET URL 验证、POST 消息接收解密、XML 解析、异步处理分发
"""

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from unittest.mock import AsyncMock, MagicMock, patch

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
    from api.routes.wecom import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)


@pytest.fixture
def mock_settings():
    """Mock 配置"""
    settings = MagicMock()
    settings.wecom_token = TOKEN
    settings.wecom_encoding_aes_key = ENCODING_AES_KEY
    settings.wecom_corp_id = CORP_ID
    settings.wecom_agent_id = 1000006
    return settings


class TestVerifyUrl:
    """GET /api/wecom/callback URL 验证"""

    def test_verify_url_success(self, client, crypt, mock_settings):
        """正确签名 → 返回解密后的 echostr"""
        # 加密 echostr
        ret, encrypted_echo = crypt._encrypt("echo_test_12345")
        assert ret == 0

        # 计算签名
        timestamp, nonce = "1234567890", "nonce_abc"
        ret, signature = crypt._compute_signature(timestamp, nonce, encrypted_echo)
        assert ret == 0

        with patch(
            "api.routes.wecom.get_settings", return_value=mock_settings,
        ):
            resp = client.get(
                "/api/wecom/callback",
                params={
                    "msg_signature": signature,
                    "timestamp": timestamp,
                    "nonce": nonce,
                    "echostr": encrypted_echo,
                },
            )

        assert resp.status_code == 200
        assert resp.text == "echo_test_12345"

    def test_verify_url_bad_signature(self, client, crypt, mock_settings):
        """签名错误 → 403"""
        ret, encrypted_echo = crypt._encrypt("echo")
        assert ret == 0

        with patch(
            "api.routes.wecom.get_settings", return_value=mock_settings,
        ):
            resp = client.get(
                "/api/wecom/callback",
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
        resp = client.get("/api/wecom/callback")
        assert resp.status_code == 422


class TestReceiveMessage:
    """POST /api/wecom/callback 消息接收"""

    def test_receive_text_message(self, client, crypt, mock_settings):
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

        with patch(
            "api.routes.wecom.get_settings", return_value=mock_settings,
        ), patch(
            "api.routes.wecom.WecomMessageService",
        ) as mock_svc_cls:
            # Mock WecomMessageService 防止实际调用
            mock_svc_cls.return_value.handle_message = AsyncMock()

            resp = client.post(
                "/api/wecom/callback",
                params={
                    "msg_signature": signature,
                    "timestamp": timestamp,
                    "nonce": nonce,
                },
                content=post_xml,
            )

        assert resp.status_code == 200
        assert resp.text == "success"

    def test_receive_bad_signature(self, client, crypt, mock_settings):
        """签名错误 → 403"""
        ret, encrypted = crypt._encrypt("<xml>test</xml>")
        post_xml = f"<xml><Encrypt><![CDATA[{encrypted}]]></Encrypt></xml>"

        with patch(
            "api.routes.wecom.get_settings", return_value=mock_settings,
        ):
            resp = client.post(
                "/api/wecom/callback",
                params={
                    "msg_signature": "bad_sig",
                    "timestamp": "123",
                    "nonce": "abc",
                },
                content=post_xml,
            )

        assert resp.status_code == 403

    def test_receive_invalid_xml(self, client, mock_settings):
        """无效 XML → 403"""
        with patch(
            "api.routes.wecom.get_settings", return_value=mock_settings,
        ):
            resp = client.post(
                "/api/wecom/callback",
                params={
                    "msg_signature": "sig",
                    "timestamp": "123",
                    "nonce": "abc",
                },
                content="not xml at all",
            )

        assert resp.status_code == 403


class TestProcessCallbackXml:
    """_process_callback_xml 内部逻辑"""

    @pytest.mark.asyncio
    async def test_event_type_skipped(self):
        """event 类型消息被跳过"""
        from api.routes.wecom import _process_callback_xml

        xml = (
            "<xml>"
            "<MsgType><![CDATA[event]]></MsgType>"
            "<Event><![CDATA[subscribe]]></Event>"
            "</xml>"
        )

        # 不应抛出异常
        with patch(
            "api.routes.wecom.get_settings",
            return_value=MagicMock(wecom_corp_id="corp", wecom_agent_id=100),
        ):
            await _process_callback_xml(xml, MagicMock())

    @pytest.mark.asyncio
    async def test_text_message_dispatched(self):
        """文本消息 → 构建 WecomIncomingMessage → 调用 handle_message"""
        from api.routes.wecom import _process_callback_xml

        xml = (
            "<xml>"
            "<MsgType><![CDATA[text]]></MsgType>"
            "<Content><![CDATA[测试内容]]></Content>"
            "<FromUserName><![CDATA[user_abc]]></FromUserName>"
            "<MsgId>67890</MsgId>"
            "</xml>"
        )

        mock_svc = MagicMock()
        mock_svc.handle_message = AsyncMock()

        with patch(
            "api.routes.wecom.get_settings",
            return_value=MagicMock(wecom_corp_id="corp", wecom_agent_id=100),
        ), patch(
            "api.routes.wecom.WecomMessageService",
            return_value=mock_svc,
        ):
            await _process_callback_xml(xml, MagicMock())

        mock_svc.handle_message.assert_called_once()
        msg = mock_svc.handle_message.call_args[0][0]
        assert msg.msgid == "67890"
        assert msg.wecom_userid == "user_abc"
        assert msg.text_content == "测试内容"
        assert msg.channel == "app"
        assert msg.chattype == "single"

    @pytest.mark.asyncio
    async def test_invalid_xml_handled(self):
        """无效 XML → 记录日志但不抛出"""
        from api.routes.wecom import _process_callback_xml
        # 不应抛出异常
        await _process_callback_xml("not xml", MagicMock())
