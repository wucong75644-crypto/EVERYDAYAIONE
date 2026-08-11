"""Read-only provider reconciliation for Scheduled Runtime WeCom."""

from __future__ import annotations

from services.agent.runtime.application.scheduled_wecom_receipt import (
    scheduled_wecom_reconcile_readback_hash,
    scheduled_wecom_request_id,
)
from services.agent.runtime.ports.scheduled_wecom_delivery import (
    ProviderReceiptEvidence,
    ReceiptMetadata,
    ReceiptType,
    ReconcileClaim,
    ReconcileResult,
    ReconcileResultReceipt,
    ScheduledWecomDeliveryRepositoryPort,
)
from services.agent.runtime.ports.scheduled_wecom_smart_dispatch import (
    SmartRobotReadbackResolverPort,
    SmartRobotReadbackTransportPort,
)
from services.wecom.ws_outbound import (
    WecomOutboundAckResult,
    WecomOutboundErrorClass,
    WecomOutboundStatus,
)


_SMART_PREFIX = "scheduled-wecom-smart:"
_APP_PREFIX = "scheduled-wecom-app:"
_RECONCILE_DELAY_SECONDS = 60


class ScheduledWecomReconcileError(RuntimeError):
    """Stable failure-closed error before reconciliation persistence."""


class ScheduledWecomReconcileService:
    """Claim one ambiguous attempt and read only existing provider evidence."""

    def __init__(
        self,
        repository: ScheduledWecomDeliveryRepositoryPort,
        smart_readback_resolver: SmartRobotReadbackResolverPort,
    ) -> None:
        self._repository = repository
        self._smart_readback_resolver = smart_readback_resolver

    async def reconcile_once(
        self, *, request_id: str, worker_id: str, lease_seconds: int = 60,
    ) -> ReconcileResultReceipt | None:
        claim = await self._repository.claim_reconcile(
            request_id=request_id,
            worker_id=worker_id,
            lease_seconds=lease_seconds,
        )
        if claim is None:
            return None

        provider_request_id = claim.identity.provider_request_id
        if provider_request_id.startswith(_SMART_PREFIX):
            result, evidence = await self._read_smart(claim)
        elif provider_request_id.startswith(_APP_PREFIX):
            result = ReconcileResult.STILL_UNKNOWN
            evidence = _evidence(
                claim, result, ReceiptType.WECOM_APP, "readback_unsupported",
            )
        else:
            raise ScheduledWecomReconcileError(
                "SCHEDULED_WECOM_RECONCILE_PROVIDER_IDENTITY_UNSUPPORTED",
            )

        result_request_id = scheduled_wecom_request_id(
            "reconcile-result", claim.request_id,
        )
        if result is ReconcileResult.STILL_UNKNOWN:
            return await self._repository.record_still_unknown(
                claim,
                request_id=result_request_id,
                evidence=evidence,
                delay_seconds=_RECONCILE_DELAY_SECONDS,
            )
        return await self._repository.record_definitive(
            claim,
            request_id=result_request_id,
            result=result,
            evidence=evidence,
        )

    async def _read_smart(
        self, claim: ReconcileClaim,
    ) -> tuple[ReconcileResult, ProviderReceiptEvidence]:
        transport = await self._smart_readback_resolver.resolve_smart_readback(
            claim.org_id,
        )
        if not _valid_readback_transport(transport, claim.org_id):
            result = ReconcileResult.STILL_UNKNOWN
            return result, _evidence(
                claim, result, ReceiptType.WECOM_SMART_ROBOT,
                "readback_unavailable",
            )

        readback = transport.lookup_outbound_result(
            claim.identity.provider_request_id,
        )
        result, code, metadata = _smart_readback_result(readback, claim)
        return result, _evidence(
            claim, result, ReceiptType.WECOM_SMART_ROBOT, code, metadata,
        )


def _smart_readback_result(
    readback: WecomOutboundAckResult | None,
    claim: ReconcileClaim,
) -> tuple[ReconcileResult, str, ReceiptMetadata]:
    if readback is None:
        return (
            ReconcileResult.STILL_UNKNOWN,
            "lookup_miss_or_pending",
            ReceiptMetadata(),
        )
    if (
        type(readback) is not WecomOutboundAckResult
        or readback.provider_request_id != claim.identity.provider_request_id
    ):
        raise ScheduledWecomReconcileError(
            "SCHEDULED_WECOM_RECONCILE_READBACK_IDENTITY_INVALID",
        )
    if (
        readback.status is WecomOutboundStatus.ACKNOWLEDGED
        and readback.errcode is None
        and readback.error_class is None
    ):
        return ReconcileResult.ACCEPTED, "acknowledged", ReceiptMetadata()
    if readback.status is WecomOutboundStatus.REJECTED:
        if not (
            readback.error_class is WecomOutboundErrorClass.PROVIDER_REJECTED
            and not isinstance(readback.errcode, bool)
            and isinstance(readback.errcode, int)
            and readback.errcode != 0
            and -(2**31) <= readback.errcode < 2**31
        ):
            raise ScheduledWecomReconcileError(
                "SCHEDULED_WECOM_RECONCILE_REJECTED_EVIDENCE_INVALID",
            )
        return (
            ReconcileResult.REJECTED,
            "provider_rejected",
            ReceiptMetadata(wecom_errcode=readback.errcode),
        )
    if readback.status is WecomOutboundStatus.NOT_STARTED:
        code = "not_started"
    elif readback.status is WecomOutboundStatus.UNKNOWN:
        code = "unknown"
    else:
        code = "readback_invalid"
    return ReconcileResult.STILL_UNKNOWN, code, ReceiptMetadata()


def _valid_readback_transport(
    transport: SmartRobotReadbackTransportPort | None,
    org_id: str,
) -> bool:
    return bool(
        transport is not None
        and getattr(transport, "org_id", None) == org_id
        and callable(getattr(transport, "lookup_outbound_result", None))
    )


def _evidence(
    claim: ReconcileClaim,
    result: ReconcileResult,
    receipt_type: ReceiptType,
    code: str,
    metadata: ReceiptMetadata = ReceiptMetadata(),
) -> ProviderReceiptEvidence:
    return ProviderReceiptEvidence(
        receipt_type=receipt_type,
        receipt_hash=scheduled_wecom_reconcile_readback_hash(
            reconcile_result=result,
            receipt_type=receipt_type,
            receipt_code=code,
            metadata=metadata,
            identity=claim.identity,
        ),
        receipt_code=code,
        metadata=metadata,
    )
