"""Typed persistence boundary for Scheduled Runtime WeCom delivery facts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol, TypeAlias


class DeliveryClaimOutcome(StrEnum):
    CLAIMED = "claimed"
    READBACK = "readback"
    FENCED = "fenced"


class DeliveryClaimKind(StrEnum):
    INITIAL = "initial"
    CONTINUATION = "continuation"


class AttemptOperationOutcome(StrEnum):
    PREPARED = "prepared"
    DISPATCH_STARTED = "dispatch_started"
    READBACK = "readback"


class AttemptStatus(StrEnum):
    PREPARED = "prepared"
    DISPATCH_STARTED = "dispatch_started"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


class DispatchOutcome(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


class RecordOutcome(StrEnum):
    RECORDED = "recorded"
    READBACK = "readback"


class ReceiptType(StrEnum):
    WECOM_APP = "wecom_app"
    WECOM_SMART_ROBOT = "wecom_smart_robot"


class DeliveryStatus(StrEnum):
    CLAIMED = "claimed"
    PENDING = "pending"
    UNKNOWN = "unknown"
    RECONCILE_REQUIRED = "reconcile_required"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class ItemStatus(StrEnum):
    ACCEPTED = "accepted"
    CANCELLED = "cancelled"
    FAILED = "failed"
    UNKNOWN = "unknown"
    RECONCILE_REQUIRED = "reconcile_required"


class DispatchPhase(StrEnum):
    PREPARED = "prepared"
    EXTERNAL_REQUEST_STARTED = "external_request_started"
    AMBIGUOUS = "ambiguous"
    RECEIPT_RECORDED = "receipt_recorded"


class RecoveryOutcome(StrEnum):
    RECOVERED = "recovered"
    READBACK = "readback"
    FENCED = "fenced"


class ReconcileClaimOutcome(StrEnum):
    CLAIMED = "claimed"
    RENEWED = "renewed"
    READBACK = "readback"
    FENCED = "fenced"


class ReconcileResult(StrEnum):
    STILL_UNKNOWN = "still_unknown"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class DispatchPayloadOutcome(StrEnum):
    PAYLOAD = "payload"
    UNSUPPORTED = "unsupported"
    UNAVAILABLE = "unavailable"


class DispatchChannel(StrEnum):
    APP = "app"
    SMART_ROBOT = "smart_robot"


class UnsupportedReason(StrEnum):
    ARTIFACT_IDENTITY = "wecom_artifact_identity_unsupported"
    FAILED_CONTENT = "wecom_failed_content_unsupported"
    CANCELLED_CONTENT = "wecom_cancelled_content_unsupported"
    NON_COMPLETED_CONTENT = "wecom_non_completed_content_unsupported"


class UnavailableReason(StrEnum):
    CONTRACT = "wecom_contract_unavailable"
    ORG = "wecom_org_unavailable"
    MEMBER = "wecom_member_unavailable"
    TARGET = "wecom_target_unavailable"
    SAFE_TEXT = "wecom_safe_text_unavailable"


class UnsupportedTerminalizationOutcome(StrEnum):
    TERMINALIZED = "terminalized"
    READBACK = "readback"


@dataclass(frozen=True, kw_only=True)
class ProviderDispatchIdentity:
    provider_request_id: str
    idempotency_key: str
    provider_revision: int


@dataclass(frozen=True, kw_only=True)
class DeliveryFence:
    intent_id: str
    item_id: str
    claim_request_id: str
    lease_token: str
    worker_id: str
    delivery_state_version: int
    item_state_version: int


@dataclass(frozen=True, kw_only=True)
class DeliveryClaim:
    outcome: DeliveryClaimOutcome
    kind: DeliveryClaimKind
    fence: DeliveryFence
    lease_seconds: int
    lease_expires_at: datetime
    previous_claim_request_id: str | None


@dataclass(frozen=True, kw_only=True)
class DispatchAttempt:
    outcome: AttemptOperationOutcome
    fence: DeliveryFence
    attempt_id: str
    attempt_number: int
    identity: ProviderDispatchIdentity
    status: AttemptStatus


@dataclass(frozen=True, kw_only=True)
class PreparedRecovery:
    outcome: RecoveryOutcome
    attempt: DispatchAttempt
    lease_expires_at: datetime


@dataclass(frozen=True, kw_only=True)
class ReceiptMetadata:
    provider_message_id: str | None = None
    trace_id: str | None = None
    provider_code: str | None = None
    http_status: int | None = None
    wecom_errcode: int | None = None


@dataclass(frozen=True, kw_only=True)
class ProviderReceiptEvidence:
    receipt_type: ReceiptType
    receipt_hash: str
    receipt_code: str | None
    metadata: ReceiptMetadata


@dataclass(frozen=True, kw_only=True)
class DispatchOutcomeReceipt:
    outcome: RecordOutcome
    request_id: str
    intent_id: str
    item_id: str
    attempt_id: str
    dispatch_outcome: DispatchOutcome
    evidence: ProviderReceiptEvidence | None
    attempt_status: AttemptStatus
    item_status: ItemStatus
    delivery_status: DeliveryStatus
    delivery_state_version: int
    item_state_version: int


@dataclass(frozen=True, kw_only=True)
class ReconcileClaim:
    outcome: ReconcileClaimOutcome
    request_id: str
    intent_id: str
    item_id: str
    attempt_id: str
    worker_id: str
    reconcile_token: str
    lease_seconds: int
    lease_expires_at: datetime
    claimed_lease_expires_at: datetime
    claim_delivery_state_version: int
    claim_item_state_version: int
    delivery_state_version: int
    item_state_version: int
    delivery_status: DeliveryStatus
    item_status: ItemStatus
    attempt_status: AttemptStatus
    dispatch_phase: DispatchPhase
    identity: ProviderDispatchIdentity


@dataclass(frozen=True, kw_only=True)
class ReconcileResultReceipt:
    outcome: RecordOutcome
    request_id: str
    claim_request_id: str
    intent_id: str
    item_id: str
    attempt_id: str
    reconcile_result: ReconcileResult
    evidence: ProviderReceiptEvidence
    attempt_status: AttemptStatus
    dispatch_phase: DispatchPhase
    item_status: ItemStatus
    delivery_status: DeliveryStatus
    delivery_state_version: int
    item_state_version: int
    next_attempt_at: datetime | None = None
    delay_seconds: int | None = None
    resolved_at: datetime | None = None


@dataclass(frozen=True, kw_only=True)
class WecomAppDispatchTarget:
    org_id: str
    corp_id: str
    wecom_userid: str


@dataclass(frozen=True, kw_only=True)
class WecomSmartRobotDispatchTarget:
    org_id: str
    chatid: str


DispatchTarget: TypeAlias = WecomAppDispatchTarget | WecomSmartRobotDispatchTarget


@dataclass(frozen=True, kw_only=True)
class DispatchPayload:
    outcome: DispatchPayloadOutcome
    payload_revision: int
    scheduled_run_id: str
    intent_id: str
    item_id: str
    item_key: str
    ordinal: int
    item_kind: str
    source_role: str
    source_revision: int
    source_identity_hash: str
    content_identity_hash: str
    result_hash: str
    target_hash: str
    channel: DispatchChannel
    target: DispatchTarget
    provider_revision: int
    delivery_state_version: int
    item_state_version: int
    message_type: str
    text: str
    payload_hash: str


@dataclass(frozen=True, kw_only=True)
class UnsupportedDispatchPayload:
    outcome: DispatchPayloadOutcome
    reason: UnsupportedReason


@dataclass(frozen=True, kw_only=True)
class UnavailableDispatchPayload:
    outcome: DispatchPayloadOutcome
    reason: UnavailableReason


DispatchPayloadReadback: TypeAlias = (
    DispatchPayload | UnsupportedDispatchPayload | UnavailableDispatchPayload
)


@dataclass(frozen=True, kw_only=True)
class UnsupportedTerminalizationReceipt:
    outcome: UnsupportedTerminalizationOutcome
    request_id: str
    intent_id: str
    item_id: str
    reason: UnsupportedReason
    item_status: ItemStatus
    delivery_status: DeliveryStatus
    delivery_state_version: int
    item_state_version: int
    terminalized_at: datetime


class ScheduledWecomDeliveryRepositoryPort(Protocol):
    async def claim_delivery(
        self, *, request_id: str, worker_id: str, lease_seconds: int = 60,
    ) -> DeliveryClaim | None: ...

    async def recover_prepared(
        self, *, request_id: str, worker_id: str, lease_seconds: int = 60,
    ) -> PreparedRecovery | None: ...

    async def read_dispatch_payload(
        self, claim: DeliveryClaim,
    ) -> DispatchPayloadReadback | None: ...

    async def terminalize_unsupported(
        self, claim: DeliveryClaim, *, request_id: str,
    ) -> UnsupportedTerminalizationReceipt: ...

    async def prepare_dispatch(
        self, claim: DeliveryClaim, identity: ProviderDispatchIdentity,
    ) -> DispatchAttempt: ...

    async def start_dispatch(self, attempt: DispatchAttempt) -> DispatchAttempt: ...

    async def read_attempt(self, attempt: DispatchAttempt) -> DispatchAttempt: ...

    async def record_dispatch_outcome(
        self, attempt: DispatchAttempt, *, request_id: str,
        dispatch_outcome: DispatchOutcome,
        evidence: ProviderReceiptEvidence | None,
    ) -> DispatchOutcomeReceipt: ...

    async def claim_reconcile(
        self, *, request_id: str, worker_id: str, lease_seconds: int = 60,
    ) -> ReconcileClaim | None: ...

    async def renew_reconcile(
        self, claim: ReconcileClaim, *, lease_seconds: int = 60,
    ) -> ReconcileClaim: ...

    async def read_reconcile(self, request_id: str) -> ReconcileClaim | None: ...

    async def record_still_unknown(
        self, claim: ReconcileClaim, *, request_id: str,
        evidence: ProviderReceiptEvidence, delay_seconds: int,
    ) -> ReconcileResultReceipt: ...

    async def record_definitive(
        self, claim: ReconcileClaim, *, request_id: str,
        result: ReconcileResult, evidence: ProviderReceiptEvidence,
    ) -> ReconcileResultReceipt: ...
