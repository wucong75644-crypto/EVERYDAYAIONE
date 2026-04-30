"""
core/exceptions.py 单元测试

覆盖：ConfigurationError 属性验证（code/status_code/message/details）
"""

import pytest

from core.exceptions import (
    AppException,
    ConfigurationError,
    ExternalServiceError,
    ValidationError,
)


class TestConfigurationError:
    """ConfigurationError 异常属性验证"""

    def test_inherits_app_exception(self):
        err = ConfigurationError("KIE")
        assert isinstance(err, AppException)

    def test_default_message(self):
        err = ConfigurationError("KIE")
        assert err.message == "该功能暂未开通，请联系管理员"

    def test_custom_message(self):
        err = ConfigurationError("OpenRouter", "模型供应商暂未支持")
        assert err.message == "模型供应商暂未支持"

    def test_status_code_500(self):
        err = ConfigurationError("KIE")
        assert err.status_code == 500

    def test_error_code(self):
        err = ConfigurationError("KIE")
        assert err.code == "SERVICE_NOT_CONFIGURED"

    def test_details_contains_service(self):
        err = ConfigurationError("DashScope")
        assert err.details == {"service": "DashScope"}

    def test_str_representation(self):
        err = ConfigurationError("Google")
        assert "该功能暂未开通" in str(err)


class TestExceptionHierarchy:
    """验证异常层级关系"""

    def test_configuration_error_caught_by_app_exception(self):
        with pytest.raises(AppException):
            raise ConfigurationError("KIE")

    def test_validation_error_caught_by_app_exception(self):
        with pytest.raises(AppException):
            raise ValidationError("参数错误")

    def test_external_service_error_caught_by_app_exception(self):
        with pytest.raises(AppException):
            raise ExternalServiceError("Redis", "不可用")

    def test_configuration_error_not_caught_by_value_error(self):
        """ConfigurationError 不是 ValueError，不会被 except ValueError 捕获"""
        with pytest.raises(ConfigurationError):
            try:
                raise ConfigurationError("KIE")
            except ValueError:
                pytest.fail("ConfigurationError should not be caught as ValueError")
