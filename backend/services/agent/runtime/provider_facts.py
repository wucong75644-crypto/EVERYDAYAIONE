"""Runtime-owned provider submission facts and isolated mock contract."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import StrEnum
from typing import Mapping, Protocol

from core.db_scope import DatabaseAccessKind, database_scope_from_client


class ProviderFactState(StrEnum):
    SUBMISSION_PENDING = "submission_pending"
    SUBMITTED = "submitted"
    READBACK_CONFIRMED = "readback_confirmed"
    ACCEPTED = "accepted"
    UNKNOWN = "unknown"
    RECONCILE_REQUIRED = "reconcile_required"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    FAILED = "failed"


class ProviderFactsError(RuntimeError):
    """Stable failure-closed error for the facts port."""


@dataclass(frozen=True, kw_only=True)
class ProviderSubmissionContext:
    attempt_id: str
    action_id: str
    run_id: str
    org_id: str | None
    user_id: str | None
    scope_kind: str
    scope_id: str
    execution_token: str
    request_hash: str
    provider: str
    provider_revision: str
    external_idempotency_key: str

    def __post_init__(self) -> None:
        for value, name in (
            (self.attempt_id, "attempt_id"), (self.action_id, "action_id"),
            (self.run_id, "run_id"), (self.scope_id, "scope_id"),
            (self.execution_token, "execution_token"),
            (self.provider, "provider"), (self.provider_revision, "provider_revision"),
            (self.external_idempotency_key, "external_idempotency_key"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ProviderFactsError(f"PROVIDER_FACT_{name.upper()}_REQUIRED")
        if self.scope_kind not in {"user", "channel", "system"}:
            raise ProviderFactsError("PROVIDER_FACT_SCOPE_INVALID")
        if self.scope_kind == "user" and not self.user_id:
            raise ProviderFactsError("PROVIDER_FACT_USER_REQUIRED")
        if self.scope_kind == "channel" and not self.org_id:
            raise ProviderFactsError("PROVIDER_FACT_ORG_REQUIRED")
        if len(self.request_hash) != 64 or any(
            char not in "0123456789abcdef" for char in self.request_hash
        ):
            raise ProviderFactsError("PROVIDER_FACT_REQUEST_HASH_INVALID")


@dataclass(frozen=True, kw_only=True)
class ProviderSubmissionFact:
    context: ProviderSubmissionContext
    submission_id: str
    state: ProviderFactState
    state_version: int = 0
    provider_task_ref: str | None = None
    status_locator: str | None = None
    provider_receipt_hash: str | None = None
    readback_hash: str | None = None
    ambiguity_evidence: Mapping[str, object] = field(default_factory=dict)
    cancel_reason: str | None = None
    cancel_requested_at: datetime | None = None
    cancel_confirmed_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.submission_id or self.state_version < 0:
            raise ProviderFactsError("PROVIDER_FACT_IDENTITY_INVALID")
        _validate_evidence(self.ambiguity_evidence)
        if self.state is ProviderFactState.ACCEPTED and not self.provider_task_ref:
            raise ProviderFactsError("PROVIDER_FACT_REF_REQUIRED")
        if self.state is ProviderFactState.UNKNOWN and not self.ambiguity_evidence:
            raise ProviderFactsError("PROVIDER_FACT_UNKNOWN_EVIDENCE_REQUIRED")
        if self.state is ProviderFactState.CANCELLED and self.cancel_confirmed_at is None:
            raise ProviderFactsError("PROVIDER_FACT_CANCEL_CONFIRMATION_REQUIRED")


class ProviderSubmissionFacts(Protocol):
    production_ready: bool

    async def create(self, context: ProviderSubmissionContext) -> tuple[str, ProviderSubmissionFact]: ...

    async def read(self, context: ProviderSubmissionContext,
                   submission_id: str) -> ProviderSubmissionFact: ...

    async def submitted(self, *, submission_id: str, execution_token: str,
                        request_hash: str, expected_state_version: int,
                        provider_task_ref: str, status_locator: str | None = None,
                        provider_receipt_hash: str | None = None) -> ProviderSubmissionFact: ...

    async def unknown(self, *, submission_id: str, execution_token: str,
                      request_hash: str, expected_state_version: int,
                      evidence: Mapping[str, object]) -> ProviderSubmissionFact: ...

    async def rejected(self, *, submission_id: str, execution_token: str,
                       request_hash: str, expected_state_version: int,
                       evidence: Mapping[str, object]) -> ProviderSubmissionFact: ...

    async def request_cancel(self, *, submission_id: str, execution_token: str,
                             request_hash: str, expected_state_version: int,
                             reason: str) -> ProviderSubmissionFact: ...

    async def readback(self, *, submission_id: str, execution_token: str,
                       request_hash: str, expected_state_version: int,
                       provider_state: str, readback_hash: str,
                       evidence: Mapping[str, object] | None = None,
                       provider_task_ref: str | None = None) -> ProviderSubmissionFact: ...

    async def reconcile(self, *, submission_id: str, execution_token: str,
                        request_hash: str, expected_state_version: int,
                        resolution: str, readback_hash: str | None = None,
                        evidence: Mapping[str, object] | None = None) -> ProviderSubmissionFact: ...


class MockProviderSubmissionFacts:
    """Isolated in-memory contract; never reports production readiness."""

    production_ready = False

    def __init__(self) -> None:
        self._facts: dict[str, ProviderSubmissionFact] = {}
        self._by_key: dict[tuple[str, str, str], str] = {}
        self._next_id = 0

    async def create(self, context: ProviderSubmissionContext) -> tuple[str, ProviderSubmissionFact]:
        key = (context.scope_kind, context.scope_id, context.external_idempotency_key)
        existing_id = self._by_key.get(key)
        if existing_id is not None:
            existing = self._facts[existing_id]
            if existing.context != context:
                raise ProviderFactsError("PROVIDER_FACT_IDEMPOTENCY_CONFLICT")
            return "already_applied", existing
        self._next_id += 1
        submission_id = f"mock-submission-{self._next_id}"
        fact = ProviderSubmissionFact(
            context=context, submission_id=submission_id,
            state=ProviderFactState.SUBMISSION_PENDING,
        )
        self._facts[submission_id] = fact
        self._by_key[key] = submission_id
        return "created", fact

    async def submitted(self, **params: object) -> ProviderSubmissionFact:
        return self._mutate(
            params, allowed={ProviderFactState.SUBMISSION_PENDING, ProviderFactState.SUBMITTED},
            state=ProviderFactState.SUBMITTED,
            provider_task_ref=str(params["provider_task_ref"]),
            status_locator=params.get("status_locator"),
            provider_receipt_hash=params.get("provider_receipt_hash"),
        )

    async def read(
        self, context: ProviderSubmissionContext, submission_id: str,
    ) -> ProviderSubmissionFact:
        fact = await self.recover(submission_id)
        if fact.context != context:
            raise ProviderFactsError("PROVIDER_FACT_CONTEXT_CONFLICT")
        return fact

    async def unknown(self, **params: object) -> ProviderSubmissionFact:
        evidence = params.get("evidence")
        _validate_evidence(evidence)
        if not evidence:
            raise ProviderFactsError("PROVIDER_FACT_UNKNOWN_EVIDENCE_REQUIRED")
        return self._mutate(
            params, allowed={ProviderFactState.SUBMISSION_PENDING, ProviderFactState.SUBMITTED,
                             ProviderFactState.ACCEPTED, ProviderFactState.UNKNOWN,
                             ProviderFactState.RECONCILE_REQUIRED},
            state=ProviderFactState.UNKNOWN, ambiguity_evidence=evidence,
        )

    async def rejected(self, **params: object) -> ProviderSubmissionFact:
        evidence = params.get("evidence")
        _validate_evidence(evidence)
        return self._mutate(
            params, allowed={ProviderFactState.SUBMISSION_PENDING},
            state=ProviderFactState.FAILED,
            ambiguity_evidence=evidence,
        )

    async def request_cancel(self, **params: object) -> ProviderSubmissionFact:
        return self._mutate(
            params, allowed={ProviderFactState.SUBMISSION_PENDING, ProviderFactState.SUBMITTED,
                             ProviderFactState.ACCEPTED, ProviderFactState.UNKNOWN,
                             ProviderFactState.RECONCILE_REQUIRED, ProviderFactState.CANCEL_REQUESTED},
            state=ProviderFactState.CANCEL_REQUESTED,
            cancel_reason=str(params.get("reason", ""))[:200] or None,
            cancel_requested_at=datetime.now(timezone.utc),
        )

    async def readback(self, **params: object) -> ProviderSubmissionFact:
        provider_state = params["provider_state"]
        mapping = {
            "completed": ProviderFactState.READBACK_CONFIRMED,
            "accepted": ProviderFactState.ACCEPTED,
            "failed": ProviderFactState.FAILED,
            "cancelled": ProviderFactState.CANCELLED,
            "unknown": ProviderFactState.UNKNOWN,
        }
        if provider_state not in mapping:
            raise ProviderFactsError("PROVIDER_FACT_READBACK_INVALID")
        evidence = params.get("evidence") or {}
        _validate_evidence(evidence)
        if provider_state == "unknown" and not evidence:
            raise ProviderFactsError("PROVIDER_FACT_UNKNOWN_EVIDENCE_REQUIRED")
        current = self._get(params)
        if provider_state == "cancelled" and current.state is not ProviderFactState.CANCEL_REQUESTED:
            raise ProviderFactsError("PROVIDER_FACT_CANCEL_NOT_REQUESTED")
        return self._mutate(
            params, allowed={ProviderFactState.SUBMITTED, ProviderFactState.ACCEPTED,
                             ProviderFactState.UNKNOWN, ProviderFactState.RECONCILE_REQUIRED,
                             ProviderFactState.CANCEL_REQUESTED},
            state=mapping[provider_state], readback_hash=str(params["readback_hash"]),
            ambiguity_evidence=evidence if provider_state == "unknown" else current.ambiguity_evidence,
            provider_task_ref=params.get("provider_task_ref") or current.provider_task_ref,
            cancel_confirmed_at=datetime.now(timezone.utc) if provider_state == "cancelled" else current.cancel_confirmed_at,
        )

    async def reconcile(self, **params: object) -> ProviderSubmissionFact:
        resolution = params["resolution"]
        if resolution not in {state.value for state in (
            ProviderFactState.READBACK_CONFIRMED, ProviderFactState.ACCEPTED,
            ProviderFactState.FAILED, ProviderFactState.CANCELLED, ProviderFactState.UNKNOWN,
        )}:
            raise ProviderFactsError("PROVIDER_FACT_RECONCILIATION_INVALID")
        evidence = params.get("evidence") or {}
        _validate_evidence(evidence)
        current = self._get(params)
        if resolution == "cancelled" and current.state is not ProviderFactState.CANCEL_REQUESTED:
            raise ProviderFactsError("PROVIDER_FACT_CANCEL_NOT_REQUESTED")
        if resolution == "unknown":
            if not evidence:
                return self._mutate(params, allowed={current.state}, state=ProviderFactState.RECONCILE_REQUIRED)
            target = ProviderFactState.UNKNOWN
        else:
            target = ProviderFactState(resolution)
        return self._mutate(
            params, allowed={ProviderFactState.SUBMITTED, ProviderFactState.ACCEPTED,
                             ProviderFactState.UNKNOWN, ProviderFactState.RECONCILE_REQUIRED,
                             ProviderFactState.CANCEL_REQUESTED},
            state=target, readback_hash=params.get("readback_hash") or current.readback_hash,
            ambiguity_evidence=evidence if resolution == "unknown" else current.ambiguity_evidence,
            cancel_confirmed_at=datetime.now(timezone.utc) if resolution == "cancelled" else current.cancel_confirmed_at,
        )

    async def recover(self, submission_id: str) -> ProviderSubmissionFact:
        try:
            return self._facts[submission_id]
        except KeyError:
            raise ProviderFactsError("PROVIDER_FACT_NOT_FOUND") from None

    def _get(self, params: Mapping[str, object]) -> ProviderSubmissionFact:
        try:
            fact = self._facts[str(params["submission_id"])]
        except KeyError:
            raise ProviderFactsError("PROVIDER_FACT_NOT_FOUND") from None
        if fact.context.execution_token != params["execution_token"]:
            raise ProviderFactsError("PROVIDER_FACT_FENCE_CONFLICT")
        if fact.context.request_hash != params["request_hash"]:
            raise ProviderFactsError("PROVIDER_FACT_REQUEST_HASH_CONFLICT")
        if fact.state_version != params["expected_state_version"]:
            raise ProviderFactsError("PROVIDER_FACT_STALE_VERSION")
        return fact

    def _mutate(self, params: Mapping[str, object], *, allowed: set[ProviderFactState],
                state: ProviderFactState, **changes: object) -> ProviderSubmissionFact:
        current = self._get(params)
        if current.state not in allowed:
            raise ProviderFactsError("PROVIDER_FACT_STALE_VERSION")
        updated = replace(current, state=state, state_version=current.state_version + 1, **changes)
        self._facts[current.submission_id] = updated
        return updated


class PostgresProviderSubmissionFacts:
    """Worker-scoped adapter; every operation is a narrow Runtime RPC."""

    production_ready = True

    def __init__(self, database: object) -> None:
        scope = database_scope_from_client(database)
        if scope is None or scope.access_kind is not DatabaseAccessKind.AGENT_RUNTIME:
            raise ValueError("WORKER_SCOPED_DATABASE_CLIENT_REQUIRED")
        self._database = database

    async def _rpc(self, name: str, params: Mapping[str, object]) -> Mapping[str, object]:
        payload = (await self._database.rpc(name, dict(params)).execute()).data
        if not isinstance(payload, Mapping):
            raise ProviderFactsError("PROVIDER_FACT_RPC_INVALID")
        return dict(payload)

    async def create(self, context: ProviderSubmissionContext) -> Mapping[str, object]:
        return await self._rpc("create_agent_runtime_provider_submission", {
            "p_attempt_id": context.attempt_id, "p_action_id": context.action_id,
            "p_run_id": context.run_id, "p_org_id": context.org_id,
            "p_user_id": context.user_id, "p_scope_kind": context.scope_kind,
            "p_scope_id": context.scope_id, "p_execution_token": context.execution_token,
            "p_request_hash": context.request_hash, "p_provider": context.provider,
            "p_provider_revision": context.provider_revision,
            "p_external_idempotency_key": context.external_idempotency_key,
        })

    async def read(
        self, context: ProviderSubmissionContext, submission_id: str,
    ) -> Mapping[str, object]:
        return await self._rpc("read_agent_runtime_provider_submission", {
            "p_submission_id": submission_id,
            "p_attempt_id": context.attempt_id,
            "p_action_id": context.action_id, "p_run_id": context.run_id,
            "p_org_id": context.org_id, "p_user_id": context.user_id,
            "p_scope_kind": context.scope_kind, "p_scope_id": context.scope_id,
            "p_execution_token": context.execution_token,
            "p_request_hash": context.request_hash,
        })

    async def submitted(self, **params: object) -> Mapping[str, object]:
        return await self._rpc("record_agent_runtime_provider_submitted", _prefix(params))

    async def unknown(self, **params: object) -> Mapping[str, object]:
        return await self._rpc("record_agent_runtime_provider_unknown", _prefix(params, evidence="p_ambiguity_evidence"))

    async def rejected(self, **params: object) -> Mapping[str, object]:
        return await self._rpc(
            "record_agent_runtime_media_provider_rejected_v1",
            _prefix(params, evidence="p_evidence"),
        )

    async def request_cancel(self, **params: object) -> Mapping[str, object]:
        return await self._rpc("request_agent_runtime_provider_cancel", _prefix(params))

    async def readback(self, **params: object) -> Mapping[str, object]:
        return await self._rpc("record_agent_runtime_provider_readback", _prefix(params, evidence="p_evidence"))

    async def reconcile(self, **params: object) -> Mapping[str, object]:
        return await self._rpc("reconcile_agent_runtime_provider_submission", _prefix(params, evidence="p_evidence"))


def _prefix(params: Mapping[str, object], *, evidence: str | None = None) -> dict[str, object]:
    names = {
        "submission_id": "p_submission_id", "execution_token": "p_execution_token",
        "request_hash": "p_request_hash", "expected_state_version": "p_expected_state_version",
        "provider_task_ref": "p_provider_task_ref", "status_locator": "p_status_locator",
        "provider_receipt_hash": "p_provider_receipt_hash", "evidence": evidence,
        "reason": "p_reason", "provider_state": "p_provider_state",
        "readback_hash": "p_readback_hash", "resolution": "p_resolution",
    }
    return {target: value for source, target in names.items()
            if target is not None and (value := params.get(source)) is not None}


def _validate_evidence(value: object) -> None:
    if not isinstance(value, Mapping):
        raise ProviderFactsError("PROVIDER_FACT_EVIDENCE_INVALID")
    forbidden = ("secret", "token", "password", "credential", "api_key", "authorization", "cookie")
    for key, item in value.items():
        key_text = str(key).lower()
        if any(word in key_text for word in forbidden):
            raise ProviderFactsError("PROVIDER_FACT_SENSITIVE_EVIDENCE")
        if isinstance(item, Mapping):
            _validate_evidence(item)


__all__ = [
    "MockProviderSubmissionFacts", "ProviderFactState", "ProviderFactsError",
    "ProviderSubmissionContext", "ProviderSubmissionFact", "ProviderSubmissionFacts",
]
