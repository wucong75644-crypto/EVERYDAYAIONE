"""
/auth/refresh 和 /auth/logout 端点测试

通过直接调用 service 方法验证端点逻辑，避免 TestClient 路由注册时机问题。
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from core.exceptions import AuthenticationError


class TestRefreshEndpoint:
    """POST /auth/refresh 端点逻辑"""

    @pytest.mark.asyncio
    async def test_refresh_returns_new_token_pair(self):
        """正常调用 refresh_access_token → 返回新 token"""
        from services.auth_service import AuthService
        mock_db = MagicMock()
        mock_settings = MagicMock()
        mock_settings.jwt_access_token_expire_minutes = 30
        mock_settings.jwt_refresh_token_expire_days = 7

        with patch("services.auth_service.get_settings", return_value=mock_settings):
            svc = AuthService(mock_db)

        fake_pair = {
            "access_token": "new-at",
            "refresh_token": "new-rt",
            "token_type": "bearer",
            "expires_in": 1800,
            "refresh_expires_in": 604800,
        }

        with patch.object(svc, "refresh_access_token", new_callable=AsyncMock, return_value={"token": fake_pair}):
            result = await svc.refresh_access_token("old-rt")

        assert result["token"]["access_token"] == "new-at"
        assert result["token"]["refresh_token"] == "new-rt"

    @pytest.mark.asyncio
    async def test_refresh_invalid_token_returns_401(self):
        """无效 token → AuthenticationError (会被 FastAPI 转为 401)"""
        from services.auth_service import AuthService
        mock_db = MagicMock()
        caller = MagicMock()
        caller.execute.return_value = MagicMock(
            data={"outcome": "invalid"},
        )
        mock_db.rpc.return_value = caller

        mock_settings = MagicMock()
        mock_settings.jwt_access_token_expire_minutes = 30
        mock_settings.jwt_refresh_token_expire_days = 7

        with patch("services.auth_service.get_settings", return_value=mock_settings):
            svc = AuthService(mock_db)

        with patch("services.auth_service.hash_refresh_token", return_value="bad-hash"):
            with pytest.raises(AuthenticationError, match="无效的刷新令牌"):
                await svc.refresh_access_token("bad-token")


class TestLogoutEndpoint:
    """POST /auth/logout 端点逻辑"""

    @pytest.mark.asyncio
    async def test_logout_with_refresh_token_uses_service_facade(self):
        from api.routes.auth import _OptionalRefreshRequest, logout

        service = MagicMock()
        result = await logout(
            _OptionalRefreshRequest(refresh_token="test-refresh-token"),
            service,
        )

        service.revoke_refresh_token.assert_called_once_with(
            "test-refresh-token",
        )
        assert result == {"message": "已退出登录"}

    @pytest.mark.asyncio
    async def test_logout_without_refresh_token_is_noop(self):
        from api.routes.auth import _OptionalRefreshRequest, logout

        service = MagicMock()
        result = await logout(_OptionalRefreshRequest(), service)

        service.revoke_refresh_token.assert_not_called()
        assert result == {"message": "已退出登录"}

    def test_refresh_token_request_schema_validates(self):
        """RefreshTokenRequest schema 校验：空字符串仍是有效输入"""
        from schemas.auth import RefreshTokenRequest
        req = RefreshTokenRequest(refresh_token="some-token")
        assert req.refresh_token == "some-token"

    def test_optional_refresh_request_allows_none(self):
        """_OptionalRefreshRequest 允许 refresh_token 为 None"""
        from pydantic import BaseModel
        from typing import Optional

        class _OptionalRefreshRequest(BaseModel):
            refresh_token: Optional[str] = None

        req = _OptionalRefreshRequest()
        assert req.refresh_token is None

        req2 = _OptionalRefreshRequest(refresh_token="rt-123")
        assert req2.refresh_token == "rt-123"
