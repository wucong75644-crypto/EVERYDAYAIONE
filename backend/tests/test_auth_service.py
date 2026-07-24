"""AuthService 验证码与响应格式化测试。"""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from core.exceptions import AppException, ValidationError
from services.auth_service import AuthService
from testing.auth_test_support import auth_user


@pytest.fixture
def auth_service(mock_settings):
    with patch("services.auth_service.get_settings", return_value=mock_settings):
        return AuthService(MagicMock())


@pytest.mark.asyncio
async def test_send_code_success(auth_service, mock_sms_service):
    result = await auth_service.send_verification_code(
        "13800138000", "login",
    )

    assert result is True
    mock_sms_service.send_verification_code.assert_awaited_once_with(
        "13800138000", "login",
    )


@pytest.mark.asyncio
async def test_send_code_provider_failure_is_mapped(
    auth_service, mock_sms_service,
):
    mock_sms_service.send_verification_code.side_effect = RuntimeError("down")

    with pytest.raises(AppException) as exc_info:
        await auth_service.send_verification_code("13800138000", "register")

    assert exc_info.value.code == "SMS_SEND_ERROR"


@pytest.mark.asyncio
async def test_send_code_business_error_passes_through(
    auth_service, mock_sms_service,
):
    mock_sms_service.send_verification_code.side_effect = ValidationError(
        "频率过高",
    )

    with pytest.raises(ValidationError, match="频率过高"):
        await auth_service.send_verification_code("13800138000", "login")


@pytest.mark.asyncio
async def test_verify_code_only_success(auth_service):
    with patch.object(
        auth_service, "_verify_code", new=AsyncMock(return_value=True),
    ):
        assert await auth_service.verify_code_only(
            "13800138000", "123456", "reset_password",
        )


@pytest.mark.asyncio
async def test_verify_code_only_invalid(auth_service):
    with patch.object(
        auth_service, "_verify_code", new=AsyncMock(return_value=False),
    ):
        with pytest.raises(ValidationError, match="验证码"):
            await auth_service.verify_code_only(
                "13800138000", "wrong", "reset_password",
            )


def test_format_user_response_masks_phone_and_detects_wecom(auth_service):
    result = auth_service._format_user_response(
        auth_user(login_methods=["phone", "wecom"]),
    )

    assert result["phone"] == "138****8000"
    assert result["wecom_bound"] is True


def test_format_user_response_handles_short_phone(auth_service):
    result = auth_service._format_user_response(auth_user(phone="12345"))

    assert result["phone"] is None


def test_format_user_response_handles_missing_login_methods(auth_service):
    result = auth_service._format_user_response(
        auth_user(login_methods=None),
    )

    assert result["wecom_bound"] is False
