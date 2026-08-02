"""Non-production provider adapters for AR-17.3.

Adapters own provider-specific request/response semantics.  They receive a
narrow transport and never receive settings, raw secrets, or a legacy tool
loop.  The transport is deliberately injectable so the same composition can
run against strict mock servers in tests and isolated provider sandboxes.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from typing import Mapping, Protocol

from services.agent.runtime.domain import ActionAttempt
from services.agent.runtime.executors.contracts import canonical_json
from services.agent.runtime.executors.specialist_contracts import (
    NetworkRule, ProviderReceipt, ProviderState, SpecialistProvider,
)
from services.kuaimai.registry import TOOL_REGISTRIES


class ProviderTransport(Protocol):
    async def request(
        self, *, provider: str, method: str, path: str,
        body: Mapping[str, object], idempotency_key: str,
    ) -> Mapping[str, object]: ...


class ErpDispatcherPort(Protocol):
    async def execute(self, tool_name: str, action: str, params: dict[str, object]) -> object: ...


class HttpProviderTransport:
    """HTTP client for isolated provider servers; no redirects and bounded body."""

    def __init__(self, endpoints: Mapping[str, tuple[str, int]], *, timeout: float = 10.0, max_response_bytes: int = 2_000_000) -> None:
        self._endpoints = dict(endpoints)
        self._timeout = timeout
        self._max_response_bytes = max_response_bytes

    async def request(self, *, provider: str, method: str, path: str, body: Mapping[str, object], idempotency_key: str) -> Mapping[str, object]:
        host, port = self._endpoints[provider]
        payload = json.dumps(dict(body), separators=(",", ":")).encode()
        request = (f"{method} {path} HTTP/1.1\r\nHost: {host}\r\nContent-Type: application/json\r\nConnection: close\r\nContent-Length: {len(payload)}\r\nX-Idempotency-Key: {idempotency_key}\r\n\r\n").encode() + payload
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), self._timeout)
        try:
            writer.write(request)
            await asyncio.wait_for(writer.drain(), self._timeout)
            raw = await asyncio.wait_for(reader.read(self._max_response_bytes + 1), self._timeout)
        finally:
            writer.close()
            await writer.wait_closed()
        head, separator, content = raw.partition(b"\r\n\r\n")
        if not separator or len(content) > self._max_response_bytes:
            raise ValueError("SPECIALIST_PROVIDER_RESPONSE_TOO_LARGE")
        status = int(head.splitlines()[0].split()[1])
        if 300 <= status < 400:
            raise RuntimeError("SPECIALIST_REDIRECT_FORBIDDEN")
        if status >= 400:
            raise RuntimeError(f"SPECIALIST_PROVIDER_HTTP_{status}")
        response = json.loads(content.decode())
        if not isinstance(response, Mapping):
            raise ValueError("SPECIALIST_PROVIDER_OBJECT_REQUIRED")
        return dict(response)


class AllowlistedTransport:
    """Strict mock/sandbox transport enforcing provider, method and path."""

    def __init__(self, delegate: ProviderTransport, rules: tuple[NetworkRule, ...]) -> None:
        self._delegate = delegate
        self._rules = rules

    async def request(self, *, provider: str, method: str, path: str,
                      body: Mapping[str, object], idempotency_key: str) -> Mapping[str, object]:
        rule = next((item for item in self._rules if item.allows(provider, method, path)), None)
        if rule is None:
            raise PermissionError("SPECIALIST_NETWORK_NOT_ALLOWED")
        response = await self._delegate.request(provider=provider, method=method, path=path, body=body, idempotency_key=idempotency_key)
        if len(canonical_json(response).encode()) > rule.max_response_bytes:
            raise ValueError("SPECIALIST_PROVIDER_RESPONSE_TOO_LARGE")
        return response


class ArtifactPort(Protocol):
    async def prepare(self, attempt: ActionAttempt, request: Mapping[str, object]) -> Mapping[str, object]: ...


class MediaTaskPort(Protocol):
    async def prepare(self, attempt: ActionAttempt, request: Mapping[str, object], *, kind: str) -> Mapping[str, object]: ...


class ResourceMutationPort(Protocol):
    async def mutate(self, attempt: ActionAttempt, request: Mapping[str, object], *, operation: str) -> Mapping[str, object]: ...


class ChildRunPort(Protocol):
    async def create(self, attempt: ActionAttempt, request: Mapping[str, object]) -> Mapping[str, object]: ...


class _HTTPProvider(SpecialistProvider):
    """Common provider adapter with explicit route and response allowlists."""

    def __init__(self, transport: ProviderTransport, *, provider: str, submit_path: str,
                 reconcile_path: str | None = None, cancel_path: str | None = None) -> None:
        self.transport = transport
        self.provider = provider
        self.submit_path = submit_path
        self.reconcile_path = reconcile_path
        self.cancel_path = cancel_path

    async def submit(self, attempt: ActionAttempt, request: Mapping[str, object], *, idempotency_key: str) -> ProviderReceipt:
        response = await self.transport.request(provider=self.provider, method="POST", path=self.submit_path, body=request, idempotency_key=idempotency_key)
        return self._receipt(attempt, response)

    async def reconcile(self, attempt: ActionAttempt, receipt: Mapping[str, object]) -> ProviderReceipt:
        if not self.reconcile_path or not _has_provider_identity(receipt):
            return _unknown(self.provider, attempt.request_hash, "PROVIDER_STATUS_UNAVAILABLE")
        response = await self.transport.request(provider=self.provider, method="GET", path=self.reconcile_path, body=_ref_body(receipt), idempotency_key=attempt.idempotency_key)
        return self._receipt(attempt, response)

    async def cancel(self, attempt: ActionAttempt, receipt: Mapping[str, object]) -> ProviderReceipt:
        if not self.cancel_path or not _has_provider_identity(receipt):
            return _unknown(self.provider, attempt.request_hash, "PROVIDER_CANCEL_UNPROVEN")
        response = await self.transport.request(provider=self.provider, method="POST", path=self.cancel_path, body=_ref_body(receipt), idempotency_key=attempt.idempotency_key + ":cancel")
        result = self._receipt(attempt, response)
        if result.state is ProviderState.CANCELLED and result.evidence.get("cancel_confirmed") is True:
            return result
        return _unknown(self.provider, attempt.request_hash, "PROVIDER_CANCEL_UNPROVEN")

    def _receipt(self, attempt: ActionAttempt, response: Mapping[str, object]) -> ProviderReceipt:
        state = ProviderState(str(response.get("state", "unknown")))
        result = _object(response.get("result"))
        evidence = _object(response.get("evidence"))
        return ProviderReceipt(
            state=state, provider=self.provider, request_hash=attempt.request_hash,
            provider_task_ref=_text(response.get("provider_task_ref")),
            status_locator=_text(response.get("status_locator")),
            callback_correlation=_text(response.get("callback_correlation")),
            result=result, cost=_object(response.get("cost")), evidence=evidence,
        )


class ERPQueryProvider(_HTTPProvider):
    def __init__(self, dispatcher: ErpDispatcherPort, *, tool_name: str, write: bool = False) -> None:
        self.dispatcher = dispatcher
        self.tool_name = tool_name
        self.write = write
        super().__init__(_NoTransport(), provider="erp", submit_path="")

    async def submit(self, attempt: ActionAttempt, request: Mapping[str, object], *, idempotency_key: str) -> ProviderReceipt:
        action = request.get("action")
        if not isinstance(action, str) or not _valid_erp_action(self.tool_name, action, write=self.write):
            return _unknown("erp", attempt.request_hash, "ERP_ACTION_NOT_REGISTERED")
        result = await self.dispatcher.execute(self.tool_name, action, _params(request))
        return _dispatcher_receipt(attempt, result, provider="erp")


class CrawlerProvider(_HTTPProvider):
    def __init__(self, transport: ProviderTransport) -> None:
        super().__init__(transport, provider="crawler", submit_path="/v1/crawl", reconcile_path="/v1/crawl/status")


class DashScopeSearchProvider(_HTTPProvider):
    def __init__(self, transport: ProviderTransport) -> None:
        super().__init__(transport, provider="dashscope", submit_path="/api/v1/search")


class KieMediaProvider(_HTTPProvider):
    def __init__(self, transport: ProviderTransport, *, kind: str, task_port: MediaTaskPort | None = None) -> None:
        self.task_port = task_port
        self.kind = kind
        path = "/api/v1/image/generations" if kind == "image" else "/api/v1/video/generations"
        status = "/api/v1/tasks/status"
        cancel = "/api/v1/tasks/cancel"
        super().__init__(transport, provider="kie", submit_path=path, reconcile_path=status, cancel_path=cancel)

    async def submit(self, attempt: ActionAttempt, request: Mapping[str, object], *, idempotency_key: str) -> ProviderReceipt:
        task_facts = {}
        if self.task_port is not None:
            task_facts = dict(await self.task_port.prepare(attempt, request, kind=self.kind))
        receipt = await super().submit(attempt, {**request, "runtime_task": task_facts}, idempotency_key=idempotency_key)
        if task_facts:
            receipt = ProviderReceipt(
                state=receipt.state, provider=receipt.provider, request_hash=receipt.request_hash,
                provider_task_ref=receipt.provider_task_ref, status_locator=receipt.status_locator,
                callback_correlation=receipt.callback_correlation, result={**receipt.result, "runtime_task": task_facts},
                cost=receipt.cost, evidence=receipt.evidence,
            )
        return receipt


@dataclass(frozen=True, kw_only=True)
class ErpApiSearchProvider(SpecialistProvider):
    search: object

    async def submit(self, attempt: ActionAttempt, request: Mapping[str, object], *, idempotency_key: str) -> ProviderReceipt:
        query = request.get("query")
        if not isinstance(query, str) or not query.strip():
            return _unknown("erp_catalog", attempt.request_hash, "ERP_SEARCH_QUERY_REQUIRED")
        result = self.search(query)  # type: ignore[operator]
        return ProviderReceipt(state=ProviderState.COMPLETED, provider="erp_catalog", request_hash=attempt.request_hash, result={"summary": str(result), "count": 1})

    async def reconcile(self, attempt: ActionAttempt, receipt: Mapping[str, object]) -> ProviderReceipt:
        return _unknown("erp_catalog", attempt.request_hash, "ERP_SEARCH_RECONCILE_UNAVAILABLE")

    async def cancel(self, attempt: ActionAttempt, receipt: Mapping[str, object]) -> ProviderReceipt:
        return _unknown("erp_catalog", attempt.request_hash, "ERP_SEARCH_CANCEL_UNAVAILABLE")


@dataclass(frozen=True, kw_only=True)
class LocalArtifactProvider(SpecialistProvider):
    port: ArtifactPort
    operation: str

    async def submit(self, attempt: ActionAttempt, request: Mapping[str, object], *, idempotency_key: str) -> ProviderReceipt:
        result = await self.port.prepare(attempt, {**request, "operation": self.operation, "idempotency_key": idempotency_key})
        return ProviderReceipt(state=ProviderState.COMPLETED, provider="artifact", request_hash=attempt.request_hash, result=result)

    async def reconcile(self, attempt: ActionAttempt, receipt: Mapping[str, object]) -> ProviderReceipt:
        return _unknown("artifact", attempt.request_hash, "ARTIFACT_RECONCILE_UNAVAILABLE")

    async def cancel(self, attempt: ActionAttempt, receipt: Mapping[str, object]) -> ProviderReceipt:
        return _unknown("artifact", attempt.request_hash, "ARTIFACT_CANCEL_UNPROVEN")


@dataclass(frozen=True, kw_only=True)
class PortBackedProvider(SpecialistProvider):
    port: ResourceMutationPort | ChildRunPort | MediaTaskPort
    operation: str
    provider: str

    async def submit(self, attempt: ActionAttempt, request: Mapping[str, object], *, idempotency_key: str) -> ProviderReceipt:
        if self.provider == "media":
            result = await self.port.prepare(attempt, {**request, "idempotency_key": idempotency_key}, kind=self.operation)  # type: ignore[attr-defined]
        elif self.provider == "child_run":
            result = await self.port.create(attempt, {**request, "idempotency_key": idempotency_key})  # type: ignore[attr-defined]
        else:
            result = await self.port.mutate(attempt, {**request, "idempotency_key": idempotency_key}, operation=self.operation)  # type: ignore[attr-defined]
        return ProviderReceipt(state=ProviderState(str(result.get("state", "completed"))), provider=self.provider, request_hash=attempt.request_hash, provider_task_ref=_text(result.get("provider_task_ref")), result=result, evidence=_object(result.get("evidence")))

    async def reconcile(self, attempt: ActionAttempt, receipt: Mapping[str, object]) -> ProviderReceipt:
        return _unknown(self.provider, attempt.request_hash, "PORT_RECONCILE_UNAVAILABLE")

    async def cancel(self, attempt: ActionAttempt, receipt: Mapping[str, object]) -> ProviderReceipt:
        return _unknown(self.provider, attempt.request_hash, "PORT_CANCEL_UNPROVEN")


def request_hash(request: Mapping[str, object]) -> str:
    return hashlib.sha256(canonical_json(request).encode()).hexdigest()


def _ref_body(receipt: Mapping[str, object]) -> dict[str, object]:
    return {key: receipt[key] for key in ("provider_task_ref", "status_locator", "callback_correlation") if key in receipt}


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _object(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, Mapping) else {}


def _unknown(provider: str, request_hash_value: str, code: str) -> ProviderReceipt:
    return ProviderReceipt(state=ProviderState.UNKNOWN, provider=provider, request_hash=request_hash_value, evidence={"error_code": code})


def _has_provider_identity(receipt: Mapping[str, object]) -> bool:
    return bool(receipt.get("provider_task_ref") or receipt.get("status_locator") or receipt.get("callback_correlation"))


def _valid_erp_action(tool_name: str, action: str, *, write: bool) -> bool:
    entry = TOOL_REGISTRIES.get(tool_name, {}).get(action)
    return entry is not None and entry.is_write is write


def _params(request: Mapping[str, object]) -> dict[str, object]:
    value = request.get("params", {})
    return dict(value) if isinstance(value, Mapping) else {}


def _dispatcher_receipt(attempt: ActionAttempt, result: object, *, provider: str) -> ProviderReceipt:
    status = getattr(result, "status", "success")
    status_value = getattr(status, "value", status)
    if status_value in {"error", "timeout"}:
        return ProviderReceipt(state=ProviderState.FAILED, provider=provider, request_hash=attempt.request_hash, evidence={"error": str(getattr(result, "error_message", "ERP_PROVIDER_ERROR"))})
    return ProviderReceipt(state=ProviderState.COMPLETED, provider=provider, request_hash=attempt.request_hash, result={"summary": str(getattr(result, "summary", result)), "data": getattr(result, "data", None) or []})


class _NoTransport:
    async def request(self, **kwargs):
        raise RuntimeError("TRANSPORT_NOT_CONFIGURED")


__all__ = [
    "AllowlistedTransport", "ArtifactPort", "ChildRunPort", "CrawlerProvider", "DashScopeSearchProvider",
    "ERPQueryProvider", "ErpApiSearchProvider", "ErpDispatcherPort", "HttpProviderTransport", "KieMediaProvider", "LocalArtifactProvider", "MediaTaskPort",
    "PortBackedProvider", "ProviderTransport", "ResourceMutationPort", "request_hash",
]
