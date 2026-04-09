"""
错误分类器 (core/error_classifier.py) 单元测试

覆盖 classify_error 对每种异常类型的分类正确性。
"""

import asyncio

import httpx
import pytest

from core.error_classifier import ClassifiedError, ErrorCategory, classify_error


# ============================================================
# 1. Supabase DB 错误 → INFRA
# ============================================================


class TestDBErrors:
    """PostgREST APIError → INFRA, 不可重试, 不记熔断"""

    def test_postgrest_api_error(self):
        from postgrest.exceptions import APIError as PostgrestAPIError

        err = PostgrestAPIError({"message": "relation does not exist", "code": "42P01"})
        c = classify_error(err)

        assert c.category == ErrorCategory.INFRA
        assert c.is_retryable is False
        assert c.is_transient is False
        assert c.should_record_breaker is False
        assert c.error_code == "DB_ERROR"
        assert c.original is err

    def test_postgrest_on_conflict_error(self):
        """ON CONFLICT 错误也应归 INFRA"""
        from postgrest.exceptions import APIError as PostgrestAPIError

        err = PostgrestAPIError({
            "message": "there is no unique or exclusion constraint matching the ON CONFLICT specification",
            "code": "42712",
        })
        c = classify_error(err)
        assert c.category == ErrorCategory.INFRA
        assert c.is_retryable is False


# ============================================================
# 2. ProviderUnavailableError → MODEL, 可重试, 不记熔断
# ============================================================


class TestProviderUnavailable:
    def test_provider_unavailable(self):
        from services.adapters.types import ModelProvider, ProviderUnavailableError

        err = ProviderUnavailableError("KIE is down", ModelProvider.KIE)
        c = classify_error(err)

        assert c.category == ErrorCategory.MODEL
        assert c.is_retryable is True
        assert c.should_record_breaker is False
        assert c.error_code == "PROVIDER_UNAVAILABLE"


# ============================================================
# 3. 网络 / 超时类错误 → TRANSIENT
# ============================================================


class TestTransientErrors:
    @pytest.mark.parametrize("error_class", [
        ConnectionError,
        TimeoutError,
        ConnectionResetError,
        ConnectionAbortedError,
        BrokenPipeError,
    ])
    def test_builtin_network_errors(self, error_class):
        c = classify_error(error_class("network down"))
        assert c.category == ErrorCategory.TRANSIENT
        assert c.is_retryable is True
        assert c.is_transient is True
        assert c.should_record_breaker is True
        assert c.error_code == "NETWORK_ERROR"

    def test_asyncio_timeout(self):
        c = classify_error(asyncio.TimeoutError())
        assert c.category == ErrorCategory.TRANSIENT
        assert c.is_retryable is True

    @pytest.mark.parametrize("error_class", [
        httpx.ConnectError,
        httpx.ReadTimeout,
        httpx.WriteTimeout,
        httpx.ConnectTimeout,
        httpx.PoolTimeout,
    ])
    def test_httpx_errors(self, error_class):
        # httpx errors require a message arg
        c = classify_error(error_class("timeout"))
        assert c.category == ErrorCategory.TRANSIENT
        assert c.is_retryable is True

    def test_file_not_found_is_not_transient(self):
        """FileNotFoundError (OSError 子类) 不应被误分类为 TRANSIENT"""
        c = classify_error(FileNotFoundError("no such file"))
        assert c.category != ErrorCategory.TRANSIENT
        assert c.is_retryable is False

    def test_permission_error_is_not_transient(self):
        """PermissionError (OSError 子类) 不应被误分类为 TRANSIENT"""
        c = classify_error(PermissionError("access denied"))
        assert c.category != ErrorCategory.TRANSIENT
        assert c.is_retryable is False


# ============================================================
# 4. 业务规则错误 → BUSINESS
# ============================================================


class TestBusinessErrors:
    def test_insufficient_credits(self):
        from core.exceptions import InsufficientCreditsError

        c = classify_error(InsufficientCreditsError(required=100, current=10))
        assert c.category == ErrorCategory.BUSINESS
        assert c.is_retryable is False
        assert c.should_refund is False
        assert c.error_code == "INSUFFICIENT_CREDITS"

    def test_validation_error(self):
        from core.exceptions import ValidationError

        c = classify_error(ValidationError("invalid field"))
        assert c.category == ErrorCategory.BUSINESS
        assert c.is_retryable is False
        assert c.should_refund is True

    def test_permission_denied(self):
        from core.exceptions import PermissionDeniedError

        c = classify_error(PermissionDeniedError())
        assert c.category == ErrorCategory.BUSINESS
        assert c.is_retryable is False

    def test_rate_limit_is_transient(self):
        """RateLimitError 归类为 TRANSIENT（可退避重试）"""
        from core.exceptions import RateLimitError

        c = classify_error(RateLimitError())
        assert c.category == ErrorCategory.TRANSIENT
        assert c.is_retryable is True
        assert c.is_transient is True

    def test_task_queue_full(self):
        from core.exceptions import TaskQueueFullError

        c = classify_error(TaskQueueFullError(current_count=5, max_count=5))
        assert c.category == ErrorCategory.BUSINESS
        assert c.is_retryable is False
        assert c.error_code == "QUEUE_FULL"


# ============================================================
# 5. AI 模型调用错误 → MODEL
# ============================================================


class TestModelErrors:
    def test_ai_model_error(self):
        from core.exceptions import AIModelError

        c = classify_error(AIModelError(model="gpt-4", message="rate limited"))
        assert c.category == ErrorCategory.MODEL
        assert c.is_retryable is True
        assert c.should_record_breaker is True
        assert c.error_code == "MODEL_ERROR"

    def test_external_service_error(self):
        from core.exceptions import ExternalServiceError

        c = classify_error(ExternalServiceError(service="OpenRouter"))
        assert c.category == ErrorCategory.MODEL
        assert c.is_retryable is True
        assert c.should_record_breaker is True


# ============================================================
# 6. KIE Adapter 错误
# ============================================================


class TestKieErrors:
    def test_kie_rate_limit(self):
        from services.adapters.kie.client import KieRateLimitError

        c = classify_error(KieRateLimitError("too many requests"))
        assert c.category == ErrorCategory.TRANSIENT
        assert c.is_retryable is True
        assert c.error_code == "KIE_RATE_LIMIT"

    def test_kie_insufficient_balance(self):
        from services.adapters.kie.client import KieInsufficientBalanceError

        c = classify_error(KieInsufficientBalanceError("no balance"))
        assert c.category == ErrorCategory.BUSINESS
        assert c.is_retryable is False

    def test_kie_task_timeout(self):
        from services.adapters.kie.client import KieTaskTimeoutError

        c = classify_error(KieTaskTimeoutError("timeout"))
        assert c.category == ErrorCategory.TRANSIENT
        assert c.is_retryable is True

    def test_kie_task_failed(self):
        from services.adapters.kie.client import KieTaskFailedError

        c = classify_error(KieTaskFailedError("generation failed", fail_code="NSFW"))
        assert c.category == ErrorCategory.MODEL
        assert c.is_retryable is True
        assert c.error_code == "KIE_ERROR"

    def test_kie_api_error(self):
        from services.adapters.kie.client import KieAPIError

        c = classify_error(KieAPIError("server error", status_code=500))
        assert c.category == ErrorCategory.MODEL
        assert c.is_retryable is True


# ============================================================
# 7. 快麦 ERP 错误
# ============================================================


class TestKuaiMaiErrors:
    def test_kuaimai_rate_limit(self):
        from services.kuaimai.errors import KuaiMaiRateLimitError

        c = classify_error(KuaiMaiRateLimitError("频率限制"))
        assert c.category == ErrorCategory.TRANSIENT
        assert c.is_retryable is False  # ERP 限流不换模型
        assert c.is_transient is True

    def test_kuaimai_token_expired(self):
        from services.kuaimai.errors import KuaiMaiTokenExpiredError

        c = classify_error(KuaiMaiTokenExpiredError("token expired"))
        assert c.category == ErrorCategory.INFRA
        assert c.is_retryable is False

    def test_kuaimai_generic(self):
        from services.kuaimai.errors import KuaiMaiError

        c = classify_error(KuaiMaiError("unknown error"))
        assert c.category == ErrorCategory.MODEL
        assert c.is_retryable is False


# ============================================================
# 8. 兜底：未知错误 → UNKNOWN
# ============================================================


class TestUnknownErrors:
    def test_runtime_error(self):
        c = classify_error(RuntimeError("unexpected"))
        assert c.category == ErrorCategory.UNKNOWN
        assert c.is_retryable is False
        assert c.should_record_breaker is False
        assert c.error_code == "UNKNOWN_ERROR"

    def test_value_error(self):
        c = classify_error(ValueError("bad value"))
        assert c.category == ErrorCategory.UNKNOWN
        assert c.is_retryable is False

    def test_key_error(self):
        c = classify_error(KeyError("missing"))
        assert c.category == ErrorCategory.UNKNOWN
        assert c.is_retryable is False

    def test_generic_exception(self):
        c = classify_error(Exception("generic"))
        assert c.category == ErrorCategory.UNKNOWN
        assert c.is_retryable is False


# ============================================================
# 9. ClassifiedError 属性
# ============================================================


class TestClassifiedErrorProperties:
    def test_frozen_dataclass(self):
        """ClassifiedError 是 frozen 的，不可修改"""
        c = classify_error(RuntimeError("test"))
        with pytest.raises(AttributeError):
            c.is_retryable = True  # type: ignore

    def test_original_preserved(self):
        """原始异常被完整保留"""
        err = ValueError("original")
        c = classify_error(err)
        assert c.original is err
