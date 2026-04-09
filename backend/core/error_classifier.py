"""
错误分类器

将任意异常映射为结构化分类结果，供重试决策、熔断器记录、积分处理等使用。
设计原则：不改已有异常类，新增一个统一分类入口。

分类依据（优先级从高到低）：
1. Supabase DB 错误（PostgREST APIError）→ INFRA
2. ProviderUnavailableError（熔断器已 OPEN）→ MODEL（可重试，不再记熔断）
3. 网络/超时类错误 → TRANSIENT
4. 积分不足 → BUSINESS
5. AI 模型调用错误 → MODEL
6. 快麦/KIE 等外部服务错误 → 按子类型分
7. 未知错误 → UNKNOWN（不可重试）
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class ErrorCategory(str, Enum):
    """错误大类"""
    MODEL = "model"          # 模型/Provider 调用失败（可换模型重试）
    INFRA = "infra"          # 基础设施故障（DB/Redis/内部服务）
    BUSINESS = "business"    # 业务规则拒绝（积分不足/权限/验证）
    TRANSIENT = "transient"  # 瞬态故障（网络抖动/超时/乐观锁冲突）
    UNKNOWN = "unknown"      # 未识别


@dataclass(frozen=True)
class ClassifiedError:
    """错误分类结果"""
    category: ErrorCategory
    is_retryable: bool             # 可换模型重试
    is_transient: bool             # 瞬态，可同操作短暂退避后重试
    should_refund: bool            # 建议退回积分
    should_record_breaker: bool    # 是否记入熔断器
    error_code: str                # 标准化错误码
    original: Exception            # 原始异常


def classify_error(error: Exception) -> ClassifiedError:
    """
    统一错误分类入口。

    将任意异常映射为 ClassifiedError，调用方根据分类属性做决策，
    不再依赖 isinstance 散落在各处。
    """
    # ------------------------------------------------------------------
    # 1. Supabase PostgREST 数据库错误 → INFRA
    # ------------------------------------------------------------------
    try:
        from postgrest.exceptions import APIError as PostgrestAPIError
        if isinstance(error, PostgrestAPIError):
            return ClassifiedError(
                category=ErrorCategory.INFRA,
                is_retryable=False,
                is_transient=False,
                should_refund=False,
                should_record_breaker=False,
                error_code="DB_ERROR",
                original=error,
            )
    except ImportError:
        pass

    # ------------------------------------------------------------------
    # 2. ProviderUnavailableError（熔断器已 OPEN）→ MODEL，可重试但不记熔断
    # ------------------------------------------------------------------
    try:
        from services.adapters.types import ProviderUnavailableError
        if isinstance(error, ProviderUnavailableError):
            return ClassifiedError(
                category=ErrorCategory.MODEL,
                is_retryable=True,
                is_transient=False,
                should_refund=True,
                should_record_breaker=False,  # 已经 OPEN，不重复记录
                error_code="PROVIDER_UNAVAILABLE",
                original=error,
            )
    except ImportError:
        pass

    # ------------------------------------------------------------------
    # 3. 网络 / 超时类错误 → TRANSIENT
    # ------------------------------------------------------------------
    import asyncio
    import httpx

    _transient_types = (
        ConnectionError, TimeoutError, asyncio.TimeoutError,
        httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout,
        httpx.ConnectTimeout, httpx.PoolTimeout,
        ConnectionResetError, ConnectionAbortedError, BrokenPipeError,
    )
    if isinstance(error, _transient_types):
        return ClassifiedError(
            category=ErrorCategory.TRANSIENT,
            is_retryable=True,
            is_transient=True,
            should_refund=True,
            should_record_breaker=True,
            error_code="NETWORK_ERROR",
            original=error,
        )

    # ------------------------------------------------------------------
    # 4. 业务规则错误 → BUSINESS（不可重试）
    # ------------------------------------------------------------------
    try:
        from core.exceptions import (
            InsufficientCreditsError, ValidationError, PermissionDeniedError,
            RateLimitError, TaskQueueFullError,
        )
        if isinstance(error, InsufficientCreditsError):
            return ClassifiedError(
                category=ErrorCategory.BUSINESS,
                is_retryable=False,
                is_transient=False,
                should_refund=False,
                should_record_breaker=False,
                error_code="INSUFFICIENT_CREDITS",
                original=error,
            )
        if isinstance(error, (ValidationError, PermissionDeniedError)):
            return ClassifiedError(
                category=ErrorCategory.BUSINESS,
                is_retryable=False,
                is_transient=False,
                should_refund=True,
                should_record_breaker=False,
                error_code="BUSINESS_ERROR",
                original=error,
            )
        if isinstance(error, RateLimitError):
            return ClassifiedError(
                category=ErrorCategory.TRANSIENT,
                is_retryable=True,
                is_transient=True,
                should_refund=False,
                should_record_breaker=True,
                error_code="RATE_LIMIT",
                original=error,
            )
        if isinstance(error, TaskQueueFullError):
            return ClassifiedError(
                category=ErrorCategory.BUSINESS,
                is_retryable=False,
                is_transient=False,
                should_refund=True,
                should_record_breaker=False,
                error_code="QUEUE_FULL",
                original=error,
            )
    except ImportError:
        pass

    # ------------------------------------------------------------------
    # 5. KIE Adapter 错误（必须在 ExternalServiceError 之前，因为 KIE 是其子类）
    # ------------------------------------------------------------------
    try:
        from services.adapters.kie.client import (
            KieAPIError, KieRateLimitError, KieInsufficientBalanceError,
            KieTaskFailedError, KieTaskTimeoutError,
        )
        if isinstance(error, KieRateLimitError):
            return ClassifiedError(
                category=ErrorCategory.TRANSIENT,
                is_retryable=True,
                is_transient=True,
                should_refund=False,
                should_record_breaker=True,
                error_code="KIE_RATE_LIMIT",
                original=error,
            )
        if isinstance(error, KieInsufficientBalanceError):
            return ClassifiedError(
                category=ErrorCategory.BUSINESS,
                is_retryable=False,
                is_transient=False,
                should_refund=True,
                should_record_breaker=False,
                error_code="KIE_BALANCE",
                original=error,
            )
        if isinstance(error, KieTaskTimeoutError):
            return ClassifiedError(
                category=ErrorCategory.TRANSIENT,
                is_retryable=True,
                is_transient=True,
                should_refund=True,
                should_record_breaker=True,
                error_code="KIE_TIMEOUT",
                original=error,
            )
        if isinstance(error, (KieTaskFailedError, KieAPIError)):
            return ClassifiedError(
                category=ErrorCategory.MODEL,
                is_retryable=True,
                is_transient=False,
                should_refund=True,
                should_record_breaker=True,
                error_code="KIE_ERROR",
                original=error,
            )
    except ImportError:
        pass

    # ------------------------------------------------------------------
    # 6. 快麦 ERP 错误（必须在 ExternalServiceError 之前，因为 KuaiMai 是其子类）
    # ------------------------------------------------------------------
    try:
        from services.kuaimai.errors import (
            KuaiMaiError, KuaiMaiRateLimitError, KuaiMaiTokenExpiredError,
        )
        if isinstance(error, KuaiMaiRateLimitError):
            return ClassifiedError(
                category=ErrorCategory.TRANSIENT,
                is_retryable=False,
                is_transient=True,
                should_refund=False,
                should_record_breaker=False,
                error_code="KUAIMAI_RATE_LIMIT",
                original=error,
            )
        if isinstance(error, KuaiMaiTokenExpiredError):
            return ClassifiedError(
                category=ErrorCategory.INFRA,
                is_retryable=False,
                is_transient=False,
                should_refund=False,
                should_record_breaker=False,
                error_code="KUAIMAI_TOKEN_EXPIRED",
                original=error,
            )
        if isinstance(error, KuaiMaiError):
            return ClassifiedError(
                category=ErrorCategory.MODEL,
                is_retryable=False,
                is_transient=False,
                should_refund=False,
                should_record_breaker=False,
                error_code="KUAIMAI_ERROR",
                original=error,
            )
    except ImportError:
        pass

    # ------------------------------------------------------------------
    # 7. AI 模型调用错误 → MODEL（通用兜底，放在具体子类之后）
    # ------------------------------------------------------------------
    try:
        from core.exceptions import AIModelError, ExternalServiceError
        if isinstance(error, AIModelError):
            return ClassifiedError(
                category=ErrorCategory.MODEL,
                is_retryable=True,
                is_transient=False,
                should_refund=True,
                should_record_breaker=True,
                error_code="MODEL_ERROR",
                original=error,
            )
        if isinstance(error, ExternalServiceError):
            return ClassifiedError(
                category=ErrorCategory.MODEL,
                is_retryable=True,
                is_transient=False,
                should_refund=True,
                should_record_breaker=True,
                error_code="EXTERNAL_SERVICE_ERROR",
                original=error,
            )
    except ImportError:
        pass

    # ------------------------------------------------------------------
    # 8. 兜底：未识别错误 → UNKNOWN（不可重试，不记熔断）
    # ------------------------------------------------------------------
    return ClassifiedError(
        category=ErrorCategory.UNKNOWN,
        is_retryable=False,
        is_transient=False,
        should_refund=False,
        should_record_breaker=False,
        error_code="UNKNOWN_ERROR",
        original=error,
    )
