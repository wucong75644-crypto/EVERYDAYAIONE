"""Durable Provider Fact bridge for one-shot Runtime KIE media calls."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from typing import Mapping

from services.agent.runtime.domain import ActionAttempt
from services.agent.runtime.executors.contracts import canonical_json
from services.agent.runtime.executors.specialist_contracts import ProviderReceipt
from services.agent.runtime.provider_facts import ProviderSubmissionContext


@dataclass(frozen=True, kw_only=True)
class KieFactIdentity:
    submission_id: str
    state: str
    state_version: int
    provider_task_ref: str | None = None
    cancel_requested: bool = False


async def create_fact(
    facts: object, attempt: ActionAttempt, idempotency_key: str,
) -> tuple[str, KieFactIdentity]:
    scope = attempt.scope
    context = ProviderSubmissionContext(
        attempt_id=str(attempt.attempt_id), action_id=str(attempt.action_id),
        run_id=str(attempt.run_id), org_id=scope.org_id, user_id=scope.user_id,
        scope_kind=scope.kind.value, scope_id=scope.scope_id,
        execution_token=str(attempt.lease.fencing_token),
        request_hash=attempt.request_hash, provider="kie",
        provider_revision="kie-runtime-media-v1",
        external_idempotency_key=idempotency_key,
    )
    result = await facts.create(context)  # type: ignore[attr-defined]
    if isinstance(result, tuple) and len(result) == 2:
        outcome, value = result
    else:
        outcome, value = _required(result, "outcome"), result
    return str(outcome), _identity(value)


async def latest_fact(
    facts: object, attempt: ActionAttempt, identity: KieFactIdentity,
    idempotency_key: str,
) -> KieFactIdentity:
    value = await facts.read(  # type: ignore[attr-defined]
        _context(attempt, idempotency_key), identity.submission_id,
    )
    return _identity(value, provider_task_ref=identity.provider_task_ref)


async def submitted_fact(
    facts: object, attempt: ActionAttempt, identity: KieFactIdentity,
    receipt: ProviderReceipt,
) -> KieFactIdentity:
    value = await facts.submitted(  # type: ignore[attr-defined]
        submission_id=identity.submission_id,
        execution_token=str(attempt.lease.fencing_token),
        request_hash=attempt.request_hash,
        expected_state_version=identity.state_version,
        provider_task_ref=receipt.provider_task_ref or "",
        status_locator=receipt.status_locator,
        provider_receipt_hash=_receipt_hash(receipt),
    )
    return _identity(value, provider_task_ref=receipt.provider_task_ref)


async def unknown_fact(
    facts: object, attempt: ActionAttempt, identity: KieFactIdentity,
    evidence: Mapping[str, object],
) -> KieFactIdentity:
    value = await facts.unknown(  # type: ignore[attr-defined]
        submission_id=identity.submission_id,
        execution_token=str(attempt.lease.fencing_token),
        request_hash=attempt.request_hash,
        expected_state_version=identity.state_version,
        evidence=dict(evidence),
    )
    return _identity(value, provider_task_ref=identity.provider_task_ref)


async def rejected_fact(
    facts: object, attempt: ActionAttempt, identity: KieFactIdentity,
    evidence: Mapping[str, object],
) -> KieFactIdentity:
    value = await facts.rejected(  # type: ignore[attr-defined]
        submission_id=identity.submission_id,
        execution_token=str(attempt.lease.fencing_token),
        request_hash=attempt.request_hash,
        expected_state_version=identity.state_version,
        evidence=dict(evidence),
    )
    return _identity(value, provider_task_ref=identity.provider_task_ref)


async def cancel_requested_fact(
    facts: object, attempt: ActionAttempt, identity: KieFactIdentity,
) -> KieFactIdentity:
    value = await facts.request_cancel(  # type: ignore[attr-defined]
        submission_id=identity.submission_id,
        execution_token=str(attempt.lease.fencing_token),
        request_hash=attempt.request_hash,
        expected_state_version=identity.state_version,
        reason="runtime_cancel_unproven",
    )
    return _identity(
        value, provider_task_ref=identity.provider_task_ref,
        cancel_requested=True,
    )


async def readback_fact(
    facts: object, attempt: ActionAttempt, identity: KieFactIdentity,
    receipt: ProviderReceipt,
) -> KieFactIdentity:
    value = await facts.readback(  # type: ignore[attr-defined]
        submission_id=identity.submission_id,
        execution_token=str(attempt.lease.fencing_token),
        request_hash=attempt.request_hash,
        expected_state_version=identity.state_version,
        provider_state=receipt.state.value,
        readback_hash=_receipt_hash(receipt),
        provider_task_ref=receipt.provider_task_ref,
        evidence=dict(receipt.evidence),
    )
    return _identity(
        value, provider_task_ref=receipt.provider_task_ref,
        cancel_requested=identity.cancel_requested,
    )


def receipt_identity(receipt: Mapping[str, object]) -> KieFactIdentity:
    evidence = receipt.get("evidence")
    if not isinstance(evidence, Mapping):
        raise RuntimeError("KIE_PROVIDER_FACT_IDENTITY_REQUIRED")
    state = str(evidence.get("provider_fact_state", "unknown"))
    return KieFactIdentity(
        submission_id=_text(evidence.get("submission_id")),
        state=state,
        state_version=_integer(evidence.get("state_version")),
        provider_task_ref=_optional_text(receipt.get("provider_task_ref")),
        cancel_requested=(
            state == "cancel_requested"
            or evidence.get("cancel_unproven") is True
        ),
    )


def _context(
    attempt: ActionAttempt, idempotency_key: str,
) -> ProviderSubmissionContext:
    scope = attempt.scope
    return ProviderSubmissionContext(
        attempt_id=str(attempt.attempt_id), action_id=str(attempt.action_id),
        run_id=str(attempt.run_id), org_id=scope.org_id, user_id=scope.user_id,
        scope_kind=scope.kind.value, scope_id=scope.scope_id,
        execution_token=str(attempt.lease.fencing_token),
        request_hash=attempt.request_hash, provider="kie",
        provider_revision="kie-runtime-media-v1",
        external_idempotency_key=idempotency_key,
    )


def with_fact(
    receipt: ProviderReceipt, identity: KieFactIdentity, *,
    provider_request_hash: str, provider_idempotency_key: str,
    cancel_unproven: bool = False,
) -> ProviderReceipt:
    evidence = {
        **dict(receipt.evidence),
        "submission_id": identity.submission_id,
        "state_version": identity.state_version,
        "provider_fact_state": identity.state,
        "provider_request_hash": provider_request_hash,
        "provider_idempotency_key": provider_idempotency_key,
    }
    if cancel_unproven:
        evidence["cancel_unproven"] = True
    return replace(receipt, evidence=evidence)


def _identity(
    value: object, *, provider_task_ref: str | None = None,
    cancel_requested: bool = False,
) -> KieFactIdentity:
    state = _text(_value(value, "state"))
    return KieFactIdentity(
        submission_id=_text(_value(value, "submission_id")),
        state=state,
        state_version=_integer(_value(value, "state_version")),
        provider_task_ref=(
            _optional_text(_value(value, "provider_task_ref"))
            or provider_task_ref
        ),
        cancel_requested=(
            cancel_requested or state == "cancel_requested"
            or _value(value, "cancel_requested_at") is not None
        ),
    )


def _receipt_hash(receipt: ProviderReceipt) -> str:
    payload = {
        "state": receipt.state.value, "provider": receipt.provider,
        "provider_task_ref": receipt.provider_task_ref,
        "status_locator": receipt.status_locator,
        "result": dict(receipt.result), "evidence": dict(receipt.evidence),
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _value(value: object, name: str) -> object:
    return value.get(name) if isinstance(value, Mapping) else getattr(value, name)


def _required(value: object, name: str) -> object:
    item = _value(value, name)
    if item is None:
        raise RuntimeError("KIE_PROVIDER_FACT_RESPONSE_INVALID")
    return item


def _text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError("KIE_PROVIDER_FACT_IDENTITY_REQUIRED")
    return value.strip()


def _optional_text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RuntimeError("KIE_PROVIDER_FACT_VERSION_INVALID")
    return value


__all__ = [
    "KieFactIdentity", "cancel_requested_fact", "create_fact", "latest_fact",
    "readback_fact", "receipt_identity", "rejected_fact", "submitted_fact",
    "unknown_fact", "with_fact",
]
