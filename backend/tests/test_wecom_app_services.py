"""
企微自建应用服务单元测试

覆盖：access_token_manager（获取/缓存/重试）、
      app_message_sender（文本/Markdown 发送）
"""

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.wecom.access_token_manager import (
    _redis_key,
    get_access_token,
    _fetch_and_cache_token,
)
from services.wecom.app_message_sender import send_text, send_markdown, OrgWecomCreds

# 测试用常量
TEST_ORG_ID = "org_test"
TEST_CORP_ID = "corp_test"
TEST_SECRET = "secret_test"


def _make_creds(agent_id: int = 1000006) -> OrgWecomCreds:
    return OrgWecomCreds(
        org_id=TEST_ORG_ID,
        corp_id=TEST_CORP_ID,
        agent_id=agent_id,
        agent_secret=TEST_SECRET,
    )


# ============================================================
# TestAccessTokenManager
# ============================================================


class TestAccessTokenManager:
    """access_token 获取/缓存/重试"""

    @pytest.mark.asyncio
    async def test_returns_cached_token(self):
        """Redis 中有缓存 → 直接返回"""
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value="cached_token_abc")

        with patch(
            "services.wecom.access_token_manager.get_redis",
            new=AsyncMock(return_value=mock_redis),
        ):
            token = await get_access_token(TEST_ORG_ID, TEST_CORP_ID, TEST_SECRET)

        assert token == "cached_token_abc"
        mock_redis.get.assert_called_once_with(_redis_key(TEST_ORG_ID))

    @pytest.mark.asyncio
    async def test_fetches_on_cache_miss(self):
        """Redis 缓存未命中 → 从 API 获取"""
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        mock_redis.set = AsyncMock()

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "errcode": 0,
            "errmsg": "ok",
            "access_token": "new_token_xyz",
            "expires_in": 7200,
        }
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "services.wecom.access_token_manager.get_redis",
            new=AsyncMock(return_value=mock_redis),
        ), patch(
            "services.wecom.access_token_manager.httpx.AsyncClient",
            return_value=mock_client,
        ):
            token = await _fetch_and_cache_token(TEST_ORG_ID, TEST_CORP_ID, TEST_SECRET)

        assert token == "new_token_xyz"
        mock_redis.set.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_none_on_missing_config(self):
        """未配置 corp_id/secret → 返回 None"""
        token = await _fetch_and_cache_token(TEST_ORG_ID, None, None)

        assert token is None

    @pytest.mark.asyncio
    async def test_retries_on_api_error(self):
        """API 返回 errcode≠0 → 重试"""
        mock_resp_fail = MagicMock()
        mock_resp_fail.json.return_value = {
            "errcode": 40013, "errmsg": "invalid corpid",
        }
        mock_resp_ok = MagicMock()
        mock_resp_ok.json.return_value = {
            "errcode": 0, "access_token": "retry_ok", "expires_in": 7200,
        }

        call_count = 0

        async def mock_get(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return mock_resp_fail if call_count <= 2 else mock_resp_ok

        mock_client = AsyncMock()
        mock_client.get = mock_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock()

        with patch(
            "services.wecom.access_token_manager.get_redis",
            new=AsyncMock(return_value=mock_redis),
        ), patch(
            "services.wecom.access_token_manager.httpx.AsyncClient",
            return_value=mock_client,
        ):
            token = await _fetch_and_cache_token(
                TEST_ORG_ID, TEST_CORP_ID, TEST_SECRET, retries=3,
            )

        assert token == "retry_ok"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_returns_none_after_all_retries(self):
        """所有重试都失败 → 返回 None"""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"errcode": 40013, "errmsg": "error"}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "services.wecom.access_token_manager.get_redis",
            new=AsyncMock(return_value=None),
        ), patch(
            "services.wecom.access_token_manager.httpx.AsyncClient",
            return_value=mock_client,
        ):
            token = await _fetch_and_cache_token(
                TEST_ORG_ID, TEST_CORP_ID, TEST_SECRET, retries=2,
            )

        assert token is None


# ============================================================
# TestAppMessageSender
# ============================================================


class TestAppMessageSender:
    """消息发送 API 封装"""

    @pytest.mark.asyncio
    async def test_send_text_success(self):
        """文本消息发送成功"""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"errcode": 0, "errmsg": "ok"}

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        creds = _make_creds()

        with patch(
            "services.wecom.app_message_sender.get_access_token",
            new=AsyncMock(return_value="test_token"),
        ), patch(
            "services.wecom.app_message_sender.httpx.AsyncClient",
            return_value=mock_client,
        ):
            ok = await send_text("user123", "你好", creds)

        assert ok is True
        call_kwargs = mock_client.post.call_args
        payload = call_kwargs.kwargs["json"]
        assert payload["touser"] == "user123"
        assert payload["msgtype"] == "text"
        assert payload["text"]["content"] == "你好"

    @pytest.mark.asyncio
    async def test_send_markdown_success(self):
        """Markdown 消息发送成功"""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"errcode": 0, "errmsg": "ok"}

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        creds = _make_creds(agent_id=100)

        with patch(
            "services.wecom.app_message_sender.get_access_token",
            new=AsyncMock(return_value="test_token"),
        ), patch(
            "services.wecom.app_message_sender.httpx.AsyncClient",
            return_value=mock_client,
        ):
            ok = await send_markdown("user123", "# Hello", creds)

        assert ok is True
        payload = mock_client.post.call_args.kwargs["json"]
        assert payload["msgtype"] == "markdown"
        assert payload["markdown"]["content"] == "# Hello"

    @pytest.mark.asyncio
    async def test_send_fails_no_token(self):
        """无 token → 返回 False"""
        creds = _make_creds()
        with patch(
            "services.wecom.app_message_sender.get_access_token",
            new=AsyncMock(return_value=None),
        ):
            ok = await send_text("user123", "hi", creds)

        assert ok is False

    @pytest.mark.asyncio
    async def test_send_fails_on_api_error(self):
        """API 返回错误 → 返回 False"""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"errcode": 40003, "errmsg": "invalid userid"}

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        creds = _make_creds(agent_id=100)

        with patch(
            "services.wecom.app_message_sender.get_access_token",
            new=AsyncMock(return_value="token"),
        ), patch(
            "services.wecom.app_message_sender.httpx.AsyncClient",
            return_value=mock_client,
        ):
            ok = await send_text("user123", "hi", creds)

        assert ok is False

    @pytest.mark.asyncio
    async def test_custom_agent_id(self):
        """自定义 agent_id 传递（通过 creds）"""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"errcode": 0, "errmsg": "ok"}

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        creds = _make_creds(agent_id=999)

        with patch(
            "services.wecom.app_message_sender.get_access_token",
            new=AsyncMock(return_value="token"),
        ), patch(
            "services.wecom.app_message_sender.httpx.AsyncClient",
            return_value=mock_client,
        ):
            await send_text("user123", "hi", creds)

        payload = mock_client.post.call_args.kwargs["json"]
        assert payload["agentid"] == 999
