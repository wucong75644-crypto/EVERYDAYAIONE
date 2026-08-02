"""Non-production provider adapters for AR-17.3.

Adapters own provider-specific request/response semantics.  They receive a
narrow transport and never receive settings, raw secrets, or a legacy tool
loop.  The transport is deliberately injectable so the same composition can
run against strict mock servers in tests and isolated provider sandboxes.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Mapping, Protocol

from services.agent.runtime.domain import ActionAttempt
from services.agent.runtime.executors.contracts import canonical_json
from services.agent.runtime.executors.specialist_contracts import (
    NetworkRule, ProviderReceipt, ProviderState, SpecialistProvider,
)


class ProviderTransport(Protocol):
    async def request(
        self, *, provider: str, method: str, path: str,
        body: Mapping[str, object], idempotency_key: str,
    ) -> Mapping[str, object]: ...


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
        self.reconcile_path = reconcile_path or submit_path
        self.cancel_path = cancel_path or submit_path

    async def submit(self, attempt: ActionAttempt, request: Mapping[str, object], *, idempotency_key: str) -> ProviderReceipt:
        response = await self.transport.request(provider=self.provider, method="POST", path=self.submit_path, body=request, idempotency_key=idempotency_key)
        return self._receipt(attempt, response)

    async def reconcile(self, attempt: ActionAttempt, receipt: Mapping[str, object]) -> ProviderReceipt:
        response = await self.transport.request(provider=self.provider, method="GET", path=self.reconcile_path, body=_ref_body(receipt), idempotency_key=attempt.idempotency_key)
        return self._receipt(attempt, response)

    async def cancel(self, attempt: ActionAttempt, receipt: Mapping[str, object]) -> ProviderReceipt:
        response = await self.transport.request(provider=self.provider, method="POST", path=self.cancel_path, body=_ref_body(receipt), idempotency_key=attempt.idempotency_key + ":cancel")
        return self._receipt(attempt, response)

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
    def __init__(self, transport: ProviderTransport, *, operation: str) -> None:
        super().__init__(transport, provider="erp", submit_path=f"/v1/query/{operation}")


class CrawlerProvider(_HTTPProvider):
    def __init__(self, transport: ProviderTransport) -> None:
        super().__init__(transport, provider="crawler", submit_path="/v1/crawl", reconcile_path="/v1/crawl/status")


class DashScopeSearchProvider(_HTTPProvider):
    def __init__(self, transport: ProviderTransport) -> None:
        super().__init__(transport, provider="dashscope", submit_path="/api/v1/search")


class KieMediaProvider(_HTTPProvider):
    def __init__(self, transport: ProviderTransport, *, kind: str) -> None:
        path = "/api/v1/image/generations" if kind == "image" else "/api/v1/video/generations"
        status = "/api/v1/tasks/status"
        cancel = "/api/v1/tasks/cancel"
        super().__init__(transport, provider="kie", submit_path=path, reconcile_path=status, cancel_path=cancel)


@dataclass(frozen=True, kw_only=True)
class LocalArtifactProvider(SpecialistProvider):
    port: ArtifactPort
    operation: str

    async def submit(self, attempt: ActionAttempt, request: Mapping[str, object], *, idempotency_key: str) -> ProviderReceipt:
        result = await self.port.prepare(attempt, {**request, "operation": self.operation, "idempotency_key": idempotency_key})
        return ProviderReceipt(state=ProviderState.COMPLETED, provider="artifact", request_hash=attempt.request_hash, result=result)

    async def reconcile(self, attempt: ActionAttempt, receipt: Mapping[str, object]) -> ProviderReceipt:
        return ProviderReceipt(state=ProviderState.COMPLETED, provider="artifact", request_hash=attempt.request_hash, result=receipt)

    async def cancel(self, attempt: ActionAttempt, receipt: Mapping[str, object]) -> ProviderReceipt:
        return ProviderReceipt(state=ProviderState.CANCELLED, provider="artifact", request_hash=attempt.request_hash, evidence={"cancelled": True})


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
        return ProviderReceipt(state=ProviderState.COMPLETED, provider=self.provider, request_hash=attempt.request_hash, result=receipt)

    async def cancel(self, attempt: ActionAttempt, receipt: Mapping[str, object]) -> ProviderReceipt:
        return ProviderReceipt(state=ProviderState.CANCELLED, provider=self.provider, request_hash=attempt.request_hash, evidence={"cancelled": True})


def request_hash(request: Mapping[str, object]) -> str:
    return hashlib.sha256(canonical_json(request).encode()).hexdigest()


def _ref_body(receipt: Mapping[str, object]) -> dict[str, object]:
    return {key: receipt[key] for key in ("provider_task_ref", "status_locator", "callback_correlation") if key in receipt}


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _object(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, Mapping) else {}


__all__ = [
    "AllowlistedTransport", "ArtifactPort", "ChildRunPort", "CrawlerProvider", "DashScopeSearchProvider",
    "ERPQueryProvider", "KieMediaProvider", "LocalArtifactProvider", "MediaTaskPort",
    "PortBackedProvider", "ProviderTransport", "ResourceMutationPort", "request_hash",
]
