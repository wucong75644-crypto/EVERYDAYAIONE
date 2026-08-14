"""Runtime-owned KIE image/video provider with reconcile-only recovery."""

from __future__ import annotations

from typing import Mapping, Protocol

from services.agent.runtime.domain import ActionAttempt
from services.agent.runtime.executors.specialist_contracts import (
    ProviderReceipt, ProviderState, SpecialistProvider,
)
from services.agent.runtime.providers.kie_transport import KieOneShotTransport
from services.agent.runtime.providers.kie_media_receipts import (
    STATUS_LOCATOR, cancel_unproven as _cancel_unproven,
    failed as _failed, provider_ref as _provider_ref,
    readback_receipt as _readback_receipt,
    receipt_evidence_text as _receipt_evidence_text,
    receipt_integer as _receipt_integer, receipt_text as _receipt_text,
    request_facts as _request_facts, submit_receipt as _submit_receipt,
    unknown as _unknown,
)
from services.agent.runtime.providers.kie_media_facts import (
    KieFactIdentity, cancel_requested_fact, create_fact, latest_fact,
    readback_fact, receipt_identity, rejected_fact, submitted_fact,
    unknown_fact, with_fact,
)


class RuntimeMediaTaskPort(Protocol):
    async def prepare(
        self, attempt: ActionAttempt, *, kind: str,
    ) -> Mapping[str, object]: ...

    async def read(
        self, attempt: ActionAttempt, *, kind: str,
        owner_token: str | None = None,
        expected_state_version: int | None = None,
    ) -> Mapping[str, object]: ...


class RuntimeKieCredentialSource(Protocol):
    async def api_key(
        self, attempt: ActionAttempt, *, provider_request_hash: str,
        owner_token: str | None = None,
        expected_state_version: int | None = None,
    ) -> str: ...


class RuntimeKieMediaProvider(SpecialistProvider):
    provider = "kie"
    status_locator = STATUS_LOCATOR

    def __init__(
        self, transport: KieOneShotTransport, *, task_port: RuntimeMediaTaskPort,
        credentials: RuntimeKieCredentialSource | None, kind: str,
        production_ready: bool = False, recovery_ready: bool | None = None,
        facts: object | None = None,
    ) -> None:
        if kind not in {"image", "video"}:
            raise ValueError("KIE_MEDIA_KIND_INVALID")
        self._transport = transport
        self._task_port = task_port
        self._credentials = credentials
        self._facts = facts
        self._kind = kind
        self.production_ready = production_ready
        self.recovery_ready = (
            production_ready if recovery_ready is None else recovery_ready
        )

    async def submit(
        self, attempt: ActionAttempt, request: Mapping[str, object], *,
        idempotency_key: str,
    ) -> ProviderReceipt:
        del request
        status = getattr(attempt, "status", None)
        status_value = getattr(status, "value", status)
        if status_value is not None and str(status_value) not in {"claimed", "dispatching"}:
            raise RuntimeError("KIE_MEDIA_SUBMIT_PHASE_INVALID")
        if (
            not self.production_ready or self._credentials is None
            or self._facts is None
        ):
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
            fact_outcome, fact = await create_fact(
                self._facts, attempt, idempotency_key,
            )
        except Exception:
            return _failed(attempt, "KIE_MEDIA_PROVIDER_FACTS_UNAVAILABLE")
        if fact_outcome != "created":
            return with_fact(
                _unknown(
                    attempt, "KIE_SUBMISSION_FACT_REQUIRES_READBACK",
                    provider_task_ref=fact.provider_task_ref,
                    provider_request_hash=provider_hash,
                    provider_idempotency_key=idempotency_key,
                ),
                fact, provider_request_hash=provider_hash,
                provider_idempotency_key=idempotency_key,
            )
        try:
            response = await self._transport.submit(
                api_key=api_key, body=provider_request,
                idempotency_key=idempotency_key,
            )
        except Exception:
            receipt = _unknown(
                attempt, "KIE_SUBMIT_RESULT_UNKNOWN",
                provider_request_hash=provider_hash,
                provider_idempotency_key=idempotency_key,
            )
            try:
                fact = await unknown_fact(
                    self._facts, attempt, fact, receipt.evidence,
                )
            except Exception:
                receipt = _unknown(
                    attempt, "KIE_SUBMIT_FACT_WRITE_UNKNOWN",
                    provider_request_hash=provider_hash,
                    provider_idempotency_key=idempotency_key,
                )
            return with_fact(
                receipt, fact, provider_request_hash=provider_hash,
                provider_idempotency_key=idempotency_key,
            )
        receipt = _submit_receipt(
            attempt, response, provider_hash, idempotency_key,
        )
        try:
            if receipt.provider_task_ref:
                fact = await submitted_fact(
                    self._facts, attempt, fact, receipt,
                )
            if receipt.state is ProviderState.COMPLETED:
                fact = await readback_fact(
                    self._facts, attempt, fact, receipt,
                )
            elif receipt.state is ProviderState.UNKNOWN:
                fact = await unknown_fact(
                    self._facts, attempt, fact, receipt.evidence,
                )
            elif receipt.state is ProviderState.FAILED:
                fact = await rejected_fact(
                    self._facts, attempt, fact, receipt.evidence,
                )
        except Exception:
            receipt = _unknown(
                attempt, "KIE_SUBMIT_FACT_WRITE_UNKNOWN",
                provider_task_ref=receipt.provider_task_ref,
                provider_request_hash=provider_hash,
                provider_idempotency_key=idempotency_key,
            )
        return with_fact(
            receipt, fact, provider_request_hash=provider_hash,
            provider_idempotency_key=idempotency_key,
        )

    async def reconcile(
        self, attempt: ActionAttempt, receipt: Mapping[str, object],
    ) -> ProviderReceipt:
        status = getattr(attempt, "status", None)
        status_value = getattr(status, "value", status)
        if status_value is not None and str(status_value) not in {"accepted", "unknown"}:
            return _unknown(attempt, "KIE_MEDIA_RECONCILE_PHASE_INVALID")
        if (
            not self.recovery_ready or self._credentials is None
            or self._facts is None
        ):
            return _unknown(attempt, "KIE_MEDIA_PROVIDER_NOT_READY")
        try:
            fact, provider_ref, persisted_hash, provider_key = (
                await self._latest_fact(attempt, receipt)
            )
        except Exception:
            return _unknown(
                attempt, "KIE_PROVIDER_FACT_IDENTITY_REQUIRED",
                provider_task_ref=_provider_ref(receipt),
            )
        return await self._readback(
            attempt, receipt, fact=fact, provider_ref=provider_ref,
            persisted_hash=persisted_hash, provider_key=provider_key,
            cancel_unproven=_cancel_unproven(receipt) or fact.cancel_requested,
        )

    async def _latest_fact(
        self, attempt: ActionAttempt, receipt: Mapping[str, object],
    ) -> tuple[KieFactIdentity, str | None, str, str]:
        if self._facts is None:
            raise RuntimeError("KIE_PROVIDER_FACTS_REQUIRED")
        fact = receipt_identity(receipt)
        provider_key = _receipt_evidence_text(
            receipt, "provider_idempotency_key",
        )
        persisted_hash = _receipt_evidence_text(
            receipt, "provider_request_hash",
        )
        fact = await latest_fact(self._facts, attempt, fact, provider_key)
        return (
            fact, fact.provider_task_ref or _provider_ref(receipt),
            persisted_hash, provider_key,
        )

    async def _readback(
        self, attempt: ActionAttempt, receipt: Mapping[str, object], *,
        fact: KieFactIdentity, provider_ref: str | None,
        persisted_hash: str, provider_key: str, cancel_unproven: bool,
    ) -> ProviderReceipt:
        if provider_ref is None:
            return with_fact(
                _unknown(
                    attempt, "KIE_READBACK_REF_REQUIRED",
                    provider_request_hash=persisted_hash,
                    provider_idempotency_key=provider_key,
                ),
                fact, provider_request_hash=persisted_hash,
                provider_idempotency_key=provider_key,
                cancel_unproven=cancel_unproven,
            )
        try:
            request_facts = await self._task_port.read(
                attempt, kind=self._kind,
                owner_token=_receipt_text(receipt, "reconciliation_token"),
                expected_state_version=_receipt_integer(
                    receipt, "reconciliation_state_version",
                ),
            )
            _, provider_hash = _request_facts(request_facts)
            api_key = await self._credentials.api_key(
                attempt, provider_request_hash=provider_hash,
                owner_token=_receipt_text(receipt, "reconciliation_token"),
                expected_state_version=_receipt_integer(
                    receipt, "reconciliation_state_version",
                ),
            )
        except Exception:
            return with_fact(
                _unknown(
                    attempt, "KIE_READBACK_CONFIGURATION_UNAVAILABLE",
                    provider_task_ref=provider_ref,
                    provider_request_hash=persisted_hash,
                    provider_idempotency_key=provider_key,
                ),
                fact, provider_request_hash=persisted_hash,
                provider_idempotency_key=provider_key,
                cancel_unproven=cancel_unproven,
            )
        try:
            response = await self._transport.query(
                api_key=api_key, provider_task_ref=provider_ref,
            )
        except Exception:
            return with_fact(
                _unknown(
                    attempt, "KIE_READBACK_RESULT_UNKNOWN",
                    provider_task_ref=provider_ref,
                    provider_request_hash=provider_hash,
                    provider_idempotency_key=provider_key,
                ),
                fact, provider_request_hash=provider_hash,
                provider_idempotency_key=provider_key,
                cancel_unproven=cancel_unproven,
            )
        provider_receipt = _readback_receipt(
            attempt, response, provider_ref, provider_hash, self._kind,
        )
        try:
            fact = await readback_fact(
                self._facts, attempt, fact, provider_receipt,
            )
        except Exception:
            provider_receipt = _unknown(
                attempt, "KIE_READBACK_FACT_WRITE_UNKNOWN",
                provider_task_ref=provider_ref,
                provider_request_hash=provider_hash,
                provider_idempotency_key=provider_key,
            )
        if cancel_unproven and provider_receipt.state is ProviderState.ACCEPTED:
            provider_receipt = _unknown(
                attempt, "KIE_CANCEL_UNPROVEN_PROVIDER_PENDING",
                provider_task_ref=provider_ref,
                provider_request_hash=provider_hash,
                provider_idempotency_key=provider_key,
            )
        return with_fact(
            provider_receipt, fact, provider_request_hash=provider_hash,
            provider_idempotency_key=provider_key,
            cancel_unproven=cancel_unproven,
        )

    async def cancel(
        self, attempt: ActionAttempt, receipt: Mapping[str, object],
    ) -> ProviderReceipt:
        # KIE documents no generally verifiable cancel endpoint for this API.
        # Never infer cancellation and never issue an unverified network call.
        provider_ref = _provider_ref(receipt)
        try:
            fact, provider_ref, provider_hash, provider_key = (
                await self._latest_fact(attempt, receipt)
            )
            if fact.cancel_requested:
                return await self._readback(
                    attempt, receipt, fact=fact, provider_ref=provider_ref,
                    persisted_hash=provider_hash, provider_key=provider_key,
                    cancel_unproven=True,
                )
            fact = await cancel_requested_fact(
                self._facts, attempt, fact,
            )
        except Exception:
            return _unknown(
                attempt, "KIE_CANCEL_FACT_WRITE_UNKNOWN",
                provider_task_ref=provider_ref,
            )
        return with_fact(
            _unknown(
                attempt, "CANCEL_UNPROVEN", provider_task_ref=provider_ref,
                provider_request_hash=provider_hash,
                provider_idempotency_key=provider_key,
            ),
            fact, provider_request_hash=provider_hash,
            provider_idempotency_key=provider_key, cancel_unproven=True,
        )


__all__ = [
    "RuntimeKieCredentialSource", "RuntimeKieMediaProvider",
    "RuntimeMediaTaskPort",
]
