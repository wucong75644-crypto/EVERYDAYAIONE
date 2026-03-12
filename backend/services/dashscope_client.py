"""
DashScope HTTP 客户端工厂

提供统一的 httpx.AsyncClient 创建和生命周期管理，
消除 context_summarizer / memory_filter / knowledge_extractor 中的重复代码。
"""

from typing import Optional

import httpx
from loguru import logger

from core.config import settings


class DashScopeClient:
    """延迟初始化的 DashScope HTTP 客户端包装器"""

    def __init__(self, timeout_attr: str, default_timeout: float = 5.0):
        """
        Args:
            timeout_attr: settings 上的超时属性名，如 'context_summary_timeout'
            default_timeout: 属性不存在时的默认超时（秒）
        """
        self._timeout_attr = timeout_attr
        self._default_timeout = default_timeout
        self._client: Optional[httpx.AsyncClient] = None

    async def get(self) -> httpx.AsyncClient:
        """获取或创建 HTTP 客户端"""
        if self._client is None or self._client.is_closed:
            timeout = getattr(settings, self._timeout_attr, self._default_timeout)
            self._client = httpx.AsyncClient(
                base_url=settings.dashscope_base_url,
                headers={
                    "Authorization": f"Bearer {settings.dashscope_api_key}",
                    "Content-Type": "application/json",
                },
                timeout=httpx.Timeout(
                    connect=5.0,
                    read=timeout,
                    write=10.0,
                    pool=5.0,
                ),
            )
        return self._client

    async def close(self) -> None:
        """关闭 HTTP 客户端"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
            logger.debug(f"DashScope client closed | timeout_attr={self._timeout_attr}")
