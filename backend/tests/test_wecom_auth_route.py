"""
wecom_auth 路由单元测试

覆盖：/qr-url、/callback、/binding、/binding-status
"""

import base64
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from api.routes.wecom_auth import _classify_error


class TestClassifyError:
    """_classify_error 错误分类"""

    def test_state_invalid(self):
        assert _classify_error("state 无效或已过期") == "state_invalid"

    def test_not_member(self):
        assert _classify_error("仅限企业成员使用") == "not_member"

    def test_user_disabled(self):
        assert _classify_error("账号已被禁用") == "user_disabled"

    def test_already_bound(self):
        assert _classify_error("该账号已绑定其他企微用户") == "already_bound"

    def test_generic_error(self):
        assert _classify_error("unknown error") == "api_error"


class TestGetQrUrl:
    """GET /auth/wecom/qr-url 测试"""

    @pytest.mark.asyncio
    async def test_returns_qr_url_for_login(self):
        """未登录 → 返回 login 模式 QR URL"""
        from api.routes.wecom_auth import get_qr_url

        mock_svc = MagicMock()
        mock_svc.generate_state = AsyncMock(return_value="state_123")
        mock_svc.build_qr_url.return_value = {
            "qr_url": "https://login.work.weixin.qq.com/...",
            "state": "state_123",
            "appid": "ww_corp",
            "agentid": "1000006",
            "redirect_uri": "https://example.com/api/auth/wecom/callback",
        }

        settings_mock = MagicMock()
        settings_mock.wecom_corp_id = "ww_corp"
        settings_mock.wecom_agent_id = 1000006
        settings_mock.wecom_oauth_redirect_uri = "https://example.com/api/auth/wecom/callback"

        with patch("api.routes.wecom_auth.get_settings", return_value=settings_mock):
            result = await get_qr_url(user_id=None, svc=mock_svc)

        assert result["state"] == "state_123"
        mock_svc.generate_state.assert_called_once_with("login", user_id=None)

    @pytest.mark.asyncio
    async def test_returns_qr_url_for_bind(self):
        """已登录 → 返回 bind 模式 QR URL"""
        from api.routes.wecom_auth import get_qr_url

        mock_svc = MagicMock()
        mock_svc.generate_state = AsyncMock(return_value="state_bind")
        mock_svc.build_qr_url.return_value = {"state": "state_bind"}

        settings_mock = MagicMock()
        settings_mock.wecom_corp_id = "ww_corp"
        settings_mock.wecom_agent_id = 1000006
        settings_mock.wecom_oauth_redirect_uri = "https://example.com/callback"

        with patch("api.routes.wecom_auth.get_settings", return_value=settings_mock):
            result = await get_qr_url(user_id="uid-123", svc=mock_svc)

        mock_svc.generate_state.assert_called_once_with("bind", user_id="uid-123")


class TestOAuthCallback:
    """GET /auth/wecom/callback 测试"""

    @pytest.mark.asyncio
    async def test_success_redirects_with_token(self):
        """登录成功 → 302 重定向到前端（带 token+user）"""
        from api.routes.wecom_auth import oauth_callback

        mock_svc = AsyncMock()
        mock_svc.validate_state.return_value = {"type": "login", "user_id": None}
        mock_svc.exchange_code.return_value = {"userid": "zhangsan", "user_ticket": None}
        mock_svc.login_or_create.return_value = {
            "token": {"access_token": "jwt_abc", "token_type": "bearer", "expires_in": 86400},
            "user": {"id": "uid-1", "nickname": "张三"},
        }

        settings_mock = MagicMock()
        settings_mock.frontend_url = "https://example.com"

        with patch("api.routes.wecom_auth.get_settings", return_value=settings_mock):
            result = await oauth_callback(code="auth_code", state="state_123", svc=mock_svc)

        assert result.status_code == 302
        location = result.headers["location"]
        assert "https://example.com/auth/wecom/callback?" in location
        assert "token=" in location
        assert "user=" in location

        # 验证 base64 解码正确
        token_b64 = location.split("token=")[1].split("&")[0]
        token_data = json.loads(base64.b64decode(token_b64))
        assert token_data["access_token"] == "jwt_abc"

    @pytest.mark.asyncio
    async def test_invalid_state_redirects_with_error(self):
        """state 无效 → 302 重定向到前端（带 error）"""
        from api.routes.wecom_auth import oauth_callback

        mock_svc = AsyncMock()
        mock_svc.validate_state.side_effect = ValueError("state 无效或已过期")

        settings_mock = MagicMock()
        settings_mock.frontend_url = "https://example.com"

        with patch("api.routes.wecom_auth.get_settings", return_value=settings_mock):
            result = await oauth_callback(code="any", state="bad", svc=mock_svc)

        assert result.status_code == 302
        assert "error=state_invalid" in result.headers["location"]

    @pytest.mark.asyncio
    async def test_non_member_redirects_with_error(self):
        """非企业成员 → 302 重定向到前端（带 error）"""
        from api.routes.wecom_auth import oauth_callback

        mock_svc = AsyncMock()
        mock_svc.validate_state.return_value = {"type": "login", "user_id": None}
        mock_svc.exchange_code.side_effect = ValueError("仅限企业成员使用扫码登录")

        settings_mock = MagicMock()
        settings_mock.frontend_url = "https://example.com"

        with patch("api.routes.wecom_auth.get_settings", return_value=settings_mock):
            result = await oauth_callback(code="ext_code", state="ok", svc=mock_svc)

        assert result.status_code == 302
        assert "error=not_member" in result.headers["location"]

    @pytest.mark.asyncio
    async def test_bind_mode_calls_bind_account(self):
        """bind 模式 → 调用 bind_account"""
        from api.routes.wecom_auth import oauth_callback

        mock_svc = AsyncMock()
        mock_svc.validate_state.return_value = {"type": "bind", "user_id": "uid-bind"}
        mock_svc.exchange_code.return_value = {"userid": "wecom_user", "user_ticket": None}
        mock_svc.bind_account.return_value = {
            "token": {"access_token": "jwt_bind", "token_type": "bearer", "expires_in": 86400},
            "user": {"id": "uid-bind", "nickname": "绑定用户"},
        }

        settings_mock = MagicMock()
        settings_mock.frontend_url = "https://example.com"

        with patch("api.routes.wecom_auth.get_settings", return_value=settings_mock):
            result = await oauth_callback(code="bind_code", state="state_bind", svc=mock_svc)

        assert result.status_code == 302
        mock_svc.bind_account.assert_called_once_with(
            user_id="uid-bind", wecom_userid="wecom_user"
        )


class TestUnbindWecom:
    """DELETE /auth/wecom/binding 测试"""

    @pytest.mark.asyncio
    async def test_unbind_success(self):
        """解绑成功"""
        from api.routes.wecom_auth import unbind_wecom

        mock_svc = AsyncMock()
        mock_svc.unbind_account.return_value = {"success": True, "message": "企微账号已解绑"}

        result = await unbind_wecom(user_id="uid-1", svc=mock_svc)
        assert result["success"] is True
        mock_svc.unbind_account.assert_called_once_with("uid-1")

    @pytest.mark.asyncio
    async def test_unbind_not_bound_raises_404(self):
        """未绑定 → HTTPException 404"""
        from api.routes.wecom_auth import unbind_wecom
        from fastapi import HTTPException

        mock_svc = AsyncMock()
        mock_svc.unbind_account.side_effect = ValueError("当前账号未绑定企微")

        with pytest.raises(HTTPException) as exc_info:
            await unbind_wecom(user_id="uid-1", svc=mock_svc)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_unbind_only_method_raises_400(self):
        """唯一登录方式 → HTTPException 400"""
        from api.routes.wecom_auth import unbind_wecom
        from fastapi import HTTPException

        mock_svc = AsyncMock()
        mock_svc.unbind_account.side_effect = ValueError("解绑后将无法登录")

        with pytest.raises(HTTPException) as exc_info:
            await unbind_wecom(user_id="uid-1", svc=mock_svc)
        assert exc_info.value.status_code == 400


class TestGetBindingStatus:
    """GET /auth/wecom/binding-status 测试"""

    @pytest.mark.asyncio
    async def test_returns_bound_status(self):
        """已绑定 → 返回详情"""
        from api.routes.wecom_auth import get_binding_status

        mock_svc = AsyncMock()
        mock_svc.get_binding_status.return_value = {
            "bound": True,
            "wecom_nickname": "张三",
            "bound_at": "2026-03-22T00:00:00+08:00",
        }

        result = await get_binding_status(user_id="uid-1", svc=mock_svc)
        assert result["bound"] is True
        assert result["wecom_nickname"] == "张三"

    @pytest.mark.asyncio
    async def test_returns_unbound_status(self):
        """未绑定 → bound=False"""
        from api.routes.wecom_auth import get_binding_status

        mock_svc = AsyncMock()
        mock_svc.get_binding_status.return_value = {
            "bound": False,
            "wecom_nickname": None,
            "bound_at": None,
        }

        result = await get_binding_status(user_id="uid-1", svc=mock_svc)
        assert result["bound"] is False
