"""
app_message_sender 单元测试

覆盖：send_text / send_markdown_v2 / send_image / send_video payload 构建，
      upload_temp_media 成功/失败/无token，_send 错误处理
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import httpx
import pytest

# ============================================================
# TestSend — 底层 _send 函数
# ============================================================


class TestSend:
    """_send 函数行为"""

    @pytest.mark.asyncio
    async def test_send_success(self):
        """API 返回 errcode=0 → True"""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"errcode": 0, "errmsg": "ok"}

        with patch(
            "services.wecom.app_message_sender.get_access_token",
            new=AsyncMock(return_value="token123"),
        ), patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            from services.wecom.app_message_sender import _send

            result = await _send({"touser": "u1", "msgtype": "text"})
            assert result is True

    @pytest.mark.asyncio
    async def test_send_no_token(self):
        """无 access_token → False"""
        with patch(
            "services.wecom.app_message_sender.get_access_token",
            new=AsyncMock(return_value=None),
        ):
            from services.wecom.app_message_sender import _send

            result = await _send({"touser": "u1"})
            assert result is False

    @pytest.mark.asyncio
    async def test_send_api_error(self):
        """API 返回非 0 errcode → False"""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"errcode": 40001, "errmsg": "invalid token"}

        with patch(
            "services.wecom.app_message_sender.get_access_token",
            new=AsyncMock(return_value="token123"),
        ), patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            from services.wecom.app_message_sender import _send

            result = await _send({"touser": "u1"})
            assert result is False

    @pytest.mark.asyncio
    async def test_send_http_exception(self):
        """HTTP 请求异常 → False"""
        with patch(
            "services.wecom.app_message_sender.get_access_token",
            new=AsyncMock(return_value="token123"),
        ), patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.side_effect = httpx.ConnectError("connection refused")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            from services.wecom.app_message_sender import _send

            result = await _send({"touser": "u1"})
            assert result is False


# ============================================================
# TestSendFunctions — 各消息类型 payload 构建
# ============================================================


class TestSendFunctions:
    """send_text / send_markdown_v2 / send_image / send_video"""

    @pytest.mark.asyncio
    async def test_send_text_payload(self):
        """send_text 构建正确的 payload"""
        with patch(
            "services.wecom.app_message_sender._send",
            new=AsyncMock(return_value=True),
        ) as mock_send:
            from services.wecom.app_message_sender import send_text

            await send_text("user1", "hello", agent_id=1000)

            payload = mock_send.call_args[0][0]
            assert payload["touser"] == "user1"
            assert payload["msgtype"] == "text"
            assert payload["agentid"] == 1000
            assert payload["text"]["content"] == "hello"

    @pytest.mark.asyncio
    async def test_send_markdown_v2_payload(self):
        """send_markdown_v2 构建正确的 payload"""
        with patch(
            "services.wecom.app_message_sender._send",
            new=AsyncMock(return_value=True),
        ) as mock_send:
            from services.wecom.app_message_sender import send_markdown_v2

            await send_markdown_v2("user1", "# 标题", agent_id=2000)

            payload = mock_send.call_args[0][0]
            assert payload["touser"] == "user1"
            assert payload["msgtype"] == "markdown_v2"
            assert payload["agentid"] == 2000
            assert payload["markdown_v2"]["content"] == "# 标题"

    @pytest.mark.asyncio
    async def test_send_image_payload(self):
        """send_image 构建正确的 payload"""
        with patch(
            "services.wecom.app_message_sender._send",
            new=AsyncMock(return_value=True),
        ) as mock_send:
            from services.wecom.app_message_sender import send_image

            await send_image("user1", "media_abc", agent_id=3000)

            payload = mock_send.call_args[0][0]
            assert payload["touser"] == "user1"
            assert payload["msgtype"] == "image"
            assert payload["agentid"] == 3000
            assert payload["image"]["media_id"] == "media_abc"

    @pytest.mark.asyncio
    async def test_send_video_payload(self):
        """send_video 构建正确的 payload"""
        with patch(
            "services.wecom.app_message_sender._send",
            new=AsyncMock(return_value=True),
        ) as mock_send:
            from services.wecom.app_message_sender import send_video

            await send_video(
                "user1", "media_vid", title="T", description="D", agent_id=4000
            )

            payload = mock_send.call_args[0][0]
            assert payload["touser"] == "user1"
            assert payload["msgtype"] == "video"
            assert payload["agentid"] == 4000
            assert payload["video"]["media_id"] == "media_vid"
            assert payload["video"]["title"] == "T"
            assert payload["video"]["description"] == "D"

    @pytest.mark.asyncio
    async def test_send_text_default_agent_id(self):
        """agent_id=None → 从配置读取"""
        with patch(
            "services.wecom.app_message_sender._send",
            new=AsyncMock(return_value=True),
        ) as mock_send, patch(
            "services.wecom.app_message_sender.get_settings",
        ) as mock_settings:
            mock_settings.return_value.wecom_agent_id = 9999

            from services.wecom.app_message_sender import send_text

            await send_text("user1", "hi")

            payload = mock_send.call_args[0][0]
            assert payload["agentid"] == 9999


# ============================================================
# TestUploadTempMedia
# ============================================================


class TestUploadTempMedia:
    """upload_temp_media 上传临时素材"""

    @pytest.mark.asyncio
    async def test_upload_success(self):
        """下载+上传成功 → 返回 media_id"""
        dl_resp = MagicMock()
        dl_resp.content = b"fake_image_bytes"
        dl_resp.raise_for_status = MagicMock()
        dl_resp.headers = {"content-type": "image/png"}

        upload_resp = MagicMock()
        upload_resp.json.return_value = {"errcode": 0, "media_id": "mid123"}

        with patch(
            "services.wecom.app_message_sender.get_access_token",
            new=AsyncMock(return_value="token123"),
        ), patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = dl_resp
            mock_client.post.return_value = upload_resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            from services.wecom.app_message_sender import upload_temp_media

            result = await upload_temp_media("https://example.com/img.png", "image")
            assert result == "mid123"

    @pytest.mark.asyncio
    async def test_upload_no_token(self):
        """无 access_token → None"""
        with patch(
            "services.wecom.app_message_sender.get_access_token",
            new=AsyncMock(return_value=None),
        ):
            from services.wecom.app_message_sender import upload_temp_media

            result = await upload_temp_media("https://example.com/img.png")
            assert result is None

    @pytest.mark.asyncio
    async def test_upload_api_error(self):
        """企微 API 返回错误 → None"""
        dl_resp = MagicMock()
        dl_resp.content = b"bytes"
        dl_resp.raise_for_status = MagicMock()
        dl_resp.headers = {}

        upload_resp = MagicMock()
        upload_resp.json.return_value = {"errcode": 40004, "errmsg": "invalid media"}

        with patch(
            "services.wecom.app_message_sender.get_access_token",
            new=AsyncMock(return_value="token123"),
        ), patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = dl_resp
            mock_client.post.return_value = upload_resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            from services.wecom.app_message_sender import upload_temp_media

            result = await upload_temp_media("https://example.com/img.png")
            assert result is None

    @pytest.mark.asyncio
    async def test_upload_download_exception(self):
        """下载文件异常 → None"""
        with patch(
            "services.wecom.app_message_sender.get_access_token",
            new=AsyncMock(return_value="token123"),
        ), patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.side_effect = httpx.ConnectError("fail")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            from services.wecom.app_message_sender import upload_temp_media

            result = await upload_temp_media("https://example.com/img.png")
            assert result is None

    @pytest.mark.asyncio
    async def test_upload_video_timeout(self):
        """video 类型使用 60s 超时"""
        dl_resp = MagicMock()
        dl_resp.content = b"video_bytes"
        dl_resp.raise_for_status = MagicMock()
        dl_resp.headers = {}

        upload_resp = MagicMock()
        upload_resp.json.return_value = {"errcode": 0, "media_id": "vid1"}

        with patch(
            "services.wecom.app_message_sender.get_access_token",
            new=AsyncMock(return_value="token123"),
        ), patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = dl_resp
            mock_client.post.return_value = upload_resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            from services.wecom.app_message_sender import upload_temp_media

            result = await upload_temp_media("https://example.com/vid.mp4", "video")
            assert result == "vid1"
            # 检查 timeout=60.0
            call_kwargs = mock_client_cls.call_args
            assert call_kwargs[1].get("timeout") == 60.0 or call_kwargs[0] == (60.0,)
