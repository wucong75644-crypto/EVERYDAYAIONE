"""
认证服务单元测试

测试 AuthService 的核心功能：注册、登录、验证码、密码重置。
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.auth_service import AuthService
from core.exceptions import AuthenticationError, ConflictError, NotFoundError, ValidationError


class TestAuthServiceRegister:
    """注册功能测试"""

    @pytest.mark.asyncio
    async def test_register_success(self, mock_db: MagicMock, mock_user: dict) -> None:
        """测试正常注册流程"""
        service = AuthService(mock_db)

        # Mock 验证码验证
        with patch.object(service, "_verify_code", new_callable=AsyncMock) as mock_verify:
            mock_verify.return_value = True

            # Mock 数据库查询（手机号未注册）
            mock_db.table = MagicMock(return_value=MagicMock(
                select=MagicMock(return_value=MagicMock(
                    eq=MagicMock(return_value=MagicMock(
                        execute=MagicMock(return_value=MagicMock(data=[]))
                    ))
                ))
            ))

            # Mock 插入用户
            mock_insert_result = MagicMock()
            mock_insert_result.data = [mock_user]

            def mock_table(name: str) -> MagicMock:
                table_mock = MagicMock()
                if name == "users":
                    table_mock.select.return_value.eq.return_value.execute.return_value.data = []
                    table_mock.insert.return_value.execute.return_value = mock_insert_result
                elif name == "credits_history":
                    table_mock.insert.return_value.execute.return_value = MagicMock(data=[{}])
                elif name == "models":
                    table_mock.select.return_value.eq.return_value.execute.return_value.data = []
                elif name == "user_subscriptions":
                    table_mock.insert.return_value.execute.return_value = MagicMock(data=[])
                return table_mock

            mock_db.table = mock_table

            # Mock _subscribe_default_models
            with patch.object(service, "_subscribe_default_models", new_callable=AsyncMock):
                result = await service.register_by_phone(
                    phone="13800138000",
                    code="123456",
                    nickname="测试用户",
                )

        assert "token" in result
        assert "user" in result
        assert result["user"]["phone"] == "138****8000"

    @pytest.mark.asyncio
    async def test_register_invalid_code(self, mock_db: MagicMock) -> None:
        """测试验证码错误"""
        service = AuthService(mock_db)

        with patch.object(service, "_verify_code", new_callable=AsyncMock) as mock_verify:
            mock_verify.return_value = False

            with pytest.raises(ValidationError) as exc_info:
                await service.register_by_phone(
                    phone="13800138000",
                    code="000000",
                )

            assert "验证码错误" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_register_phone_exists(self, mock_db: MagicMock, mock_user: dict) -> None:
        """测试手机号已注册"""
        service = AuthService(mock_db)

        with patch.object(service, "_verify_code", new_callable=AsyncMock) as mock_verify:
            mock_verify.return_value = True

            # Mock 数据库返回已存在的用户
            mock_db.table = MagicMock(return_value=MagicMock(
                select=MagicMock(return_value=MagicMock(
                    eq=MagicMock(return_value=MagicMock(
                        execute=MagicMock(return_value=MagicMock(data=[mock_user]))
                    ))
                ))
            ))

            with pytest.raises(ConflictError) as exc_info:
                await service.register_by_phone(
                    phone="13800138000",
                    code="123456",
                )

            assert "已注册" in str(exc_info.value)


class TestAuthServiceLogin:
    """登录功能测试"""

    @pytest.mark.asyncio
    async def test_login_by_phone_success(self, mock_db: MagicMock, mock_user: dict) -> None:
        """测试验证码登录成功"""
        service = AuthService(mock_db)

        with patch.object(service, "_verify_code", new_callable=AsyncMock) as mock_verify:
            mock_verify.return_value = True

            # Mock 数据库查询返回用户
            mock_db.table = MagicMock(return_value=MagicMock(
                select=MagicMock(return_value=MagicMock(
                    eq=MagicMock(return_value=MagicMock(
                        execute=MagicMock(return_value=MagicMock(data=[mock_user]))
                    ))
                )),
                update=MagicMock(return_value=MagicMock(
                    eq=MagicMock(return_value=MagicMock(
                        execute=MagicMock(return_value=MagicMock(data=[mock_user]))
                    ))
                ))
            ))

            result = await service.login_by_phone(
                phone="13800138000",
                code="123456",
            )

        assert "token" in result
        assert "user" in result

    @pytest.mark.asyncio
    async def test_login_by_phone_user_not_found(self, mock_db: MagicMock) -> None:
        """测试用户不存在"""
        service = AuthService(mock_db)

        with patch.object(service, "_verify_code", new_callable=AsyncMock) as mock_verify:
            mock_verify.return_value = True

            mock_db.table = MagicMock(return_value=MagicMock(
                select=MagicMock(return_value=MagicMock(
                    eq=MagicMock(return_value=MagicMock(
                        execute=MagicMock(return_value=MagicMock(data=[]))
                    ))
                ))
            ))

            with pytest.raises(NotFoundError):
                await service.login_by_phone(
                    phone="13800138000",
                    code="123456",
                )

    @pytest.mark.asyncio
    async def test_login_by_phone_account_disabled(self, mock_db: MagicMock, mock_user: dict) -> None:
        """测试账号被禁用"""
        service = AuthService(mock_db)
        mock_user["status"] = "disabled"

        with patch.object(service, "_verify_code", new_callable=AsyncMock) as mock_verify:
            mock_verify.return_value = True

            mock_db.table = MagicMock(return_value=MagicMock(
                select=MagicMock(return_value=MagicMock(
                    eq=MagicMock(return_value=MagicMock(
                        execute=MagicMock(return_value=MagicMock(data=[mock_user]))
                    ))
                ))
            ))

            with pytest.raises(AuthenticationError) as exc_info:
                await service.login_by_phone(
                    phone="13800138000",
                    code="123456",
                )

            assert "禁用" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_login_by_password_success(self, mock_db: MagicMock, mock_user: dict) -> None:
        """测试密码登录成功"""
        service = AuthService(mock_db)

        with patch("services.auth_service.verify_password") as mock_verify_pwd:
            mock_verify_pwd.return_value = True

            mock_db.table = MagicMock(return_value=MagicMock(
                select=MagicMock(return_value=MagicMock(
                    eq=MagicMock(return_value=MagicMock(
                        execute=MagicMock(return_value=MagicMock(data=[mock_user]))
                    ))
                )),
                update=MagicMock(return_value=MagicMock(
                    eq=MagicMock(return_value=MagicMock(
                        execute=MagicMock(return_value=MagicMock(data=[mock_user]))
                    ))
                ))
            ))

            result = await service.login_by_password(
                phone="13800138000",
                password="test123456",
            )

        assert "token" in result
        assert "user" in result

    @pytest.mark.asyncio
    async def test_login_by_password_wrong_password(self, mock_db: MagicMock, mock_user: dict) -> None:
        """测试密码错误"""
        service = AuthService(mock_db)

        with patch("services.auth_service.verify_password") as mock_verify_pwd:
            mock_verify_pwd.return_value = False

            mock_db.table = MagicMock(return_value=MagicMock(
                select=MagicMock(return_value=MagicMock(
                    eq=MagicMock(return_value=MagicMock(
                        execute=MagicMock(return_value=MagicMock(data=[mock_user]))
                    ))
                ))
            ))

            with pytest.raises(AuthenticationError) as exc_info:
                await service.login_by_password(
                    phone="13800138000",
                    password="wrongpassword",
                )

            assert "密码错误" in str(exc_info.value)


class TestAuthServiceResetPassword:
    """密码重置功能测试"""

    @pytest.mark.asyncio
    async def test_reset_password_success(self, mock_db: MagicMock, mock_user: dict) -> None:
        """测试密码重置成功"""
        service = AuthService(mock_db)

        with patch.object(service, "_verify_code", new_callable=AsyncMock) as mock_verify:
            mock_verify.return_value = True

            mock_db.table = MagicMock(return_value=MagicMock(
                select=MagicMock(return_value=MagicMock(
                    eq=MagicMock(return_value=MagicMock(
                        execute=MagicMock(return_value=MagicMock(data=[mock_user]))
                    ))
                )),
                update=MagicMock(return_value=MagicMock(
                    eq=MagicMock(return_value=MagicMock(
                        execute=MagicMock(return_value=MagicMock(data=[mock_user]))
                    ))
                ))
            ))

            result = await service.reset_password(
                phone="13800138000",
                code="123456",
                new_password="newpassword123",
            )

        assert result["message"] == "密码重置成功"

    @pytest.mark.asyncio
    async def test_reset_password_user_not_found(self, mock_db: MagicMock) -> None:
        """测试用户不存在"""
        service = AuthService(mock_db)

        mock_db.table = MagicMock(return_value=MagicMock(
            select=MagicMock(return_value=MagicMock(
                eq=MagicMock(return_value=MagicMock(
                    execute=MagicMock(return_value=MagicMock(data=[]))
                ))
            ))
        ))

        with pytest.raises(NotFoundError):
            await service.reset_password(
                phone="13800138000",
                code="123456",
                new_password="newpassword123",
            )


class TestAuthServiceHelpers:
    """辅助方法测试"""

    def test_format_user_response(self, mock_db: MagicMock, mock_user: dict) -> None:
        """测试用户响应格式化"""
        service = AuthService(mock_db)
        result = service._format_user_response(mock_user)

        assert result["id"] == mock_user["id"]
        assert result["nickname"] == mock_user["nickname"]
        assert result["phone"] == "138****8000"  # 手机号脱敏
        assert result["role"] == mock_user["role"]
        assert result["credits"] == mock_user["credits"]

    def test_format_user_response_short_phone(self, mock_db: MagicMock, mock_user: dict) -> None:
        """测试短手机号格式化"""
        service = AuthService(mock_db)
        mock_user["phone"] = "123"  # 短手机号
        result = service._format_user_response(mock_user)

        assert result["phone"] is None  # 太短无法脱敏
