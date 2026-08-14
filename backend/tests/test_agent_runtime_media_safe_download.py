from __future__ import annotations

import pytest

from services.agent.runtime.application.media_safe_download import (
    RuntimeMediaDownloadSecurityError, RuntimeMediaSafeDownloader,
    SafeDownloadResponse,
)


class _Transport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.urls = []
        self.closed = False

    async def fetch(self, url, *, timeout_seconds, max_size):
        self.urls.append((url, timeout_seconds, max_size))
        return self.responses.pop(0)

    async def close(self):
        self.closed = True


def _response(status=200, headers=None, content=b"image"):
    return SafeDownloadResponse(status, headers or {"content-type": "image/webp"}, content)


@pytest.mark.asyncio
async def test_safe_download_validates_each_redirect_host_and_dns() -> None:
    resolved = []

    async def resolver(host, port):
        resolved.append((host, port))
        return ("93.184.216.34",)

    transport = _Transport([
        _response(302, {"location": "https://cdn.provider.test/result.webp"}, b""),
        _response(),
    ])
    downloader = RuntimeMediaSafeDownloader(
        ("api.provider.test", "*.provider.test"), resolver=resolver,
        transport=transport,
    )

    content, content_type = await downloader.download(
        "https://api.provider.test/start", "user", "image", 1024,
    )

    assert content == b"image"
    assert content_type == "image/webp"
    assert resolved == [
        ("api.provider.test", 443), ("cdn.provider.test", 443),
    ]
    assert [call[0] for call in transport.urls] == [
        "https://api.provider.test/start",
        "https://cdn.provider.test/result.webp",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("url", [
    "http://api.provider.test/result", "https://user@api.provider.test/result",
    "https://unlisted.test/result", "https://api.provider.test:444/result",
])
async def test_safe_download_rejects_non_https_or_unlisted_hop(url: str) -> None:
    async def resolver(host, port):
        return ("93.184.216.34",)

    transport = _Transport([_response()])
    downloader = RuntimeMediaSafeDownloader(
        ("api.provider.test",), resolver=resolver, transport=transport,
    )
    with pytest.raises(RuntimeMediaDownloadSecurityError):
        await downloader.download(url, "user", "image", 1024)
    assert transport.urls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("address", [
    "127.0.0.1", "10.0.0.1", "169.254.169.254", "192.0.2.1", "::1",
])
async def test_safe_download_rejects_private_loopback_linklocal_reserved_dns(
    address: str,
) -> None:
    async def resolver(host, port):
        return (address,)

    transport = _Transport([_response()])
    downloader = RuntimeMediaSafeDownloader(
        ("api.provider.test",), resolver=resolver, transport=transport,
    )
    with pytest.raises(RuntimeMediaDownloadSecurityError):
        await downloader.download(
            "https://api.provider.test/result", "user", "image", 1024,
        )
    assert transport.urls == []


@pytest.mark.asyncio
async def test_safe_download_rejects_redirect_to_private_dns_before_fetch() -> None:
    async def resolver(host, port):
        return ("10.0.0.1",) if host == "private.provider.test" else ("93.184.216.34",)

    transport = _Transport([
        _response(302, {"location": "https://private.provider.test/result"}, b""),
    ])
    downloader = RuntimeMediaSafeDownloader(
        ("*.provider.test",), resolver=resolver, transport=transport,
    )
    with pytest.raises(RuntimeMediaDownloadSecurityError):
        await downloader.download(
            "https://api.provider.test/start", "user", "image", 1024,
        )
    assert len(transport.urls) == 1
