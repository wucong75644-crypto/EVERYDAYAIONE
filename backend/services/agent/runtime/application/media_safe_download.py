"""Fail-closed downloader for Runtime-owned Provider media results."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from typing import Awaitable, Callable, Mapping, Protocol, Sequence
from urllib.parse import urljoin, urlsplit

import httpcore

from services.agent.runtime.domain.errors import PersistenceContractError


class RuntimeMediaDownloadSecurityError(PersistenceContractError):
    """The Provider result URL violates the Runtime network boundary."""


@dataclass(frozen=True)
class SafeDownloadResponse:
    status_code: int
    headers: Mapping[str, str]
    content: bytes


class SafeDownloadTransport(Protocol):
    async def fetch(
        self, url: str, *, connect_ip: str, server_hostname: str,
        timeout_seconds: float, max_size: int,
    ) -> SafeDownloadResponse: ...

    async def close(self) -> None: ...


Resolver = Callable[[str, int], Awaitable[Sequence[str]]]


class HttpcorePinnedDownloadTransport:
    """Connect to one validated IP while preserving the original Host and SNI."""

    async def fetch(
        self, url: str, *, connect_ip: str, server_hostname: str,
        timeout_seconds: float, max_size: int,
    ) -> SafeDownloadResponse:
        parsed = urlsplit(url)
        target = parsed.path or "/"
        if parsed.query:
            target = f"{target}?{parsed.query}"
        request = httpcore.Request(
            method="GET",
            url=httpcore.URL(
                scheme="https", host=connect_ip, port=443, target=target,
            ),
            headers={
                "Host": _host_header(server_hostname),
                "Accept": "*/*",
                "User-Agent": "EverydayAI-Runtime-Media/1",
            },
            extensions={
                "sni_hostname": server_hostname,
                "timeout": {
                    "connect": 10.0, "read": timeout_seconds,
                    "write": 10.0, "pool": 10.0,
                },
            },
        )
        chunks: list[bytes] = []
        size = 0
        async with httpcore.AsyncConnectionPool(
            max_connections=1, max_keepalive_connections=0,
        ) as pool:
            response = await pool.handle_async_request(request)
            headers = {
                key.decode("ascii").lower(): value.decode("latin-1")
                for key, value in response.headers
            }
            try:
                if (
                    response.status not in _REDIRECT_CODES
                    and not 200 <= response.status < 300
                ):
                    raise RuntimeError(
                        f"runtime media result HTTP {response.status}"
                    )
                declared = headers.get("content-length")
                if declared and int(declared) > max_size:
                    raise ValueError("runtime media result exceeds size limit")
                if response.status not in _REDIRECT_CODES:
                    async for chunk in response.aiter_stream():
                        size += len(chunk)
                        if size > max_size:
                            raise ValueError(
                                "runtime media result exceeds size limit"
                            )
                        chunks.append(chunk)
            finally:
                await response.aclose()
            return SafeDownloadResponse(
                status_code=response.status,
                headers=headers,
                content=b"".join(chunks),
            )

    async def close(self) -> None:
        return None


class RuntimeMediaSafeDownloader:
    """Validate allowlist and DNS at every HTTPS redirect hop."""

    def __init__(
        self, allowed_hosts: Sequence[str], *, resolver: Resolver | None = None,
        transport: SafeDownloadTransport | None = None, max_redirects: int = 5,
    ) -> None:
        normalized = tuple(_normalize_rule(value) for value in allowed_hosts)
        self._allowed_hosts = tuple(value for value in normalized if value)
        if not self._allowed_hosts:
            raise ValueError("RUNTIME_MEDIA_RESULT_HOST_ALLOWLIST_REQUIRED")
        if max_redirects not in range(0, 11):
            raise ValueError("RUNTIME_MEDIA_REDIRECT_LIMIT_INVALID")
        self._resolver = resolver or _resolve_public_addresses
        self._transport = transport or HttpcorePinnedDownloadTransport()
        self._max_redirects = max_redirects

    async def download(
        self, url: str, user_id: str, media_type: str, max_size: int,
    ) -> tuple[bytes, str]:
        del user_id
        current = url
        timeout_seconds = 120.0 if media_type == "video" else 60.0
        for redirect_count in range(self._max_redirects + 1):
            connect_ip, server_hostname = await self._validate_hop(current)
            response = await self._transport.fetch(
                current, connect_ip=connect_ip,
                server_hostname=server_hostname,
                timeout_seconds=timeout_seconds, max_size=max_size,
            )
            if response.status_code not in _REDIRECT_CODES:
                return response.content, response.headers.get("content-type", "")
            if redirect_count == self._max_redirects:
                raise RuntimeMediaDownloadSecurityError(
                    "RUNTIME_MEDIA_REDIRECT_LIMIT_EXCEEDED"
                )
            location = response.headers.get("location")
            if not location:
                raise RuntimeMediaDownloadSecurityError(
                    "RUNTIME_MEDIA_REDIRECT_LOCATION_REQUIRED"
                )
            current = urljoin(current, location)
        raise RuntimeMediaDownloadSecurityError("RUNTIME_MEDIA_REDIRECT_INVALID")

    async def _validate_hop(self, url: str) -> tuple[str, str]:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        if (
            parsed.scheme != "https" or not host or parsed.username is not None
            or parsed.password is not None or parsed.port not in (None, 443)
            or not _host_allowed(host, self._allowed_hosts)
        ):
            raise RuntimeMediaDownloadSecurityError(
                "RUNTIME_MEDIA_RESULT_URL_NOT_ALLOWED"
            )
        addresses = await self._resolver(host, 443)
        if not addresses:
            raise RuntimeMediaDownloadSecurityError(
                "RUNTIME_MEDIA_RESULT_DNS_EMPTY"
            )
        validated: list[str] = []
        for value in addresses:
            try:
                address = ipaddress.ip_address(value)
            except ValueError as error:
                raise RuntimeMediaDownloadSecurityError(
                    "RUNTIME_MEDIA_RESULT_DNS_INVALID"
                ) from error
            if not _public_address(address):
                raise RuntimeMediaDownloadSecurityError(
                    "RUNTIME_MEDIA_RESULT_DNS_FORBIDDEN"
                )
            validated.append(str(address))
        return sorted(validated)[0], host

    async def close(self) -> None:
        await self._transport.close()


async def _resolve_public_addresses(host: str, port: int) -> Sequence[str]:
    loop = asyncio.get_running_loop()
    records = await loop.getaddrinfo(
        host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP,
    )
    return tuple(sorted({record[4][0] for record in records}))


def _normalize_rule(value: str) -> str:
    return value.strip().lower().rstrip(".")


def _host_header(host: str) -> str:
    return f"[{host}]" if ":" in host else host


def _host_allowed(host: str, rules: Sequence[str]) -> bool:
    return any(
        host == rule or (
            rule.startswith("*.") and host.endswith(rule[1:])
            and host != rule[2:]
        )
        for rule in rules
    )


def _public_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return bool(
        address.is_global and not address.is_private and not address.is_loopback
        and not address.is_link_local and not address.is_reserved
        and not address.is_multicast and not address.is_unspecified
    )


_REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})


__all__ = [
    "HttpcorePinnedDownloadTransport", "RuntimeMediaDownloadSecurityError",
    "RuntimeMediaSafeDownloader",
    "SafeDownloadResponse", "SafeDownloadTransport",
]
