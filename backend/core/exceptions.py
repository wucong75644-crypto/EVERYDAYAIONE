"""
自定义异常类

定义应用中使用的各种业务异常。
"""

from typing import Any, Optional


class AppException(Exception):
    """应用基础异常"""

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 400,
        details: Optional[dict[str, Any]] = None,
    ):
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}
        super().__init__(message)


# ========== 认证相关异常 ==========


class AuthenticationError(AppException):
    """认证失败"""

    def __init__(self, message: str = "认证失败", details: Optional[dict] = None):
        super().__init__(
            code="AUTHENTICATION_ERROR",
            message=message,
            status_code=401,
            details=details,
        )


class TokenExpiredError(AuthenticationError):
    """Token 已过期"""

    def __init__(self):
        super().__init__(message="登录已过期，请重新登录")


class InvalidTokenError(AuthenticationError):
    """无效的 Token"""

    def __init__(self):
        super().__init__(message="无效的认证信息")


class PermissionDeniedError(AppException):
    """权限不足"""

    def __init__(self, message: str = "权限不足"):
        super().__init__(
            code="PERMISSION_DENIED",
            message=message,
            status_code=403,
        )


# ========== 资源相关异常 ==========


class NotFoundError(AppException):
    """资源不存在"""

    def __init__(self, resource: str = "资源", resource_id: Optional[str] = None):
        details = {"resource_id": resource_id} if resource_id else {}
        super().__init__(
            code="NOT_FOUND",
            message=f"{resource}不存在",
            status_code=404,
            details=details,
        )


class ConflictError(AppException):
    """资源冲突（如重复）"""

    def __init__(self, message: str = "资源已存在"):
        super().__init__(
            code="CONFLICT",
            message=message,
            status_code=409,
        )


# ========== 业务相关异常 ==========


class ValidationError(AppException):
    """数据验证失败"""

    def __init__(self, message: str, field: Optional[str] = None):
        details = {"field": field} if field else {}
        super().__init__(
            code="VALIDATION_ERROR",
            message=message,
            status_code=400,
            details=details,
        )


class RateLimitError(AppException):
    """频率限制"""

    def __init__(self, message: str = "请求过于频繁", retry_after: int = 60):
        super().__init__(
            code="RATE_LIMIT_EXCEEDED",
            message=message,
            status_code=429,
            details={"retry_after": retry_after},
        )


class InsufficientCreditsError(AppException):
    """积分不足"""

    def __init__(self, required: int, current: int):
        super().__init__(
            code="INSUFFICIENT_CREDITS",
            message=f"积分不足，需要 {required} 积分，当前余额 {current} 积分",
            status_code=402,
            details={"required": required, "current": current},
        )


class TaskQueueFullError(AppException):
    """任务队列已满"""

    def __init__(self, current_count: int, max_count: int, scope: str = "global"):
        if scope == "global":
            message = "当前任务数已达上限，请等待任务完成后再试"
        else:
            message = "当前对话任务数已达上限，请等待完成或切换到其他对话"
        super().__init__(
            code="TASK_QUEUE_FULL",
            message=message,
            status_code=429,
            details={
                "current_count": current_count,
                "max_count": max_count,
                "scope": scope,
            },
        )


# ========== 外部服务异常 ==========


class ExternalServiceError(AppException):
    """外部服务调用失败"""

    def __init__(
        self,
        service: str,
        message: str = "服务暂时不可用",
        original_error: Optional[str] = None,
    ):
        super().__init__(
            code="EXTERNAL_SERVICE_ERROR",
            message=f"{service}: {message}",
            status_code=503,
            details={"service": service, "original_error": original_error},
        )


class SMSServiceError(ExternalServiceError):
    """短信服务异常"""

    def __init__(self, message: str = "短信发送失败", original_error: Optional[str] = None):
        super().__init__(
            service="短信服务",
            message=message,
            original_error=original_error,
        )


class AIModelError(ExternalServiceError):
    """AI 模型调用异常"""

    def __init__(
        self,
        model: str,
        message: str = "模型调用失败",
        original_error: Optional[str] = None,
    ):
        super().__init__(
            service=f"AI模型({model})",
            message=message,
            original_error=original_error,
        )
