"""WORKER-scoped authorization recovery and dispatch gate adapter."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping
from uuid import UUID

from core.db_scope import DatabaseAccessKind, database_scope_from_client
from services.agent.runtime.executors.types import (
    ExecutorDescriptor,
    IdempotencySupport,
)
from services.agent.runtime.ports.authorization import (
    ActionAuthorizationPort,
    AuthorizationRecoveryClaim,
    DispatchGateDenied,
    DispatchGateOutcome,
    DispatchGateReceipt,
    PolicyReceiptRecord,
)
from services.agent.runtime.ports.coordinator_recovery import (
    ActionDispatchSnapshot,
)


_PERMANENT = {
    "grant_invalid",
    "grant_expired",
    "grant_revoked",
    "grant_replay_conflict",
    "receipt_expired",
    "receipt_conflict",
    "scope_mismatch",
    "executor_revision_conflict",
    "action_not_dispatchable",
    "dispatch_gate_disabled",
}


class PostgresActionAuthorizationRepository(ActionAuthorizationPort):
    def __init__(self, database: Any) -> None:
        scope = database_scope_from_client(database)
        if scope is None or scope.access_kind not in {
            DatabaseAccessKind.AGENT_RUNTIME,
            DatabaseAccessKind.AUTHORIZATION,
        }:
            raise ValueError("AUTHORIZATION_SCOPED_DATABASE_CLIENT_REQUIRED")
        self._access_kind = scope.access_kind
        self._database = database

    async def gate(
        self, *, snapshot: ActionDispatchSnapshot,
        descriptor: ExecutorDescriptor,
    ) -> DispatchGateReceipt:
        if self._access_kind is not DatabaseAccessKind.AGENT_RUNTIME:
            raise PermissionError("AGENT_RUNTIME_DISPATCH_GATE_REQUIRED")
        action = snapshot.action
        attempt = snapshot.attempt
        receipt_id = _receipt_id(action)
        response = await self._database.rpc(
            "gate_agent_action_dispatch_v2", {
                "p_attempt_id": _text(attempt, "id"),
                "p_execution_token": _text(attempt, "execution_token"),
                "p_expected_attempt_version": _integer(
                    attempt, "state_version",
                ),
                "p_request_hash": _text(attempt, "request_hash"),
                "p_policy_receipt_id": receipt_id,
                "p_executor_type": descriptor.executor_type,
                "p_executor_revision": descriptor.revision,
                "p_policy_revision": _text(action, "policy_revision"),
                "p_recovery_mode": (
                    "idempotent_replay"
                    if descriptor.idempotency is not IdempotencySupport.NONE
                    else "reconcile_only"
                ),
            },
        ).execute()
        row = _mapping(response.data)
        outcome = row.get("outcome")
        if outcome in _PERMANENT:
            raise DispatchGateDenied(str(outcome))
        try:
            parsed = DispatchGateOutcome(str(outcome))
        except ValueError as error:
            raise RuntimeError(f"ACTION_DISPATCH_GATE_{outcome}") from error
        return DispatchGateReceipt(
            outcome=parsed,
            intent_id=_uuid(row.get("intent_id")),
            state_version=_integer(row, "state_version"),
            external_idempotency_key=_text(
                row, "external_idempotency_key",
            ),
            recovery_mode=_text(row, "recovery_mode"),
        )

    async def claim_recovery(
        self, *, worker_id: str, lease_seconds: int = 120,
    ) -> AuthorizationRecoveryClaim | None:
        self._require_recovery_owner()
        response = await self._database.rpc(
            "claim_next_agent_authorization_recovery", {
                "p_worker_id": worker_id,
                "p_lease_seconds": lease_seconds,
            },
        ).execute()
        row = _mapping(response.data)
        if row.get("outcome") in {"not_found", "grant_invalid"}:
            return None
        if row.get("outcome") != "claimed":
            raise RuntimeError(
                f"AUTHORIZATION_RECOVERY_{row.get('outcome')}",
            )
        return AuthorizationRecoveryClaim(
            interaction_id=_uuid(row.get("interaction_id")),
            recovery_token=_uuid(row.get("recovery_token")),
            state_version=_integer(row, "state_version"),
            lease_expires_at=_datetime(row.get("lease_expires_at")),
            action=_mapping(row.get("action")),
            grant=_mapping(row.get("grant")),
        )

    async def record_allow_receipt(
        self, *, claim: AuthorizationRecoveryClaim,
        descriptor: ExecutorDescriptor, policy_revision: str,
        reason_codes: tuple[str, ...], obligations: tuple[str, ...],
        receipt_hash: str,
    ) -> PolicyReceiptRecord:
        action = claim.action
        grant = claim.grant
        self._require_recovery_owner()
        response = await self._database.rpc(
            "record_agent_policy_receipt", {
                "p_action_id": _text(action, "id"),
                "p_arguments_hash": _text(action, "arguments_hash"),
                "p_executor_type": descriptor.executor_type,
                "p_executor_revision": descriptor.revision,
                "p_policy_revision": policy_revision,
                "p_decision": "allow",
                "p_grant_id": _text(grant, "id"),
                "p_effective_scope": _mapping(
                    grant.get("effective_scope"),
                ),
                "p_reason_codes": list(reason_codes),
                "p_obligations": list(obligations),
                "p_receipt_hash": receipt_hash,
                "p_ttl_seconds": 300,
            },
        ).execute()
        row = _mapping(response.data)
        if row.get("outcome") not in {"recorded", "already_recorded"}:
            raise RuntimeError(
                f"AUTHORIZATION_RECEIPT_{row.get('outcome')}",
            )
        receipt = _mapping(row.get("receipt"))
        return PolicyReceiptRecord(receipt_id=_uuid(receipt.get("id")))

    async def activate(
        self, *, claim: AuthorizationRecoveryClaim,
        receipt: PolicyReceiptRecord,
    ) -> None:
        self._require_recovery_owner()
        action = claim.action
        response = await self._database.rpc(
            "activate_agent_authorized_action", {
                "p_action_id": _text(action, "id"),
                "p_expected_action_version": _integer(
                    action, "state_version",
                ),
                "p_interaction_id": claim.interaction_id,
                "p_recovery_token": claim.recovery_token,
                "p_expected_interaction_version": claim.state_version,
                "p_policy_receipt_id": receipt.receipt_id,
            },
        ).execute()
        row = _mapping(response.data)
        if row.get("outcome") not in {"activated", "already_activated"}:
            raise RuntimeError(
                f"AUTHORIZATION_ACTIVATE_{row.get('outcome')}",
            )

    def _require_recovery_owner(self) -> None:
        if self._access_kind is not DatabaseAccessKind.AUTHORIZATION:
            raise PermissionError("AUTHORIZATION_RECOVERY_OWNER_REQUIRED")


def _receipt_id(action: Mapping[str, object]) -> str:
    direct = action.get("policy_receipt_id")
    if direct is not None:
        return _uuid(direct)
    snapshot = action.get("policy_snapshot")
    if not isinstance(snapshot, Mapping):
        raise ValueError("Action policy_snapshot required")
    return _uuid(snapshot.get("dispatch_policy_receipt_id"))


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise RuntimeError("ACTION_DISPATCH_GATE_OBJECT_REQUIRED")
    return value


def _text(value: Mapping[str, object], field: str) -> str:
    item = value.get(field)
    if not isinstance(item, str) or not item.strip():
        raise ValueError(f"{field} must be nonblank text")
    return item


def _integer(value: Mapping[str, object], field: str) -> int:
    item = value.get(field)
    if isinstance(item, bool) or not isinstance(item, int) or item < 0:
        raise ValueError(f"{field} must be a nonnegative integer")
    return item


def _uuid(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("UUID required")
    return str(UUID(value))


def _datetime(value: object) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("timezone-aware datetime required")
    return value
