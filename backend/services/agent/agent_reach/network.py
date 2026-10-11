"""Bounded HTTP with public DNS addresses pinned through redirects."""
import asyncio
import ipaddress
import socket
from urllib.parse import urljoin, urlsplit

import httpx

from .contracts import ReachError, validate_url
from . import login_logging  # Install filters before issuing login HTTP requests.

MAX_BYTES = 2_000_000


async def public_address(url: str) -> str:
    parsed = urlsplit(validate_url(url))
    records = await asyncio.get_running_loop().getaddrinfo(
        parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80),
        type=socket.SOCK_STREAM)
    addresses = [record[4][0] for record in records]
    if not addresses or any(not ipaddress.ip_address(addr).is_global for addr in addresses):
        raise ReachError('INVALID_URL', '不能访问内网、本机或保留地址')
    return addresses[0]


class PublicHTTP:
    async def request(self, method, url, *, json=None, headers=None, content=None, include_headers=False, include_cookies=False, allowed_statuses=()):
        original_host = urlsplit(url).hostname
        for _ in range(4):
            address = await public_address(url)
            target = httpx.URL(url)
            # TLS still verifies the original hostname; the connection uses the
            # vetted address rather than resolving the hostname a second time.
            pinned = target.copy_with(host=address)
            request_headers = dict(headers or {})
            request_headers['Host'] = target.netloc.decode('ascii')
            async with httpx.AsyncClient(trust_env=False, follow_redirects=False,
                                         timeout=20.0) as client:
                async with client.stream(method, pinned, json=json, content=content, headers=request_headers,
                                         extensions={'sni_hostname': target.host}) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        if method != 'GET' or not response.headers.get('location'):
                            raise ReachError('UPSTREAM_CHANGED', '上游请求发生不支持的重定向')
                        url = urljoin(url, response.headers['location'])
                        if headers and urlsplit(url).hostname != original_host:
                            raise ReachError('INVALID_URL', '认证请求不能重定向到其他站点')
                        continue
                    if response.status_code == 429:
                        raise ReachError('RATE_LIMITED', '平台限流，请稍后重试')
                    if response.status_code in {401, 403} and response.status_code not in allowed_statuses:
                        raise ReachError('PLATFORM_BLOCKED', '平台拒绝访问，可能需要登录或受平台限制')
                    if response.status_code == 404:
                        raise ReachError('NOT_FOUND', '目标资料不存在或当前账号不可见')
                    if response.status_code >= 400 and response.status_code not in allowed_statuses:
                        raise ReachError('NETWORK_ERROR', '上游服务请求失败', retryable=True)
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > MAX_BYTES:
                            raise ReachError('OUTPUT_LIMIT', '上游资料超过单次读取上限')
                    if include_cookies:
                        # httpx sees a pinned IP, so its CookieJar can reject
                        # the original domain's Set-Cookie. Parse each header
                        # independently; the caller selects known cookie names.
                        from http.cookies import SimpleCookie
                        cookies = {}
                        for header in response.headers.get_list('set-cookie'):
                            parsed = SimpleCookie(); parsed.load(header)
                            cookies.update({name:cookie.value for name,cookie in parsed.items()})
                        return bytes(data), dict(response.headers), cookies
                    return (bytes(data), dict(response.headers)) if include_headers else bytes(data)
        raise ReachError('INVALID_URL', '网页重定向次数超过上限')

    async def get(self, url, **kwargs):
        return await self.request('GET', url, **kwargs)
