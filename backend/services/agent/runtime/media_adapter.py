"""Runtime-owned media completion adapter for isolated providers only."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Mapping, Protocol

from services.agent.runtime.domain import ActionAttempt
from services.agent.runtime.executors.contracts import canonical_json
from services.agent.runtime.executors.specialist_contracts import ProviderReceipt, ProviderState, SpecialistProvider, validate_public_request
from services.agent.runtime.provider_facts import ProviderFactState, ProviderSubmissionContext


class IsolatedMediaProvider(SpecialistProvider, Protocol):
    isolated_only: bool
    production_ready: bool


@dataclass(frozen=True, kw_only=True)
class MediaAdapterReadiness:
    service_wiring_ready: bool
    credential_backend_ready: bool
    provider_ready: bool
    production_ready: bool
    error_code: str | None = None

    @property
    def ready(self) -> bool:
        return all((self.service_wiring_ready, self.credential_backend_ready, self.provider_ready, self.production_ready))


class MockMediaProvider:
    isolated_only = True
    production_ready = False

    def __init__(self) -> None:
        self.submit_calls = self.reconcile_calls = self.cancel_calls = 0

    async def submit(self, attempt: ActionAttempt, request: Mapping[str, object], *, idempotency_key: str) -> ProviderReceipt:
        validate_public_request(request)
        self.submit_calls += 1
        kind = str(request.get("kind", ""))
        ref = f"mock-media-{kind}-" + hashlib.sha256(idempotency_key.encode()).hexdigest()[:24]
        return ProviderReceipt(state=ProviderState.ACCEPTED, provider="media-mock", request_hash=attempt.request_hash,
                               provider_task_ref=ref, status_locator=f"mock://media/status/{ref}", evidence={"isolated": True, "kind": kind})

    async def reconcile(self, attempt: ActionAttempt, receipt: Mapping[str, object]) -> ProviderReceipt:
        validate_public_request(receipt)
        self.reconcile_calls += 1
        return ProviderReceipt(state=ProviderState.COMPLETED, provider="media-mock", request_hash=attempt.request_hash,
                               result={"media_ref": "mock-media-result", "isolated": True}, evidence={"isolated": True, "readback": True})

    async def cancel(self, attempt: ActionAttempt, receipt: Mapping[str, object]) -> ProviderReceipt:
        validate_public_request(receipt)
        self.cancel_calls += 1
        return ProviderReceipt(state=ProviderState.CANCELLED, provider="media-mock", request_hash=attempt.request_hash,
                               evidence={"isolated": True, "cancel_confirmed": True})


class RuntimeMediaAdapter(SpecialistProvider):
    """Media completion owner; terminal facts remain owned by the Runtime facts port."""

    def __init__(self, *, facts: object, provider: IsolatedMediaProvider, provider_revision: str, kind: str) -> None:
        if not getattr(provider, "isolated_only", False):
            raise RuntimeError("MEDIA_ISOLATED_PROVIDER_REQUIRED")
        if getattr(provider, "production_ready", True):
            raise RuntimeError("MEDIA_PRODUCTION_PROVIDER_FORBIDDEN_IN_A4")
        if kind not in {"image", "video"}:
            raise RuntimeError("MEDIA_KIND_INVALID")
        if not isinstance(provider_revision, str) or not provider_revision.strip():
            raise RuntimeError("MEDIA_PROVIDER_REVISION_REQUIRED")
        self.facts, self.provider, self.provider_revision, self.kind = facts, provider, provider_revision, kind

    @property
    def readiness(self) -> MediaAdapterReadiness:
        return MediaAdapterReadiness(service_wiring_ready=True, credential_backend_ready=False, provider_ready=True,
                                     production_ready=False, error_code="CREDENTIAL_BACKEND_NOT_READY")

    async def submit(self, attempt: ActionAttempt, request: Mapping[str, object], *, idempotency_key: str) -> ProviderReceipt:
        self._validate_attempt(attempt, request)
        context = self._context(attempt, idempotency_key)
        outcome, fact = await self.facts.create(context)
        state, submission_id, version, task_ref = _fact_values(fact)
        if outcome == "already_applied" and state is not ProviderFactState.SUBMISSION_PENDING:
            return _receipt(attempt, state, submission_id, version, task_ref)
        try:
            provider_receipt = await self.provider.submit(attempt, request, idempotency_key=idempotency_key)
        except Exception:
            return await self._unknown(attempt, submission_id, version, {"error_code": "MEDIA_PROVIDER_SUBMIT_UNKNOWN"})
        if provider_receipt.request_hash != attempt.request_hash or provider_receipt.state is not ProviderState.ACCEPTED:
            return await self._unknown(attempt, submission_id, version, {"error_code": "MEDIA_PROVIDER_RESULT_NOT_CONFIRMED"})
        fact = await self.facts.submitted(submission_id=submission_id, execution_token=context.execution_token,
            request_hash=context.request_hash, expected_state_version=version,
            provider_task_ref=provider_receipt.provider_task_ref or "", status_locator=provider_receipt.status_locator)
        return _receipt(attempt, ProviderFactState.ACCEPTED, submission_id, _version(fact), provider_receipt.provider_task_ref,
                        evidence=provider_receipt.evidence)

    async def reconcile(self, attempt: ActionAttempt, receipt: Mapping[str, object]) -> ProviderReceipt:
        self._validate_attempt(attempt, receipt, require_kind=False)
        submission_id = _required_text(receipt.get("submission_id"), "MEDIA_SUBMISSION_ID_REQUIRED")
        version = _required_int(receipt.get("state_version"), "MEDIA_FACT_STATE_VERSION_REQUIRED")
        try:
            provider_receipt = await self.provider.reconcile(attempt, receipt)
            fact = await self.facts.readback(submission_id=submission_id, execution_token=_execution_token(attempt),
                request_hash=attempt.request_hash, expected_state_version=version, provider_state=_provider_state(provider_receipt.state),
                readback_hash=_hash_receipt(provider_receipt), evidence=provider_receipt.evidence,
                provider_task_ref=provider_receipt.provider_task_ref)
        except Exception:
            return _unknown_receipt(attempt, "MEDIA_READBACK_FACT_WRITE_FAILED")
        return _receipt(attempt, _state(fact), submission_id, _version(fact), provider_receipt.provider_task_ref,
                        result=provider_receipt.result, evidence=provider_receipt.evidence,
                        completed=provider_receipt.state is ProviderState.COMPLETED)

    async def cancel(self, attempt: ActionAttempt, receipt: Mapping[str, object]) -> ProviderReceipt:
        self._validate_attempt(attempt, receipt, require_kind=False)
        submission_id = _required_text(receipt.get("submission_id"), "MEDIA_SUBMISSION_ID_REQUIRED")
        version = _required_int(receipt.get("state_version"), "MEDIA_FACT_STATE_VERSION_REQUIRED")
        try:
            requested = await self.facts.request_cancel(submission_id=submission_id, execution_token=_execution_token(attempt),
                request_hash=attempt.request_hash, expected_state_version=version, reason="runtime_cancel")
            provider_receipt = await self.provider.cancel(attempt, receipt)
            fact = await self.facts.readback(submission_id=submission_id, execution_token=_execution_token(attempt),
                request_hash=attempt.request_hash, expected_state_version=_version(requested),
                provider_state="cancelled" if provider_receipt.state is ProviderState.CANCELLED else "unknown",
                readback_hash=_hash_receipt(provider_receipt), evidence=provider_receipt.evidence or {"error_code": "MEDIA_CANCEL_UNCONFIRMED"})
        except Exception:
            return _unknown_receipt(attempt, "MEDIA_CANCEL_UNKNOWN")
        return _receipt(attempt, _state(fact), submission_id, _version(fact), None, evidence=provider_receipt.evidence)

    def _validate_attempt(self, attempt: ActionAttempt, request: Mapping[str, object], *, require_kind: bool = True) -> None:
        if not getattr(attempt, "run_id", None):
            raise RuntimeError("MEDIA_RUN_CONTEXT_REQUIRED")
        if not getattr(attempt, "scope", None) or not getattr(attempt.scope, "scope_id", None):
            raise RuntimeError("MEDIA_SCOPE_REQUIRED")
        validate_public_request(request)
        if require_kind and request.get("kind") != self.kind:
            raise RuntimeError("MEDIA_KIND_MISMATCH")

    def _context(self, attempt: ActionAttempt, idempotency_key: str) -> ProviderSubmissionContext:
        scope = attempt.scope
        return ProviderSubmissionContext(attempt_id=str(attempt.attempt_id), action_id=str(attempt.action_id), run_id=str(attempt.run_id),
            org_id=scope.org_id, user_id=scope.user_id, scope_kind=scope.kind.value, scope_id=scope.scope_id,
            execution_token=_execution_token(attempt), request_hash=attempt.request_hash, provider="media-mock",
            provider_revision=self.provider_revision, external_idempotency_key=idempotency_key)

    async def _unknown(self, attempt: ActionAttempt, submission_id: str, version: int, evidence: Mapping[str, object]) -> ProviderReceipt:
        try:
            fact = await self.facts.unknown(submission_id=submission_id, execution_token=_execution_token(attempt),
                request_hash=attempt.request_hash, expected_state_version=version, evidence=evidence)
            return _receipt(attempt, ProviderFactState.UNKNOWN, submission_id, _version(fact), None, evidence=evidence)
        except Exception:
            return _unknown_receipt(attempt, "MEDIA_UNKNOWN_FACT_WRITE_FAILED")


def _receipt(attempt: ActionAttempt, state: ProviderFactState, submission_id: str, version: int, task_ref: str | None, *,
             result: Mapping[str, object] | None = None, evidence: Mapping[str, object] | None = None, completed: bool = False) -> ProviderReceipt:
    provider_state = ProviderState.COMPLETED if completed or state is ProviderFactState.READBACK_CONFIRMED else {
        ProviderFactState.SUBMITTED: ProviderState.ACCEPTED, ProviderFactState.ACCEPTED: ProviderState.ACCEPTED,
        ProviderFactState.CANCELLED: ProviderState.CANCELLED, ProviderFactState.FAILED: ProviderState.FAILED,
    }.get(state, ProviderState.UNKNOWN)
    return ProviderReceipt(state=provider_state, provider="media-mock", request_hash=attempt.request_hash,
        provider_task_ref=task_ref, result=dict(result or {}), evidence={"submission_id": submission_id, "state_version": version, **dict(evidence or {})})


def _fact_values(fact: object) -> tuple[ProviderFactState, str, int, str | None]:
    return _state(fact), str(_value(fact, "submission_id")), _version(fact), _text(_value(fact, "provider_task_ref"))


def _state(value: object) -> ProviderFactState:
    state = _value(value, "state")
    return state if isinstance(state, ProviderFactState) else ProviderFactState(str(state))


def _version(value: object) -> int:
    return int(_value(value, "state_version", 0))


def _value(value: object, name: str, default: object = None) -> object:
    return value.get(name, default) if isinstance(value, Mapping) else getattr(value, name, default)


def _execution_token(attempt: ActionAttempt) -> str:
    return str(attempt.lease.fencing_token)


def _provider_state(state: ProviderState) -> str:
    return {ProviderState.COMPLETED: "completed", ProviderState.ACCEPTED: "accepted", ProviderState.FAILED: "failed",
            ProviderState.CANCELLED: "cancelled", ProviderState.UNKNOWN: "unknown"}.get(state, "unknown")


def _hash_receipt(receipt: ProviderReceipt) -> str:
    return hashlib.sha256(canonical_json({"provider": receipt.provider, "state": receipt.state.value,
        "provider_task_ref": receipt.provider_task_ref, "result": dict(receipt.result), "evidence": dict(receipt.evidence)}).encode()).hexdigest()


def _unknown_receipt(attempt: ActionAttempt, code: str) -> ProviderReceipt:
    return ProviderReceipt(state=ProviderState.UNKNOWN, provider="media-mock", request_hash=attempt.request_hash, evidence={"error_code": code})


def _required_text(value: object, error: str) -> str:
    text = value if isinstance(value, str) else ""
    if not text.strip():
        raise RuntimeError(error)
    return text


def _required_int(value: object, error: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RuntimeError(error)
    return value


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


__all__ = ["IsolatedMediaProvider", "MediaAdapterReadiness", "MockMediaProvider", "RuntimeMediaAdapter"]
