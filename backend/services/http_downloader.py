"""
HTTP 文件下载器

提供带连接池复用的异步文件下载功能。
支持流式下载、大小限制检查、细粒度超时控制。

httpx 官方推荐复用 Client 实例以获得连接池，
避免每次请求都建立新的 TCP 连接和 TLS 握手。
参考：https://www.python-httpx.org/advanced/clients/
"""

from typing import Optional

import httpx
from loguru import logger


class HttpDownloader:
    """
    异步 HTTP 文件下载器（连接池复用）

    特性：
    - 惰性初始化 httpx.AsyncClient，复用连接池
    - 细粒度超时：connect=10s, read=60s(图片)/120s(视频), write=10s, pool=10s
    - 流式下载 + 实时大小检查，防止内存溢出
    - 错误类型保留：TimeoutException/HTTPStatusError 不被包装
    - HTTP 403/404/410 转为 ValueError（不可重试）
    """

    def __init__(self) -> None:
        self._client: Optional[httpx.AsyncClient] = None

    async def get_client(self) -> httpx.AsyncClient:
        """获取或创建 HTTP 客户端（复用连接池）"""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(
                    connect=10.0,   # 连接超时 10s
                    read=60.0,      # 读超时 60s（per-chunk，适合大文件流式下载）
                    write=10.0,     # 写超时 10s
                    pool=10.0,      # 连接池等待 10s
                ),
                follow_redirects=True,
                limits=httpx.Limits(
                    max_connections=10,
                    max_keepalive_connections=5,
                ),
            )
        return self._client

    async def close(self) -> None:
        """关闭 HTTP 客户端连接池"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def download(
        self,
        url: str,
        user_id: str,
        media_type: str,
        max_size: int,
    ) -> tuple[bytes, str]:
        """
        从远程 URL 下载文件

        Args:
            url: 远程文件 URL
            user_id: 用户 ID（用于日志）
            media_type: 媒体类型（image/video）
            max_size: 最大允许字节数

        Returns:
            (content, content_type): 文件内容和 Content-Type

        Raises:
            ValueError: URL 无效或文件过大
            httpx.TimeoutException: 超时（可重试）
            httpx.HTTPStatusError: HTTP 错误（403/404/410 不可重试）
        """
        max_size_mb = max_size / 1024 / 1024

        client = await self.get_client()

        # 视频使用更长的 read timeout（per-request 覆盖默认值）
        request_timeout = httpx.Timeout(
            connect=10.0,
            read=120.0 if media_type == "video" else 60.0,
            write=10.0,
            pool=10.0,
        )

        try:
            # 尝试 HEAD 请求获取 Content-Length
            try:
                head_response = await client.head(url, timeout=10.0)
                content_length = head_response.headers.get("content-length")
                if content_length:
                    size = int(content_length)
                    if size > max_size:
                        logger.warning(
                            f"{media_type.capitalize()} too large | "
                            f"size={size/1024/1024:.1f}MB | max={max_size_mb}MB"
                        )
                        raise ValueError(
                            f"{media_type}文件过大: "
                            f"{size/1024/1024:.1f}MB > {max_size_mb}MB"
                        )
                    logger.info(
                        f"Pre-check passed | size={size/1024/1024:.1f}MB | "
                        f"max={max_size_mb}MB"
                    )
            except (httpx.HTTPError, ValueError) as e:
                if isinstance(e, ValueError) and "文件过大" in str(e):
                    raise
                logger.debug(f"HEAD request failed, will check size during download: {e}")

            # 流式下载并检查累计大小
            content_chunks = []
            total_size = 0

            async with client.stream("GET", url, timeout=request_timeout) as response:
                response.raise_for_status()
                content_type = response.headers.get("content-type", "")

                async for chunk in response.aiter_bytes(chunk_size=8192):
                    content_chunks.append(chunk)
                    total_size += len(chunk)

                    if total_size > max_size:
                        logger.warning(
                            f"{media_type.capitalize()} download aborted | "
                            f"size={total_size/1024/1024:.1f}MB > {max_size_mb}MB"
                        )
                        raise ValueError(
                            f"{media_type}下载超限: >{max_size_mb}MB"
                        )

            content = b"".join(content_chunks)
            logger.info(
                f"{media_type.capitalize()} downloaded | "
                f"size={len(content)/1024/1024:.1f}MB"
            )
            return content, content_type

        except ValueError:
            raise
        except httpx.TimeoutException as e:
            logger.error(
                f"Download timeout | type={media_type} | user_id={user_id} | "
                f"url={url[:100]} | error={e}"
            )
            raise
        except httpx.HTTPStatusError as e:
            logger.error(
                f"HTTP error | status={e.response.status_code} | "
                f"user_id={user_id} | url={url[:100]}"
            )
            if e.response.status_code in (403, 404, 410):
                raise ValueError(
                    f"{media_type} URL 已失效(HTTP {e.response.status_code})"
                )
            raise
        except httpx.HTTPError as e:
            logger.error(
                f"Download failed | type={media_type} | user_id={user_id} | "
                f"url={url[:100]} | error={e}"
            )
            raise
