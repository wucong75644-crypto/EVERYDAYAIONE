"""
快麦ERP API 异常定义

基于 ExternalServiceError 派生快麦专用异常。
"""

from typing import Optional

from core.exceptions import ExternalServiceError


class KuaiMaiError(ExternalServiceError):
    """快麦ERP API 基础异常"""

    def __init__(
        self,
        message: str = "快麦ERP服务暂时不可用",
        code: Optional[str] = None,
        original_error: Optional[str] = None,
    ):
        super().__init__(
            service="快麦ERP",
            message=message,
            original_error=original_error,
        )
        self.error_code = code


class KuaiMaiSignatureError(KuaiMaiError):
    """签名错误 (快麦 code=25)"""

    def __init__(self, original_error: Optional[str] = None):
        super().__init__(
            message="API签名验证失败，请检查AppSecret配置",
            code="25",
            original_error=original_error,
        )


class KuaiMaiTokenExpiredError(KuaiMaiError):
    """Token 过期"""

    def __init__(self, original_error: Optional[str] = None):
        super().__init__(
            message="ERP授权已过期，请联系管理员重新授权",
            code="TOKEN_EXPIRED",
            original_error=original_error,
        )


class KuaiMaiRateLimitError(KuaiMaiError):
    """频率限制"""

    def __init__(self, original_error: Optional[str] = None):
        super().__init__(
            message="请求过于频繁，请稍后再试",
            code="RATE_LIMIT",
            original_error=original_error,
        )


class KuaiMaiBusinessError(KuaiMaiError):
    """业务错误（快麦返回 success=false）"""

    def __init__(
        self,
        message: str,
        code: Optional[str] = None,
        original_error: Optional[str] = None,
    ):
        super().__init__(
            message=message,
            code=code,
            original_error=original_error,
        )
