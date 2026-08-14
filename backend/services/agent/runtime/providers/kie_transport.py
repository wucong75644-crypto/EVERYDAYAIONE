"""Single-attempt HTTP transport for Runtime-owned KIE media calls."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol

import httpx


@dataclass(frozen=True, kw_only=True)
class KieHttpResponse:
    status_code: int
    payload: Mapping[str, object]


class KieOneShotTransport(Protocol):
    async def submit(
        self, *, api_key: str, body: Mapping[str, object], idempotency_key: str,
    ) -> KieHttpResponse: ...

    async def query(
        self, *, api_key: str, provider_task_ref: str,
    ) -> KieHttpResponse: ...


class HttpxKieOneShotTransport:
    """Issue exactly one HTTP request per method call; never retry."""

    base_url = "https://api.kie.ai"
    submit_path = "/api/v1/jobs/createTask"
    query_path = "/api/v1/jobs/recordInfo"

    def __init__(
        self, *, timeout_seconds: float = 60.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("KIE_TIMEOUT_MUST_BE_POSITIVE")
        self._timeout = httpx.Timeout(
            connect=min(timeout_seconds, 5.0), read=timeout_seconds,
            write=min(timeout_seconds, 10.0), pool=min(timeout_seconds, 5.0),
        )
        self._client = client

    async def submit(
        self, *, api_key: str, body: Mapping[str, object], idempotency_key: str,
    ) -> KieHttpResponse:
        return await self._request(
            "POST", self.submit_path, api_key=api_key, json_body=body,
            idempotency_key=idempotency_key,
        )

    async def query(
        self, *, api_key: str, provider_task_ref: str,
    ) -> KieHttpResponse:
        return await self._request(
            "GET", self.query_path, api_key=api_key,
            query={"taskId": provider_task_ref},
        )

    async def _request(
        self, method: str, path: str, *, api_key: str,
        json_body: Mapping[str, object] | None = None,
        query: Mapping[str, str] | None = None,
        idempotency_key: str | None = None,
    ) -> KieHttpResponse:
        if not api_key.strip():
            raise RuntimeError("KIE_CREDENTIAL_UNAVAILABLE")
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        if idempotency_key:
            headers["X-Idempotency-Key"] = idempotency_key
        owned = self._client is None
        client = self._client or httpx.AsyncClient(
            base_url=self.base_url, timeout=self._timeout,
            follow_redirects=False,
        )
        try:
            response = await client.request(
                method, path, headers=headers,
                json=dict(json_body) if json_body is not None else None,
                params=dict(query) if query is not None else None,
            )
            if 300 <= response.status_code < 400:
                raise RuntimeError("KIE_REDIRECT_FORBIDDEN")
            payload = response.json()
            if not isinstance(payload, Mapping):
                raise ValueError("KIE_RESPONSE_OBJECT_REQUIRED")
            return KieHttpResponse(
                status_code=response.status_code, payload=dict(payload),
            )
        finally:
            if owned:
                await client.aclose()


__all__ = [
    "HttpxKieOneShotTransport", "KieHttpResponse", "KieOneShotTransport",
]
