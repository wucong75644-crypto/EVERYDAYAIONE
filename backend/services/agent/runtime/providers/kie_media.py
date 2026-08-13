"""Runtime-owned KIE image/video provider with reconcile-only recovery."""

from __future__ import annotations

import json
from typing import Mapping, Protocol

from services.agent.runtime.domain import ActionAttempt
from services.agent.runtime.executors.specialist_contracts import (
    ProviderReceipt, ProviderState, SpecialistProvider,
)
from services.agent.runtime.providers.kie_transport import (
    KieHttpResponse, KieOneShotTransport,
)


class RuntimeMediaTaskPort(Protocol):
    async def prepare(
        self, attempt: ActionAttempt, *, kind: str,
    ) -> Mapping[str, object]: ...

    async def read(
        self, attempt: ActionAttempt, *, kind: str,
    ) -> Mapping[str, object]: ...


class RuntimeKieCredentialSource(Protocol):
    async def api_key(
        self, attempt: ActionAttempt, *, provider_request_hash: str,
    ) -> str: ...


class RuntimeKieMediaProvider(SpecialistProvider):
    provider = "kie"
    status_locator = "/api/v1/jobs/recordInfo"

    def __init__(
        self, transport: KieOneShotTransport, *, task_port: RuntimeMediaTaskPort,
        credentials: RuntimeKieCredentialSource | None, kind: str,
        production_ready: bool = False,
    ) -> None:
        if kind not in {"image", "video"}:
            raise ValueError("KIE_MEDIA_KIND_INVALID")
        self._transport = transport
        self._task_port = task_port
        self._credentials = credentials
        self._kind = kind
        self.production_ready = production_ready

    async def submit(
        self, attempt: ActionAttempt, request: Mapping[str, object], *,
        idempotency_key: str,
    ) -> ProviderReceipt:
        del request
        status = getattr(attempt, "status", None)
        status_value = getattr(status, "value", status)
        if status_value is not None and str(status_value) not in {"claimed", "dispatching"}:
            raise RuntimeError("KIE_MEDIA_SUBMIT_PHASE_INVALID")
        if not self.production_ready or self._credentials is None:
            return _failed(attempt, "KIE_MEDIA_PROVIDER_NOT_READY")
        try:
            facts = await self._task_port.prepare(attempt, kind=self._kind)
            provider_request, provider_hash = _request_facts(facts)
            api_key = await self._credentials.api_key(
                attempt, provider_request_hash=provider_hash,
            )
        except Exception:
            return _failed(
                attempt, "KIE_MEDIA_CONFIGURATION_UNAVAILABLE",
            )
        try:
            response = await self._transport.submit(
                api_key=api_key, body=provider_request,
                idempotency_key=idempotency_key,
            )
        except Exception:
            return _unknown(
                attempt, "KIE_SUBMIT_RESULT_UNKNOWN",
                provider_request_hash=provider_hash,
            )
        return _submit_receipt(attempt, response, provider_hash)

    async def reconcile(
        self, attempt: ActionAttempt, receipt: Mapping[str, object],
    ) -> ProviderReceipt:
        status = getattr(attempt, "status", None)
        status_value = getattr(status, "value", status)
        if status_value is not None and str(status_value) not in {"accepted", "unknown"}:
            return _unknown(attempt, "KIE_MEDIA_RECONCILE_PHASE_INVALID")
        if not self.production_ready or self._credentials is None:
            return _unknown(attempt, "KIE_MEDIA_PROVIDER_NOT_READY")
        provider_ref = _provider_ref(receipt)
        if provider_ref is None:
            return _unknown(attempt, "KIE_READBACK_REF_REQUIRED")
        try:
            facts = await self._task_port.read(attempt, kind=self._kind)
            _, provider_hash = _request_facts(facts)
            api_key = await self._credentials.api_key(
                attempt, provider_request_hash=provider_hash,
            )
        except Exception:
            return _unknown(
                attempt, "KIE_READBACK_CONFIGURATION_UNAVAILABLE",
                provider_task_ref=provider_ref,
            )
        try:
            response = await self._transport.query(
                api_key=api_key, provider_task_ref=provider_ref,
            )
        except Exception:
            return _unknown(
                attempt, "KIE_READBACK_RESULT_UNKNOWN",
                provider_task_ref=provider_ref,
                provider_request_hash=provider_hash,
            )
        return _readback_receipt(
            attempt, response, provider_ref, provider_hash, self._kind,
        )

    async def cancel(
        self, attempt: ActionAttempt, receipt: Mapping[str, object],
    ) -> ProviderReceipt:
        # KIE documents no generally verifiable cancel endpoint for this API.
        # Never infer cancellation and never issue an unverified network call.
        provider_ref = _provider_ref(receipt)
        return _unknown(
            attempt, "CANCEL_UNPROVEN", provider_task_ref=provider_ref,
        )


def _request_facts(
    facts: Mapping[str, object],
) -> tuple[Mapping[str, object], str]:
    request = facts.get("provider_request")
    request_hash = facts.get("provider_request_hash")
    if not isinstance(request, Mapping):
        raise RuntimeError("KIE_PROVIDER_REQUEST_REQUIRED")
    if not isinstance(request_hash, str) or len(request_hash) != 64 or any(
        char not in "0123456789abcdef" for char in request_hash
    ):
        raise RuntimeError("KIE_PROVIDER_REQUEST_HASH_INVALID")
    forbidden = {
        "task_id", "user_id", "org_id", "credit_transaction_id",
        "reserved_credits", "currency", "runtime_task", "internal_facts",
    }
    if _contains_forbidden(request, forbidden):
        raise RuntimeError("KIE_PROVIDER_REQUEST_INTERNAL_FACT_FORBIDDEN")
    return dict(request), request_hash


def _contains_forbidden(value: object, forbidden: set[str]) -> bool:
    if isinstance(value, Mapping):
        return any(
            str(key) in forbidden or _contains_forbidden(item, forbidden)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_forbidden(item, forbidden) for item in value)
    return False


def _submit_receipt(
    attempt: ActionAttempt, response: KieHttpResponse,
    provider_hash: str,
) -> ProviderReceipt:
    payload = response.payload
    code = payload.get("code")
    data = payload.get("data")
    task_ref = data.get("taskId") if isinstance(data, Mapping) else None
    state = str(data.get("state") if isinstance(data, Mapping) else "").lower()
    if response.status_code == 200 and code == 200 and state == "fail":
        return _failed(
            attempt, "KIE_SUBMIT_REJECTED", provider_request_hash=provider_hash,
            provider_code=payload.get("code"),
        )
    if response.status_code == 200 and code == 200 and state == "success":
        urls = _strict_result_urls(data.get("resultJson") if isinstance(data, Mapping) else None)
        if urls is None:
            return _unknown(
                attempt, "KIE_RESULT_URLS_AMBIGUOUS",
                provider_request_hash=provider_hash,
            )
        return ProviderReceipt(
            state=ProviderState.COMPLETED, provider="kie",
            request_hash=attempt.request_hash, provider_task_ref=task_ref,
            status_locator=RuntimeKieMediaProvider.status_locator,
            result={"image_urls": urls},
            evidence={"provider_state": state, "provider_request_hash": provider_hash},
        )
    if (
        response.status_code == 200 and code == 200
        and isinstance(task_ref, str) and task_ref.strip()
    ):
        return ProviderReceipt(
            state=ProviderState.ACCEPTED, provider="kie",
            request_hash=attempt.request_hash,
            provider_task_ref=task_ref.strip(),
            status_locator=RuntimeKieMediaProvider.status_locator,
            evidence={
                "provider_state": "accepted",
                "provider_request_hash": provider_hash,
            },
        )
    if response.status_code >= 500 or (
        response.status_code == 200 and code == 200
    ):
        return _unknown(
            attempt, "KIE_SUBMIT_RESPONSE_AMBIGUOUS",
            provider_request_hash=provider_hash,
        )
    return _failed(
        attempt, "KIE_SUBMIT_REJECTED",
        provider_request_hash=provider_hash,
        provider_code=code,
    )


def _readback_receipt(
    attempt: ActionAttempt, response: KieHttpResponse, provider_ref: str,
    provider_hash: str, kind: str,
) -> ProviderReceipt:
    payload = response.payload
    data = payload.get("data")
    if response.status_code >= 500 or not isinstance(data, Mapping):
        return _unknown(
            attempt, "KIE_READBACK_RESPONSE_AMBIGUOUS",
            provider_task_ref=provider_ref,
            provider_request_hash=provider_hash,
        )
    if payload.get("code") != 200:
        return _failed(
            attempt, "KIE_READBACK_REJECTED",
            provider_task_ref=provider_ref,
            provider_request_hash=provider_hash,
            provider_code=payload.get("code"),
        )
    returned_ref = data.get("taskId")
    if returned_ref is not None and returned_ref != provider_ref:
        return _unknown(
            attempt, "KIE_READBACK_TASK_REF_CONFLICT",
            provider_task_ref=provider_ref,
            provider_request_hash=provider_hash,
        )
    state = str(data.get("state") or "").lower()
    evidence = {
        "provider_state": state or "unknown",
        "provider_request_hash": provider_hash,
    }
    if state in {"waiting", "queuing", "generating"}:
        return ProviderReceipt(
            state=ProviderState.ACCEPTED, provider="kie",
            request_hash=attempt.request_hash,
            provider_task_ref=provider_ref,
            status_locator=RuntimeKieMediaProvider.status_locator,
            evidence=evidence,
        )
    if state == "fail":
        return ProviderReceipt(
            state=ProviderState.FAILED, provider="kie",
            request_hash=attempt.request_hash,
            provider_task_ref=provider_ref,
            status_locator=RuntimeKieMediaProvider.status_locator,
            evidence={**evidence, "error_code": "KIE_TASK_FAILED"},
        )
    if state == "success":
        urls = _strict_result_urls(data.get("resultJson"))
        if urls is None:
            return _unknown(
                attempt, "KIE_RESULT_URLS_AMBIGUOUS",
                provider_task_ref=provider_ref,
                provider_request_hash=provider_hash,
            )
        return ProviderReceipt(
            state=ProviderState.COMPLETED, provider="kie",
            request_hash=attempt.request_hash,
            provider_task_ref=provider_ref,
            status_locator=RuntimeKieMediaProvider.status_locator,
            result=({"image_urls": urls} if kind == "image" else {"urls": urls}),
            evidence=evidence,
        )
    return _unknown(
        attempt, "KIE_READBACK_STATE_UNKNOWN",
        provider_task_ref=provider_ref,
        provider_request_hash=provider_hash,
    )


def _strict_result_urls(value: object) -> list[str] | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(parsed, Mapping):
        return None
    urls = parsed.get("resultUrls")
    if not isinstance(urls, list) or not urls or not all(
        isinstance(url, str) and url.startswith("https://")
        and not any(char.isspace() for char in url)
        for url in urls
    ):
        return None
    return list(urls)


def _provider_ref(receipt: Mapping[str, object]) -> str | None:
    value = receipt.get("provider_task_ref")
    return value.strip() if isinstance(value, str) and value.strip() else None


def _unknown(
    attempt: ActionAttempt, code: str, *, provider_task_ref: str | None = None,
    provider_request_hash: str | None = None,
) -> ProviderReceipt:
    evidence: dict[str, object] = {"error_code": code}
    if provider_request_hash is not None:
        evidence["provider_request_hash"] = provider_request_hash
    return ProviderReceipt(
        state=ProviderState.UNKNOWN, provider="kie",
        request_hash=attempt.request_hash,
        provider_task_ref=provider_task_ref,
        status_locator=(RuntimeKieMediaProvider.status_locator
                        if provider_task_ref else None),
        evidence=evidence,
    )


def _failed(
    attempt: ActionAttempt, code: str, *, provider_task_ref: str | None = None,
    provider_request_hash: str | None = None,
    provider_code: object = None,
) -> ProviderReceipt:
    evidence: dict[str, object] = {"error_code": code}
    if provider_request_hash is not None:
        evidence["provider_request_hash"] = provider_request_hash
    if isinstance(provider_code, (str, int)) and not isinstance(provider_code, bool):
        evidence["provider_code"] = provider_code
    return ProviderReceipt(
        state=ProviderState.FAILED, provider="kie",
        request_hash=attempt.request_hash,
        provider_task_ref=provider_task_ref,
        status_locator=(RuntimeKieMediaProvider.status_locator
                        if provider_task_ref else None),
        evidence=evidence,
    )


__all__ = [
    "RuntimeKieCredentialSource", "RuntimeKieMediaProvider",
    "RuntimeMediaTaskPort",
]
