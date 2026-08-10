"""PostgreSQL Scheduled Runtime WeCom repository over narrow worker RPCs."""

from __future__ import annotations

from typing import Any

from psycopg import InterfaceError, OperationalError

from core.db_scope import DatabaseAccessKind, database_scope_from_client
from services.agent.runtime.domain.errors import PersistenceContractError
from services.agent.runtime.infrastructure.postgres.scheduled_wecom_parsing import (
    AttemptRpcOperation,
    metadata_params,
    parse_attempt,
    parse_delivery_claim,
    parse_dispatch_outcome,
    parse_prepared_recovery,
    parse_reconcile_claim,
    parse_reconcile_result,
    validate_evidence,
)
from services.agent.runtime.ports.scheduled_wecom_delivery import (
    AttemptStatus,
    DeliveryClaim,
    DeliveryFence,
    DispatchAttempt,
    DispatchOutcome,
    DispatchOutcomeReceipt,
    PreparedRecovery,
    ProviderDispatchIdentity,
    ProviderReceiptEvidence,
    ReconcileClaim,
    ReconcileClaimOutcome,
    ReconcileResult,
    ReconcileResultReceipt,
)


class PostgresScheduledWecomDeliveryRepository:
    """Uses only the Scheduled WeCom worker RPC surface from 227_39–227_45."""

    def __init__(self, database: Any) -> None:
        scope = database_scope_from_client(database)
        if scope is None or scope.access_kind is not DatabaseAccessKind.WORKER:
            raise ValueError("WECOM_WORKER_SCOPED_DATABASE_CLIENT_REQUIRED")
        self._database = database

    async def _rpc(self, name: str, params: dict[str, object]) -> object:
        response = await self._database.rpc(name, params).execute()
        return response.data

    async def _replayable_rpc(
        self, name: str, params: dict[str, object],
    ) -> object:
        try:
            return await self._rpc(name, params)
        except (OperationalError, InterfaceError):
            return await self._rpc(name, params)

    async def claim_delivery(
        self, *, request_id: str, worker_id: str, lease_seconds: int = 60,
    ) -> DeliveryClaim | None:
        params = {
            "p_claim_request_id": request_id,
            "p_worker_id": worker_id,
            "p_lease_seconds": lease_seconds,
        }
        claim = parse_delivery_claim(await self._replayable_rpc(
            "claim_agent_runtime_scheduled_wecom_delivery_v2", params,
        ))
        if claim is not None and (
            claim.fence.claim_request_id != request_id
            or claim.fence.worker_id != worker_id
        ):
            raise _contract("delivery_claim_identity_changed")
        return claim

    async def recover_prepared(
        self, *, request_id: str, worker_id: str, lease_seconds: int = 60,
    ) -> PreparedRecovery | None:
        params = {
            "p_recovery_request_id": request_id,
            "p_worker_id": worker_id,
            "p_lease_seconds": lease_seconds,
        }
        return parse_prepared_recovery(await self._replayable_rpc(
            "recover_agent_runtime_scheduled_wecom_prepared_dispatch_v1", params,
        ), request_id=request_id, worker_id=worker_id)

    async def prepare_dispatch(
        self, claim: DeliveryClaim, identity: ProviderDispatchIdentity,
    ) -> DispatchAttempt:
        if claim.outcome.value == "fenced":
            raise _contract("delivery_claim_fenced")
        params = _dispatch_params(claim.fence, identity)
        raw = await self._replayable_rpc(
            "prepare_agent_runtime_scheduled_wecom_dispatch_v2", params,
        )
        return parse_attempt(
            raw, claim.fence, identity, operation=AttemptRpcOperation.PREPARE,
        )

    async def start_dispatch(self, attempt: DispatchAttempt) -> DispatchAttempt:
        if attempt.status not in {AttemptStatus.PREPARED, AttemptStatus.DISPATCH_STARTED}:
            raise _contract("attempt_not_startable")
        params = {
            **_dispatch_params(attempt.fence, attempt.identity),
            "p_attempt_id": attempt.attempt_id,
        }
        raw = await self._replayable_rpc(
            "start_agent_runtime_scheduled_wecom_dispatch_v2", params,
        )
        started = parse_attempt(
            raw, attempt.fence, attempt.identity,
            operation=AttemptRpcOperation.START,
        )
        if started.attempt_id != attempt.attempt_id:
            raise _contract("attempt_id_changed")
        return started

    async def read_attempt(self, attempt: DispatchAttempt) -> DispatchAttempt:
        fence = attempt.fence
        identity = attempt.identity
        params = {
            "p_intent_id": fence.intent_id,
            "p_item_id": fence.item_id,
            "p_attempt_id": attempt.attempt_id,
            "p_claim_request_id": fence.claim_request_id,
            "p_lease_token": fence.lease_token,
            "p_worker_id": fence.worker_id,
            "p_provider_request_id": identity.provider_request_id,
            "p_idempotency_key": identity.idempotency_key,
            "p_provider_revision": identity.provider_revision,
        }
        readback = parse_attempt(
            await self._rpc(
                "read_agent_runtime_scheduled_wecom_dispatch_attempt_v2", params,
            ),
            fence,
            identity,
            operation=AttemptRpcOperation.READ,
        )
        if readback.attempt_id != attempt.attempt_id:
            raise _contract("attempt_id_changed")
        return readback

    async def record_dispatch_outcome(
        self, attempt: DispatchAttempt, *, request_id: str,
        dispatch_outcome: DispatchOutcome,
        evidence: ProviderReceiptEvidence | None,
    ) -> DispatchOutcomeReceipt:
        if attempt.status is not AttemptStatus.DISPATCH_STARTED:
            raise _contract("attempt_not_dispatch_started")
        receipt = _dispatch_evidence(dispatch_outcome, evidence)
        fence = attempt.fence
        identity = attempt.identity
        params = {
            "p_request_id": request_id,
            "p_intent_id": fence.intent_id,
            "p_item_id": fence.item_id,
            "p_attempt_id": attempt.attempt_id,
            "p_claim_request_id": fence.claim_request_id,
            "p_lease_token": fence.lease_token,
            "p_worker_id": fence.worker_id,
            "p_expected_delivery_state_version": fence.delivery_state_version,
            "p_expected_item_state_version": fence.item_state_version,
            "p_provider_request_id": identity.provider_request_id,
            "p_idempotency_key": identity.idempotency_key,
            "p_provider_revision": identity.provider_revision,
            "p_dispatch_outcome": dispatch_outcome.value,
            **receipt,
        }
        parsed = parse_dispatch_outcome(await self._replayable_rpc(
            "record_agent_runtime_scheduled_wecom_dispatch_outcome_v1", params,
        ))
        if (
            parsed.request_id != request_id
            or parsed.intent_id != fence.intent_id
            or parsed.item_id != fence.item_id
            or parsed.attempt_id != attempt.attempt_id
            or parsed.dispatch_outcome is not dispatch_outcome
        ):
            raise _contract("dispatch_outcome_identity_changed")
        return parsed

    async def claim_reconcile(
        self, *, request_id: str, worker_id: str, lease_seconds: int = 60,
    ) -> ReconcileClaim | None:
        params = {
            "p_request_id": request_id,
            "p_worker_id": worker_id,
            "p_lease_seconds": lease_seconds,
        }
        claim = parse_reconcile_claim(await self._replayable_rpc(
            "claim_agent_runtime_scheduled_wecom_reconcile_v1", params,
        ))
        if claim is not None and (
            claim.request_id != request_id or claim.worker_id != worker_id
        ):
            raise _contract("reconcile_claim_identity_changed")
        return claim

    async def renew_reconcile(
        self, claim: ReconcileClaim, *, lease_seconds: int = 60,
    ) -> ReconcileClaim:
        _require_active_reconcile(claim)
        params = {
            "p_intent_id": claim.intent_id,
            "p_request_id": claim.request_id,
            "p_reconcile_token": claim.reconcile_token,
            "p_worker_id": claim.worker_id,
            "p_expected_delivery_state_version": claim.delivery_state_version,
            "p_lease_seconds": lease_seconds,
        }
        renewed = parse_reconcile_claim(await self._rpc(
            "renew_agent_runtime_scheduled_wecom_reconcile_lease_v1", params,
        ))
        if renewed is None or renewed.outcome is not ReconcileClaimOutcome.RENEWED:
            raise _contract("reconcile_renew_fenced")
        _same_reconcile_identity(renewed, claim)
        return renewed

    async def read_reconcile(self, request_id: str) -> ReconcileClaim | None:
        claim = parse_reconcile_claim(await self._rpc(
            "read_agent_runtime_scheduled_wecom_reconcile_v1", {
                "p_request_id": request_id,
            },
        ))
        if claim is not None and claim.request_id != request_id:
            raise _contract("reconcile_read_identity_changed")
        return claim

    async def record_still_unknown(
        self, claim: ReconcileClaim, *, request_id: str,
        evidence: ProviderReceiptEvidence, delay_seconds: int,
    ) -> ReconcileResultReceipt:
        _require_active_reconcile(claim)
        params = {
            **_reconcile_result_params(claim, request_id, evidence),
            "p_reconcile_result": ReconcileResult.STILL_UNKNOWN.value,
            "p_delay_seconds": delay_seconds,
        }
        receipt = parse_reconcile_result(await self._replayable_rpc(
            "record_agent_runtime_scheduled_wecom_reconcile_result_v1", params,
        ), definitive=False)
        _validate_reconcile_receipt(receipt, claim, request_id)
        return receipt

    async def record_definitive(
        self, claim: ReconcileClaim, *, request_id: str,
        result: ReconcileResult, evidence: ProviderReceiptEvidence,
    ) -> ReconcileResultReceipt:
        _require_active_reconcile(claim)
        if result not in {ReconcileResult.ACCEPTED, ReconcileResult.REJECTED}:
            raise _contract("definitive_result_required")
        params = {
            **_reconcile_result_params(claim, request_id, evidence),
            "p_reconcile_result": result.value,
        }
        receipt = parse_reconcile_result(await self._replayable_rpc(
            "record_agent_runtime_scheduled_wecom_reconcile_definitive_result_v1", params,
        ), definitive=True)
        _validate_reconcile_receipt(receipt, claim, request_id)
        if receipt.reconcile_result is not result:
            raise _contract("reconcile_result_changed")
        return receipt


def _contract(code: str) -> PersistenceContractError:
    return PersistenceContractError(f"SCHEDULED_WECOM_REPOSITORY_FENCED:{code}")


def _dispatch_params(
    fence: DeliveryFence, identity: ProviderDispatchIdentity,
) -> dict[str, object]:
    return {
        "p_intent_id": fence.intent_id,
        "p_item_id": fence.item_id,
        "p_claim_request_id": fence.claim_request_id,
        "p_lease_token": fence.lease_token,
        "p_worker_id": fence.worker_id,
        "p_expected_delivery_state_version": fence.delivery_state_version,
        "p_expected_item_state_version": fence.item_state_version,
        "p_provider_request_id": identity.provider_request_id,
        "p_idempotency_key": identity.idempotency_key,
        "p_provider_revision": identity.provider_revision,
    }


def _dispatch_evidence(
    outcome: DispatchOutcome, evidence: ProviderReceiptEvidence | None,
) -> dict[str, object]:
    if outcome is DispatchOutcome.UNKNOWN:
        if evidence is not None:
            raise _contract("unknown_evidence_forbidden")
        return {
            "p_receipt_type": None, "p_receipt_hash": None,
            "p_receipt_code": None, "p_receipt_metadata": {},
        }
    if evidence is None:
        raise _contract("definitive_evidence_required")
    validate_evidence(evidence)
    return {
        "p_receipt_type": evidence.receipt_type.value,
        "p_receipt_hash": evidence.receipt_hash,
        "p_receipt_code": evidence.receipt_code,
        "p_receipt_metadata": metadata_params(evidence.metadata),
    }


def _require_active_reconcile(claim: ReconcileClaim) -> None:
    if claim.outcome is ReconcileClaimOutcome.FENCED:
        raise _contract("reconcile_claim_fenced")


def _same_reconcile_identity(actual: ReconcileClaim, expected: ReconcileClaim) -> None:
    if (
        actual.request_id, actual.intent_id, actual.item_id, actual.attempt_id,
        actual.worker_id, actual.reconcile_token, actual.identity,
    ) != (
        expected.request_id, expected.intent_id, expected.item_id, expected.attempt_id,
        expected.worker_id, expected.reconcile_token, expected.identity,
    ):
        raise _contract("reconcile_identity_changed")


def _reconcile_result_params(
    claim: ReconcileClaim, request_id: str, evidence: ProviderReceiptEvidence,
) -> dict[str, object]:
    validate_evidence(evidence)
    return {
        "p_request_id": request_id,
        "p_claim_request_id": claim.request_id,
        "p_intent_id": claim.intent_id,
        "p_item_id": claim.item_id,
        "p_attempt_id": claim.attempt_id,
        "p_reconcile_token": claim.reconcile_token,
        "p_worker_id": claim.worker_id,
        "p_expected_delivery_state_version": claim.delivery_state_version,
        "p_expected_item_state_version": claim.item_state_version,
        "p_provider_request_id": claim.identity.provider_request_id,
        "p_idempotency_key": claim.identity.idempotency_key,
        "p_provider_revision": claim.identity.provider_revision,
        "p_readback_type": evidence.receipt_type.value,
        "p_readback_hash": evidence.receipt_hash,
        "p_readback_code": evidence.receipt_code,
        "p_readback_metadata": metadata_params(evidence.metadata),
    }


def _validate_reconcile_receipt(
    receipt: ReconcileResultReceipt, claim: ReconcileClaim, request_id: str,
) -> None:
    if (
        receipt.request_id, receipt.claim_request_id, receipt.intent_id,
        receipt.item_id, receipt.attempt_id,
    ) != (
        request_id, claim.request_id, claim.intent_id, claim.item_id, claim.attempt_id,
    ):
        raise _contract("reconcile_receipt_identity_changed")
