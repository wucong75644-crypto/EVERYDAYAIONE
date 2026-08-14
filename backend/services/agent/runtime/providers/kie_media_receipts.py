"""Strict KIE media request and receipt parsing without Provider I/O."""

from __future__ import annotations

import json
from typing import Mapping

from services.agent.runtime.domain import ActionAttempt
from services.agent.runtime.executors.specialist_contracts import (
    ProviderReceipt, ProviderState,
)
from services.agent.runtime.providers.kie_transport import KieHttpResponse


STATUS_LOCATOR = "/api/v1/jobs/recordInfo"


def request_facts(
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


def submit_receipt(
    attempt: ActionAttempt, response: KieHttpResponse,
    provider_hash: str, provider_idempotency_key: str,
) -> ProviderReceipt:
    payload = response.payload
    code = payload.get("code")
    data = payload.get("data")
    task_ref = data.get("taskId") if isinstance(data, Mapping) else None
    state = str(data.get("state") if isinstance(data, Mapping) else "").lower()
    if response.status_code == 200 and code == 200 and state == "fail":
        return failed(
            attempt, "KIE_SUBMIT_REJECTED", provider_request_hash=provider_hash,
            provider_code=payload.get("code"),
        )
    if response.status_code == 200 and code == 200 and state == "success":
        urls = _strict_result_urls(
            data.get("resultJson") if isinstance(data, Mapping) else None,
        )
        if urls is None:
            return unknown(
                attempt, "KIE_RESULT_URLS_AMBIGUOUS",
                provider_request_hash=provider_hash,
            )
        return ProviderReceipt(
            state=ProviderState.COMPLETED, provider="kie",
            request_hash=attempt.request_hash, provider_task_ref=task_ref,
            status_locator=STATUS_LOCATOR, result={"image_urls": urls},
            evidence={
                "provider_state": state,
                "provider_request_hash": provider_hash,
                "provider_idempotency_key": provider_idempotency_key,
            },
        )
    if (
        response.status_code == 200 and code == 200
        and isinstance(task_ref, str) and task_ref.strip()
    ):
        return ProviderReceipt(
            state=ProviderState.ACCEPTED, provider="kie",
            request_hash=attempt.request_hash,
            provider_task_ref=task_ref.strip(), status_locator=STATUS_LOCATOR,
            evidence={
                "provider_state": "accepted",
                "provider_request_hash": provider_hash,
                "provider_idempotency_key": provider_idempotency_key,
            },
        )
    if response.status_code >= 500 or (
        response.status_code == 200 and code == 200
    ):
        return unknown(
            attempt, "KIE_SUBMIT_RESPONSE_AMBIGUOUS",
            provider_request_hash=provider_hash,
            provider_idempotency_key=provider_idempotency_key,
        )
    return failed(
        attempt, "KIE_SUBMIT_REJECTED",
        provider_request_hash=provider_hash,
        provider_idempotency_key=provider_idempotency_key,
        provider_code=code,
    )


def readback_receipt(
    attempt: ActionAttempt, response: KieHttpResponse, provider_ref: str,
    provider_hash: str, kind: str,
) -> ProviderReceipt:
    payload = response.payload
    data = payload.get("data")
    if response.status_code >= 500 or not isinstance(data, Mapping):
        return unknown(
            attempt, "KIE_READBACK_RESPONSE_AMBIGUOUS",
            provider_task_ref=provider_ref,
            provider_request_hash=provider_hash,
        )
    if payload.get("code") != 200:
        return failed(
            attempt, "KIE_READBACK_REJECTED",
            provider_task_ref=provider_ref,
            provider_request_hash=provider_hash,
            provider_code=payload.get("code"),
        )
    returned_ref = data.get("taskId")
    if returned_ref is not None and returned_ref != provider_ref:
        return unknown(
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
            provider_task_ref=provider_ref, status_locator=STATUS_LOCATOR,
            evidence=evidence,
        )
    if state == "fail":
        return ProviderReceipt(
            state=ProviderState.FAILED, provider="kie",
            request_hash=attempt.request_hash,
            provider_task_ref=provider_ref, status_locator=STATUS_LOCATOR,
            evidence={**evidence, "error_code": "KIE_TASK_FAILED"},
        )
    if state == "success":
        urls = _strict_result_urls(data.get("resultJson"))
        if urls is None:
            return unknown(
                attempt, "KIE_RESULT_URLS_AMBIGUOUS",
                provider_task_ref=provider_ref,
                provider_request_hash=provider_hash,
            )
        return ProviderReceipt(
            state=ProviderState.COMPLETED, provider="kie",
            request_hash=attempt.request_hash,
            provider_task_ref=provider_ref, status_locator=STATUS_LOCATOR,
            result=({"image_urls": urls} if kind == "image" else {"urls": urls}),
            evidence=evidence,
        )
    if state in {"cancel", "cancelled"}:
        return ProviderReceipt(
            state=ProviderState.CANCELLED, provider="kie",
            request_hash=attempt.request_hash,
            provider_task_ref=provider_ref, status_locator=STATUS_LOCATOR,
            evidence={**evidence, "cancel_confirmed": True},
        )
    return unknown(
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


def provider_ref(receipt: Mapping[str, object]) -> str | None:
    value = receipt.get("provider_task_ref")
    return value.strip() if isinstance(value, str) and value.strip() else None


def receipt_evidence_text(receipt: Mapping[str, object], name: str) -> str:
    evidence = receipt.get("evidence")
    if not isinstance(evidence, Mapping):
        raise RuntimeError("KIE_PROVIDER_RECEIPT_EVIDENCE_REQUIRED")
    return _text(evidence.get(name))


def receipt_text(receipt: Mapping[str, object], name: str) -> str:
    return _text(receipt.get(name))


def receipt_integer(receipt: Mapping[str, object], name: str) -> int:
    value = receipt.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RuntimeError("KIE_RECONCILIATION_VERSION_REQUIRED")
    return value


def _text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError("KIE_PROVIDER_RECEIPT_IDENTITY_REQUIRED")
    return value.strip()


def cancel_unproven(receipt: Mapping[str, object]) -> bool:
    evidence = receipt.get("evidence")
    return isinstance(evidence, Mapping) and (
        evidence.get("cancel_unproven") is True
        or evidence.get("error_code") == "CANCEL_UNPROVEN"
    )


def unknown(
    attempt: ActionAttempt, code: str, *, provider_task_ref: str | None = None,
    provider_request_hash: str | None = None,
    provider_idempotency_key: str | None = None,
) -> ProviderReceipt:
    evidence: dict[str, object] = {"error_code": code}
    if provider_request_hash is not None:
        evidence["provider_request_hash"] = provider_request_hash
    if provider_idempotency_key is not None:
        evidence["provider_idempotency_key"] = provider_idempotency_key
    return ProviderReceipt(
        state=ProviderState.UNKNOWN, provider="kie",
        request_hash=attempt.request_hash,
        provider_task_ref=provider_task_ref,
        status_locator=(STATUS_LOCATOR if provider_task_ref else None),
        evidence=evidence,
    )


def failed(
    attempt: ActionAttempt, code: str, *, provider_task_ref: str | None = None,
    provider_request_hash: str | None = None,
    provider_idempotency_key: str | None = None,
    provider_code: object = None,
) -> ProviderReceipt:
    evidence: dict[str, object] = {"error_code": code}
    if provider_request_hash is not None:
        evidence["provider_request_hash"] = provider_request_hash
    if provider_idempotency_key is not None:
        evidence["provider_idempotency_key"] = provider_idempotency_key
    if isinstance(provider_code, (str, int)) and not isinstance(provider_code, bool):
        evidence["provider_code"] = provider_code
    return ProviderReceipt(
        state=ProviderState.FAILED, provider="kie",
        request_hash=attempt.request_hash,
        provider_task_ref=provider_task_ref,
        status_locator=(STATUS_LOCATOR if provider_task_ref else None),
        evidence=evidence,
    )


__all__ = [
    "STATUS_LOCATOR", "cancel_unproven", "failed", "provider_ref",
    "readback_receipt", "receipt_evidence_text", "receipt_integer",
    "receipt_text", "request_facts", "submit_receipt", "unknown",
]
