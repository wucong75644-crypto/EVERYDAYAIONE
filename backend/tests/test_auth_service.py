"""
auth_service 单元测试

测试认证服务的核心功能：
- 手机号注册
- 手机号验证码登录
- 手机号密码登录
- 重置密码
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from services.auth_service import AuthService
from core.exceptions import (
    AuthenticationError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from tests.conftest import create_test_user


class TestAuthServiceRegister:
    """注册功能测试"""

    @pytest.fixture
    def auth_service(self, mock_db, mock_settings):
        """创建 AuthService 实例"""
        with patch("services.auth_service.get_settings", return_value=mock_settings):
            return AuthService(mock_db)

    @pytest.mark.asyncio
    async def test_register_success(self, auth_service, mock_db, mock_sms_service):
        """测试：注册成功"""
        # Arrange
        phone = "13800138000"
        code = "123456"
        nickname = "测试用户"

        # 设置空的用户表（手机号未注册）
        mock_db.set_table_data("users", [])

        # Mock insert 返回新用户
        new_user = create_test_user(phone=phone, nickname=nickname)
        mock_db.table("users").execute = MagicMock(
            return_value=MagicMock(data=[new_user])
        )

        # Act
        with patch.object(auth_service, "_verify_code", return_value=True):
            with patch.object(auth_service, "_subscribe_default_models", return_value=None):
                result = await auth_service.register_by_phone(phone, code, nickname)

        # Assert
        assert "token" in result
        assert "user" in result
        assert result["user"]["nickname"] == nickname

    @pytest.mark.asyncio
    async def test_register_phone_already_exists(self, auth_service, mock_db, mock_sms_service):
        """测试：手机号已注册"""
        # Arrange
        phone = "13800138000"
        existing_user = create_test_user(phone=phone)
        mock_db.set_table_data("users", [existing_user])

        # Act & Assert
        with patch.object(auth_service, "_verify_code", return_value=True):
            with pytest.raises(ConflictError) as exc_info:
                await auth_service.register_by_phone(phone, "123456")

        assert "已注册" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_register_invalid_code(self, auth_service, mock_db):
        """测试：验证码错误"""
        # Arrange
        phone = "13800138000"
        mock_db.set_table_data("users", [])

        # Act & Assert
        with patch.object(auth_service, "_verify_code", return_value=False):
            with pytest.raises(ValidationError) as exc_info:
                await auth_service.register_by_phone(phone, "wrong_code")

        assert "验证码" in str(exc_info.value)


class TestAuthServiceLoginByPhone:
    """手机号验证码登录测试"""

    @pytest.fixture
    def auth_service(self, mock_db, mock_settings):
        with patch("services.auth_service.get_settings", return_value=mock_settings):
            return AuthService(mock_db)

    @pytest.mark.asyncio
    async def test_login_by_phone_success(self, auth_service, mock_db, mock_sms_service):
        """测试：验证码登录成功"""
        # Arrange
        phone = "13800138000"
        user = create_test_user(phone=phone)
        mock_db.set_table_data("users", [user])

        # Act
        with patch.object(auth_service, "_verify_code", return_value=True):
            result = await auth_service.login_by_phone(phone, "123456")

        # Assert
        assert "token" in result
        assert "user" in result
        assert result["token"]["token_type"] == "bearer"

    @pytest.mark.asyncio
    async def test_login_by_phone_user_not_found(self, auth_service, mock_db, mock_sms_service):
        """测试：用户不存在"""
        # Arrange
        mock_db.set_table_data("users", [])

        # Act & Assert
        with patch.object(auth_service, "_verify_code", return_value=True):
            with pytest.raises(NotFoundError):
                await auth_service.login_by_phone("13800138000", "123456")

    @pytest.mark.asyncio
    async def test_login_by_phone_invalid_code(self, auth_service, mock_db):
        """测试：验证码错误"""
        # Act & Assert
        with patch.object(auth_service, "_verify_code", return_value=False):
            with pytest.raises(ValidationError) as exc_info:
                await auth_service.login_by_phone("13800138000", "wrong")

        assert "验证码" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_login_by_phone_user_disabled(self, auth_service, mock_db, mock_sms_service):
        """测试：账号已禁用"""
        # Arrange
        user = create_test_user(status="disabled")
        mock_db.set_table_data("users", [user])

        # Act & Assert
        with patch.object(auth_service, "_verify_code", return_value=True):
            with pytest.raises(AuthenticationError) as exc_info:
                await auth_service.login_by_phone(user["phone"], "123456")

        assert "禁用" in str(exc_info.value)


class TestAuthServiceLoginByPassword:
    """密码登录测试"""

    @pytest.fixture
    def auth_service(self, mock_db, mock_settings):
        with patch("services.auth_service.get_settings", return_value=mock_settings):
            return AuthService(mock_db)

    @pytest.mark.asyncio
    async def test_login_by_password_success(self, auth_service, mock_db):
        """测试：密码登录成功"""
        # Arrange
        phone = "13800138000"
        password = "password123"

        # 创建带密码的用户
        user = create_test_user(phone=phone)
        mock_db.set_table_data("users", [user])

        # Act
        with patch("services.auth_service.verify_password", return_value=True):
            result = await auth_service.login_by_password(phone, password)

        # Assert
        assert "token" in result
        assert "user" in result

    @pytest.mark.asyncio
    async def test_login_by_password_wrong_password(self, auth_service, mock_db):
        """测试：密码错误"""
        # Arrange
        user = create_test_user()
        user["password_hash"] = "hashed_password"
        mock_db.set_table_data("users", [user])

        # Act & Assert
        with patch("services.auth_service.verify_password", return_value=False):
            with pytest.raises(AuthenticationError) as exc_info:
                await auth_service.login_by_password(user["phone"], "wrong")

        assert "密码错误" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_login_by_password_no_password_set(self, auth_service, mock_db):
        """测试：未设置密码"""
        # Arrange
        user = create_test_user()
        user["password_hash"] = None
        mock_db.set_table_data("users", [user])

        # Act & Assert
        with pytest.raises(AuthenticationError) as exc_info:
            await auth_service.login_by_password(user["phone"], "any")

        assert "未设置密码" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_login_by_password_user_not_found(self, auth_service, mock_db):
        """测试：用户不存在"""
        # Arrange
        mock_db.set_table_data("users", [])

        # Act & Assert
        with pytest.raises(AuthenticationError) as exc_info:
            await auth_service.login_by_password("13800138000", "password")

        assert "密码错误" in str(exc_info.value)


class TestAuthServiceResetPassword:
    """重置密码测试"""

    @pytest.fixture
    def auth_service(self, mock_db, mock_settings):
        with patch("services.auth_service.get_settings", return_value=mock_settings):
            return AuthService(mock_db)

    @pytest.mark.asyncio
    async def test_reset_password_success(self, auth_service, mock_db, mock_sms_service):
        """测试：重置密码成功"""
        # Arrange
        user = create_test_user()
        mock_db.set_table_data("users", [user])

        # Act
        with patch.object(auth_service, "_verify_code", return_value=True):
            with patch("services.auth_service.hash_password", return_value="new_hash"):
                result = await auth_service.reset_password(
                    user["phone"], "123456", "newpassword"
                )

        # Assert
        assert "message" in result
        assert "成功" in result["message"]

    @pytest.mark.asyncio
    async def test_reset_password_user_not_found(self, auth_service, mock_db):
        """测试：用户不存在"""
        # Arrange
        mock_db.set_table_data("users", [])

        # Act & Assert
        with pytest.raises(NotFoundError):
            await auth_service.reset_password("13800138000", "123456", "newpwd")


class TestAuthServiceHelpers:
    """辅助方法测试"""

    @pytest.fixture
    def auth_service(self, mock_db, mock_settings):
        with patch("services.auth_service.get_settings", return_value=mock_settings):
            return AuthService(mock_db)

    def test_format_user_response(self, auth_service):
        """测试：用户信息格式化"""
        # Arrange
        user = create_test_user(phone="13800138000")

        # Act
        result = auth_service._format_user_response(user)

        # Assert
        assert result["id"] == user["id"]
        assert result["nickname"] == user["nickname"]
        assert result["phone"] == "138****8000"  # 手机号脱敏
        assert result["credits"] == user["credits"]

    def test_format_user_response_short_phone(self, auth_service):
        """测试：短手机号格式化"""
        # Arrange
        user = create_test_user(phone="12345")

        # Act
        result = auth_service._format_user_response(user)

        # Assert
        assert result["phone"] is None  # 手机号太短，不显示

    def test_create_token_response(self, auth_service):
        """测试：Token 响应格式"""
        # Act
        with patch("services.auth_service.create_access_token", return_value="test_token"):
            result = auth_service._create_token_response("user_123")

        # Assert
        assert result["access_token"] == "test_token"
        assert result["token_type"] == "bearer"
        assert result["expires_in"] == 1440 * 60
