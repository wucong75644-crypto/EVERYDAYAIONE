"""
Google Gemini API 数据模型和异常定义

定义 Google 特定的异常类型，便于错误处理和分类。
"""


class GoogleAPIError(Exception):
    """Google API 基础异常"""

    def __init__(self, message: str, status_code: int = None):
        self.message = message
        self.status_code = status_code
        super().__init__(self.message)


class GoogleRateLimitError(GoogleAPIError):
    """429 速率限制错误"""

    def __init__(self, message: str = "已达到 Google API 速率限制，请稍后重试"):
        super().__init__(message, status_code=429)


class GoogleAuthenticationError(GoogleAPIError):
    """401 认证错误"""

    def __init__(self, message: str = "Google API Key 无效或未配置"):
        super().__init__(message, status_code=401)


class GoogleInvalidRequestError(GoogleAPIError):
    """400 请求错误"""

    def __init__(self, message: str = "请求参数无效"):
        super().__init__(message, status_code=400)


class GoogleContentFilterError(GoogleAPIError):
    """内容安全过滤错误"""

    def __init__(self, message: str = "消息违反安全政策，请修改后重试"):
        super().__init__(message, status_code=400)


class GoogleServiceError(GoogleAPIError):
    """500/503 服务端错误"""

    def __init__(self, message: str = "Google 服务暂时不可用", status_code: int = 500):
        super().__init__(message, status_code=status_code)
