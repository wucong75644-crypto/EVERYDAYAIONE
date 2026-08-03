"""Runtime-owned ERP adapter for isolated providers only.

The adapter is deliberately not wired into production composition.  It owns
the facts boundary and accepts only a provider explicitly marked isolated;
legacy dispatchers and credential discovery cannot be passed here.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Mapping, Protocol

from services.agent.runtime.domain import ActionAttempt
from services.agent.runtime.executors.contracts import canonical_json
from services.agent.runtime.executors.provider_adapters import _valid_erp_action
from services.agent.runtime.executors.specialist_contracts import (
    ProviderReceipt, ProviderState, SpecialistProvider, validate_public_request,
)
from services.agent.runtime.provider_facts import (
    ProviderFactState, ProviderFactsError, ProviderSubmissionContext,
)


class IsolatedErpProvider(SpecialistProvider, Protocol):
    isolated_only: bool
    production_ready: bool


@dataclass(frozen=True, kw_only=True)
class ErpAdapterReadiness:
    service_wiring_ready: bool
    credential_backend_ready: bool
    provider_ready: bool
    production_ready: bool
    error_code: str | None = None

    @property
    def ready(self) -> bool:
        return all((
            self.service_wiring_ready, self.credential_backend_ready,
            self.provider_ready, self.production_ready,
        ))


class MockErpProvider:
    """Deterministic isolated provider; it never reports production readiness."""

    isolated_only = True
    production_ready = False

    async def submit(self, attempt: ActionAttempt, request: Mapping[str, object], *, idempotency_key: str) -> ProviderReceipt:
        validate_public_request(request)
        task_ref = "mock-erp-" + hashlib.sha256(idempotency_key.encode()).hexdigest()[:24]
        return ProviderReceipt(
            state=ProviderState.ACCEPTED, provider="erp-mock",
            request_hash=attempt.request_hash, provider_task_ref=task_ref,
            status_locator="mock://erp/status/" + task_ref,
            evidence={"isolated": True},
        )

    async def reconcile(self, attempt: ActionAttempt, receipt: Mapping[str, object]) -> ProviderReceipt:
        validate_public_request(receipt)
        return ProviderReceipt(
            state=ProviderState.COMPLETED, provider="erp-mock",
            request_hash=attempt.request_hash,
            result={"summary": "isolated ERP readback", "data": []},
            evidence={"isolated": True, "readback": True},
        )

    async def cancel(self, attempt: ActionAttempt, receipt: Mapping[str, object]) -> ProviderReceipt:
        validate_public_request(receipt)
        return ProviderReceipt(
            state=ProviderState.CANCELLED, provider="erp-mock",
            request_hash=attempt.request_hash,
            evidence={"isolated": True, "cancel_confirmed": True},
        )


class RuntimeErpAdapter(SpecialistProvider):
    """ERP provider adapter with durable facts and no credential discovery."""

    def __init__(
        self, *, facts: object, provider: IsolatedErpProvider,
        provider_revision: str,
    ) -> None:
        if not getattr(provider, "isolated_only", False):
            raise RuntimeError("ERP_ISOLATED_PROVIDER_REQUIRED")
        if getattr(provider, "production_ready", True):
            raise RuntimeError("ERP_PRODUCTION_PROVIDER_FORBIDDEN_IN_A3")
        if not isinstance(provider_revision, str) or not provider_revision.strip():
            raise RuntimeError("ERP_PROVIDER_REVISION_REQUIRED")
        self.facts = facts
        self.provider = provider
        self.provider_revision = provider_revision

    @property
    def readiness(self) -> ErpAdapterReadiness:
        return ErpAdapterReadiness(
            service_wiring_ready=True,
            credential_backend_ready=False,
            provider_ready=True,
            production_ready=False,
            error_code="CREDENTIAL_BACKEND_NOT_READY",
        )

    async def submit(
        self, attempt: ActionAttempt, request: Mapping[str, object], *, idempotency_key: str,
    ) -> ProviderReceipt:
        self._validate_attempt(attempt, request)
        context = self._context(attempt, idempotency_key)
        outcome, fact = await self.facts.create(context)
        state, submission_id, state_version, task_ref = _fact_values(outcome, fact)
        if outcome == "already_applied" and state is not ProviderFactState.SUBMISSION_PENDING:
            return _receipt_from_fact(attempt, state, submission_id, state_version, task_ref)
        try:
            provider_receipt = await self.provider.submit(
                attempt, request, idempotency_key=idempotency_key,
            )
        except Exception:
            return await self._unknown(
                attempt, submission_id, state_version,
                {"error_code": "ERP_PROVIDER_SUBMIT_UNKNOWN"},
            )
        if provider_receipt.request_hash != attempt.request_hash:
            return await self._unknown(
                attempt, submission_id, state_version,
                {"error_code": "ERP_PROVIDER_REQUEST_HASH_CONFLICT"},
            )
        if provider_receipt.state is ProviderState.ACCEPTED:
            submitted = await self.facts.submitted(
                submission_id=submission_id, execution_token=context.execution_token,
                request_hash=context.request_hash, expected_state_version=state_version,
                provider_task_ref=provider_receipt.provider_task_ref or "",
                status_locator=provider_receipt.status_locator,
            )
            return _receipt_from_fact(
                attempt, ProviderFactState.ACCEPTED, submission_id,
                _state_version(submitted), provider_receipt.provider_task_ref,
                evidence=provider_receipt.evidence,
            )
        return await self._unknown(
            attempt, submission_id, state_version,
            {"error_code": "ERP_PROVIDER_RESULT_NOT_CONFIRMED"},
        )

    async def reconcile(self, attempt: ActionAttempt, receipt: Mapping[str, object]) -> ProviderReceipt:
        self._validate_attempt(attempt, receipt, require_action=False)
        submission_id = _required_text(receipt.get("submission_id"), "ERP_SUBMISSION_ID_REQUIRED")
        state_version = _required_int(receipt.get("state_version"), "ERP_FACT_STATE_VERSION_REQUIRED")
        provider_receipt = await self.provider.reconcile(attempt, receipt)
        try:
            fact = await self.facts.readback(
                submission_id=submission_id, execution_token=_execution_token(attempt),
                request_hash=attempt.request_hash, expected_state_version=state_version,
                provider_state=_provider_state(provider_receipt.state),
                readback_hash=_hash_receipt(provider_receipt),
                evidence=provider_receipt.evidence,
                provider_task_ref=provider_receipt.provider_task_ref,
            )
        except Exception:
            return _unknown_receipt(attempt, "ERP_READBACK_FACT_WRITE_FAILED")
        return _receipt_from_fact(
            attempt, _fact_state(fact), submission_id, _state_version(fact),
            provider_receipt.provider_task_ref,
            result=provider_receipt.result, evidence=provider_receipt.evidence,
            completed=provider_receipt.state is ProviderState.COMPLETED,
        )

    async def cancel(self, attempt: ActionAttempt, receipt: Mapping[str, object]) -> ProviderReceipt:
        self._validate_attempt(attempt, receipt, require_action=False)
        submission_id = _required_text(receipt.get("submission_id"), "ERP_SUBMISSION_ID_REQUIRED")
        state_version = _required_int(receipt.get("state_version"), "ERP_FACT_STATE_VERSION_REQUIRED")
        context = self._context(attempt, str(receipt.get("external_idempotency_key", "cancel")))
        try:
            requested = await self.facts.request_cancel(
                submission_id=submission_id, execution_token=context.execution_token,
                request_hash=context.request_hash, expected_state_version=state_version,
                reason="runtime_cancel",
            )
            provider_receipt = await self.provider.cancel(attempt, receipt)
            fact = await self.facts.readback(
                submission_id=submission_id, execution_token=context.execution_token,
                request_hash=context.request_hash, expected_state_version=_state_version(requested),
                provider_state="cancelled" if provider_receipt.state is ProviderState.CANCELLED else "unknown",
                readback_hash=_hash_receipt(provider_receipt),
                evidence=provider_receipt.evidence or {"error_code": "ERP_CANCEL_UNCONFIRMED"},
            )
        except Exception:
            return _unknown_receipt(attempt, "ERP_CANCEL_FACT_OR_PROVIDER_UNKNOWN")
        return _receipt_from_fact(
            attempt, _fact_state(fact), submission_id, _state_version(fact),
            provider_receipt.provider_task_ref, evidence=provider_receipt.evidence,
        )

    def _validate_attempt(
        self, attempt: ActionAttempt, request: Mapping[str, object], *,
        require_action: bool = True,
    ) -> None:
        if not getattr(attempt, "run_id", None):
            raise RuntimeError("ERP_RUN_CONTEXT_REQUIRED")
        if not getattr(attempt, "scope", None) or not getattr(attempt.scope, "scope_id", None):
            raise RuntimeError("ERP_SCOPE_REQUIRED")
        validate_public_request(request)
        if require_action:
            action = request.get("action")
            tool_name = request.get("tool_name", "erp_execute")
            write = tool_name == "erp_execute"
            if not isinstance(action, str) or not _valid_erp_action(str(tool_name), action, write=write):
                raise RuntimeError("ERP_ACTION_NOT_REGISTERED")

    def _context(self, attempt: ActionAttempt, idempotency_key: str) -> ProviderSubmissionContext:
        scope = attempt.scope
        return ProviderSubmissionContext(
            attempt_id=str(attempt.attempt_id), action_id=str(attempt.action_id),
            run_id=str(attempt.run_id), org_id=scope.org_id, user_id=scope.user_id,
            scope_kind=scope.kind.value, scope_id=scope.scope_id,
            execution_token=str(attempt.lease.fencing_token), request_hash=attempt.request_hash,
            provider="erp-mock", provider_revision=self.provider_revision,
            external_idempotency_key=idempotency_key,
        )

    async def _unknown(self, attempt: ActionAttempt, submission_id: str,
                       state_version: int, evidence: Mapping[str, object]) -> ProviderReceipt:
        try:
            fact = await self.facts.unknown(
                submission_id=submission_id, execution_token=_execution_token(attempt),
                request_hash=attempt.request_hash, expected_state_version=state_version,
                evidence=evidence,
            )
            return _receipt_from_fact(attempt, ProviderFactState.UNKNOWN,
                                      submission_id, _state_version(fact), None, evidence=evidence)
        except Exception:
            return _unknown_receipt(attempt, "ERP_UNKNOWN_FACT_WRITE_FAILED")


def _fact_values(outcome: str, fact: object) -> tuple[ProviderFactState, str, int, str | None]:
    if isinstance(fact, Mapping):
        state = ProviderFactState(str(fact.get("state", "submission_pending")))
        return state, _required_text(fact.get("submission_id"), "ERP_SUBMISSION_ID_REQUIRED"), int(fact.get("state_version", 0)), _text(fact.get("provider_task_ref"))
    return _fact_state(fact), str(fact.submission_id), int(fact.state_version), _text(fact.provider_task_ref)


def _receipt_from_fact(
    attempt: ActionAttempt, state: ProviderFactState, submission_id: str,
    state_version: int, task_ref: str | None, *, result: Mapping[str, object] | None = None,
    evidence: Mapping[str, object] | None = None, completed: bool = False,
) -> ProviderReceipt:
    provider_state = ProviderState.COMPLETED if completed or state is ProviderFactState.READBACK_CONFIRMED else {
        ProviderFactState.SUBMITTED: ProviderState.ACCEPTED,
        ProviderFactState.ACCEPTED: ProviderState.ACCEPTED,
        ProviderFactState.CANCELLED: ProviderState.CANCELLED,
        ProviderFactState.FAILED: ProviderState.FAILED,
    }.get(state, ProviderState.UNKNOWN)
    return ProviderReceipt(
        state=provider_state, provider="erp-mock", request_hash=attempt.request_hash,
        provider_task_ref=task_ref, result=dict(result or {}),
        evidence={"submission_id": submission_id, "state_version": state_version, **dict(evidence or {})},
    )


def _unknown_receipt(attempt: ActionAttempt, code: str) -> ProviderReceipt:
    return ProviderReceipt(state=ProviderState.UNKNOWN, provider="erp-mock",
                           request_hash=attempt.request_hash, evidence={"error_code": code})


def _fact_state(value: object) -> ProviderFactState:
    state = value.state if hasattr(value, "state") else value.get("state")
    return state if isinstance(state, ProviderFactState) else ProviderFactState(str(state))


def _state_version(value: object) -> int:
    return int(value.state_version if hasattr(value, "state_version") else value.get("state_version", 0))


def _execution_token(attempt: ActionAttempt) -> str:
    return str(attempt.lease.fencing_token)


def _provider_state(state: ProviderState) -> str:
    return {ProviderState.COMPLETED: "completed", ProviderState.ACCEPTED: "accepted",
            ProviderState.FAILED: "failed", ProviderState.CANCELLED: "cancelled",
            ProviderState.UNKNOWN: "unknown"}.get(state, "unknown")


def _hash_receipt(receipt: ProviderReceipt) -> str:
    return hashlib.sha256(canonical_json({
        "provider": receipt.provider, "state": receipt.state.value,
        "provider_task_ref": receipt.provider_task_ref,
        "result": dict(receipt.result), "evidence": dict(receipt.evidence),
    }).encode()).hexdigest()


def _required_text(value: object, error: str) -> str:
    text = value if isinstance(value, str) else ""
    if not text.strip():
        raise RuntimeError(error)
    return text


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _required_int(value: object, error: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RuntimeError(error)
    return value


__all__ = ["ErpAdapterReadiness", "IsolatedErpProvider", "MockErpProvider", "RuntimeErpAdapter"]
