"""
URL 验证工具

防止 SSRF（服务器端请求伪造）攻击
"""

import ipaddress
from urllib.parse import urlparse
from typing import List

from loguru import logger

from core.exceptions import AppException


# 禁止访问的私有 IP 范围
BLOCKED_IP_RANGES = [
    ipaddress.ip_network("127.0.0.0/8"),  # localhost
    ipaddress.ip_network("10.0.0.0/8"),  # 私有网络
    ipaddress.ip_network("172.16.0.0/12"),  # 私有网络
    ipaddress.ip_network("192.168.0.0/16"),  # 私有网络
    ipaddress.ip_network("169.254.0.0/16"),  # 链路本地地址（AWS/GCP metadata）
    ipaddress.ip_network("::1/128"),  # IPv6 localhost
    ipaddress.ip_network("fc00::/7"),  # IPv6 私有网络
    ipaddress.ip_network("fe80::/10"),  # IPv6 链路本地地址
]

# 允许的 URL scheme
ALLOWED_SCHEMES = {"http", "https"}


def validate_url(url: str) -> str:
    """
    验证 URL 是否安全（防止 SSRF 攻击）

    Args:
        url: 待验证的 URL

    Returns:
        验证通过的 URL

    Raises:
        AppException: URL 不安全或格式错误
    """
    try:
        parsed = urlparse(url)

        # 1. 检查 scheme
        if parsed.scheme not in ALLOWED_SCHEMES:
            logger.warning(f"Blocked URL with invalid scheme: {url}, scheme={parsed.scheme}")
            raise AppException(
                code="INVALID_URL",
                message=f"不支持的 URL 协议: {parsed.scheme}，仅允许 http/https",
                status_code=400,
            )

        # 2. 检查是否有 hostname
        if not parsed.hostname:
            logger.warning(f"Blocked URL with no hostname: {url}")
            raise AppException(
                code="INVALID_URL",
                message="URL 格式错误：缺少主机名",
                status_code=400,
            )

        # 3. 检查是否是私有 IP
        try:
            ip = ipaddress.ip_address(parsed.hostname)
            for blocked_range in BLOCKED_IP_RANGES:
                if ip in blocked_range:
                    logger.warning(
                        f"Blocked URL with private IP: {url}, "
                        f"ip={ip}, range={blocked_range}"
                    )
                    raise AppException(
                        code="INVALID_URL",
                        message="不允许访问内网地址",
                        status_code=400,
                    )
        except ValueError:
            # 不是 IP 地址，是域名，继续检查
            pass

        # 4. 检查是否是常见的危险域名
        dangerous_hostnames = {
            "localhost",
            "metadata.google.internal",  # GCP metadata
            "metadata",  # AWS metadata (通过 169.254.169.254)
        }

        hostname_lower = parsed.hostname.lower()
        if hostname_lower in dangerous_hostnames:
            logger.warning(f"Blocked URL with dangerous hostname: {url}")
            raise AppException(
                code="INVALID_URL",
                message="不允许访问该地址",
                status_code=400,
            )

        return url

    except AppException:
        raise
    except Exception as e:
        logger.error(f"URL validation error: url={url}, error={e}")
        raise AppException(
            code="INVALID_URL",
            message=f"URL 格式错误: {str(e)}",
            status_code=400,
        )


def validate_urls(urls: List[str]) -> List[str]:
    """
    批量验证 URL 列表

    Args:
        urls: URL 列表

    Returns:
        验证通过的 URL 列表

    Raises:
        AppException: 任一 URL 不安全
    """
    return [validate_url(url) for url in urls]
