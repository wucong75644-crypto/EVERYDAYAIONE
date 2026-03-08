"""
Google Gemini API 客户端封装

封装 google-genai SDK，提供统一的 API 调用接口。
"""

from typing import Any, AsyncIterator, Dict, List

from loguru import logger
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from .models import (
    GoogleAPIError,
    GoogleRateLimitError,
    GoogleAuthenticationError,
    GoogleInvalidRequestError,
    GoogleServiceError,
    GoogleContentFilterError,
)


class GoogleClient:
    """
    Google Gemini API 客户端

    使用新的 google-genai SDK（GA 状态），提供流式和非流式生成功能。
    """

    def __init__(self, api_key: str):
        """
        初始化客户端

        Args:
            api_key: Google API Key
        """
        self.api_key = api_key
        self._client = None
        self._async_client = None

    def _ensure_client(self):
        """延迟初始化同步客户端"""
        if self._client is None:
            try:
                from google import genai
                self._client = genai.Client(api_key=self.api_key)
                logger.debug("Google genai.Client initialized (sync)")
            except Exception as e:
                logger.error(f"Failed to initialize Google Client: {e}")
                raise GoogleAPIError(f"初始化 Google 客户端失败: {str(e)}")

    def _ensure_async_client(self):
        """延迟初始化异步客户端"""
        if self._async_client is None:
            try:
                from google import genai
                self._async_client = genai.Client(
                    api_key=self.api_key,
                    http_options={'api_endpoint': 'generativelanguage.googleapis.com'}
                )
                logger.debug("Google genai.Client initialized (async)")
            except Exception as e:
                logger.error(f"Failed to initialize Google AsyncClient: {e}")
                raise GoogleAPIError(f"初始化 Google 异步客户端失败: {str(e)}")

    def _handle_error(self, error: Exception) -> GoogleAPIError:
        """
        处理 Google API 错误，转换为自定义异常

        Args:
            error: 原始异常

        Returns:
            对应的自定义异常
        """
        error_message = str(error)
        error_lower = error_message.lower()

        # 429 速率限制
        if "429" in error_message or "resource exhausted" in error_lower or "quota" in error_lower:
            logger.warning(f"Google API rate limit hit: {error_message}")
            return GoogleRateLimitError()

        # 401 认证错误
        if "401" in error_message or "unauthorized" in error_lower or "invalid api key" in error_lower:
            logger.error(f"Google API authentication failed: {error_message}")
            return GoogleAuthenticationError()

        # 400 请求错误
        if "400" in error_message or "invalid" in error_lower or "bad request" in error_lower:
            logger.error(f"Google API invalid request: {error_message}")
            return GoogleInvalidRequestError(f"请求参数无效: {error_message}")

        # 内容安全过滤
        if "block" in error_lower or "safety" in error_lower or "content filter" in error_lower:
            logger.warning(f"Google API content filtered: {error_message}")
            return GoogleContentFilterError()

        # 500/503 服务端错误
        if "500" in error_message or "503" in error_message or "internal" in error_lower or "unavailable" in error_lower:
            logger.error(f"Google API service error: {error_message}")
            status_code = 503 if "503" in error_message or "unavailable" in error_lower else 500
            return GoogleServiceError(status_code=status_code)

        # 其他未知错误
        logger.error(f"Google API unknown error: {error_message}", exc_info=True)
        return GoogleAPIError(f"Google API 错误: {error_message}")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((GoogleServiceError,)),
        reraise=True,
    )
    async def generate_content_stream(
        self,
        model: str,
        contents: List[Dict[str, Any]],
        config: Dict[str, Any],
    ) -> AsyncIterator[Any]:
        """
        流式生成内容（异步）

        Args:
            model: 模型 ID（如 gemini-2.5-flash-preview-05-20）
            contents: Google 格式的消息列表
            config: 生成配置（temperature, max_output_tokens 等）

        Yields:
            响应块（包含 text 和 usage_metadata）

        Raises:
            GoogleAPIError: API 调用失败
        """
        self._ensure_client()

        try:
            from google import genai

            # 构建生成配置
            generation_config = genai.types.GenerateContentConfig(
                temperature=config.get("temperature", 1.0),
                top_p=config.get("top_p", 0.95),
                top_k=config.get("top_k", 40),
                max_output_tokens=config.get("max_output_tokens", 8192),
            )

            logger.debug(
                f"Google stream request | model={model} | "
                f"messages={len(contents)} | config={config}"
            )

            # 调用流式 API
            response_stream = self._client.models.generate_content_stream(
                model=model,
                contents=contents,
                config=generation_config,
            )

            # 逐块返回
            chunk_count = 0
            for chunk in response_stream:
                chunk_count += 1
                yield chunk

            logger.debug(f"Google stream completed | chunks={chunk_count}")

        except Exception as e:
            # 转换为自定义异常
            raise self._handle_error(e) from e

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((GoogleServiceError,)),
        reraise=True,
    )
    async def generate_content(
        self,
        model: str,
        contents: List[Dict[str, Any]],
        config: Dict[str, Any],
    ) -> Any:
        """
        非流式生成内容（异步）

        Args:
            model: 模型 ID
            contents: Google 格式的消息列表
            config: 生成配置

        Returns:
            完整的响应对象

        Raises:
            GoogleAPIError: API 调用失败
        """
        self._ensure_client()

        try:
            from google import genai

            # 构建生成配置
            generation_config = genai.types.GenerateContentConfig(
                temperature=config.get("temperature", 1.0),
                top_p=config.get("top_p", 0.95),
                top_k=config.get("top_k", 40),
                max_output_tokens=config.get("max_output_tokens", 8192),
            )

            logger.debug(
                f"Google request | model={model} | "
                f"messages={len(contents)} | config={config}"
            )

            # 调用非流式 API
            response = self._client.models.generate_content(
                model=model,
                contents=contents,
                config=generation_config,
            )

            logger.debug(f"Google request completed | model={model}")
            return response

        except Exception as e:
            # 转换为自定义异常
            raise self._handle_error(e) from e

    async def aclose(self):
        """关闭客户端，释放资源"""
        self._client = None
        self._async_client = None
        logger.debug("Google Client closed")
