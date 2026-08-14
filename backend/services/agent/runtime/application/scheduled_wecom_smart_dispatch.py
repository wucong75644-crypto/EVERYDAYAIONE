"""One-shot Runtime-owned Scheduled WeCom Smart Robot orchestration."""

from __future__ import annotations

import asyncio

from loguru import logger

from services.agent.runtime.application.scheduled_wecom_receipt import (
    scheduled_wecom_receipt_hash,
    scheduled_wecom_request_id,
    scheduled_wecom_smart_identity,
)
from services.agent.runtime.ports.scheduled_wecom_delivery import (
    AttemptStatus,
    AttemptOperationOutcome,
    DeliveryClaim,
    DeliveryClaimOutcome,
    DispatchChannel,
    DispatchAttempt,
    DispatchOutcome,
    DispatchPayload,
    DispatchPayloadOutcome,
    PreparedRecovery,
    ProviderDispatchIdentity,
    ProviderReceiptEvidence,
    ReceiptMetadata,
    ReceiptType,
    RecoveryOutcome,
    ScheduledWecomDeliveryRepositoryPort,
    WecomSmartRobotDispatchTarget,
)
from services.agent.runtime.ports.scheduled_wecom_smart_dispatch import (
    ScheduledWecomSmartDispatchError,
    SmartRobotDispatchOutcome,
    SmartRobotDispatchResult,
    SmartRobotProactiveTransportPort,
    SmartRobotTransportResolverPort,
)
from services.wecom.ws_outbound import (
    WecomOutboundAckResult,
    WecomOutboundErrorClass,
    WecomOutboundStatus,
)


class ScheduledWecomSmartDispatchService:
    """Settle one already-routed Smart Robot item without transport retries."""

    def __init__(
        self,
        repository: ScheduledWecomDeliveryRepositoryPort,
        transport_resolver: SmartRobotTransportResolverPort,
    ) -> None:
        self._repository = repository
        self._transport_resolver = transport_resolver
        self._inflight: dict[
            tuple[str, str, str, int], asyncio.Task[SmartRobotDispatchResult]
        ] = {}

    async def dispatch_claimed(
        self, claim: DeliveryClaim, payload: DispatchPayload,
    ) -> SmartRobotDispatchResult:
        target = _validate_routed_smart(claim, payload)
        identity = scheduled_wecom_smart_identity(payload)
        key = _singleflight_key(claim.fence.item_id, identity)
        task = self._inflight.get(key)
        if task is None:
            task = asyncio.create_task(
                self._resolve_prepare_start_send(claim, payload, target, identity),
            )
            self._inflight[key] = task
            task.add_done_callback(
                lambda completed, flight_key=key: self._finish_flight(
                    flight_key, completed,
                ),
            )
        return await asyncio.shield(task)

    async def dispatch_recovered_prepared(
        self, recovery: PreparedRecovery, payload: DispatchPayload,
    ) -> SmartRobotDispatchResult:
        """Resume one verified PREPARED attempt without preparing again."""
        target = _validate_recovered_smart(recovery, payload)
        identity = scheduled_wecom_smart_identity(payload)
        if identity != recovery.attempt.identity:
            raise ScheduledWecomSmartDispatchError(
                "SCHEDULED_WECOM_SMART_RECOVERY_IDENTITY_FENCED",
            )
        try:
            transport = await self._transport_resolver.resolve_smart_transport(
                target.org_id,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            return _unavailable(payload)
        if not _valid_transport(transport, target.org_id):
            return _unavailable(payload)
        key = _singleflight_key(recovery.attempt.fence.item_id, identity)
        task = self._inflight.get(key)
        if task is None:
            task = asyncio.create_task(
                self._start_and_send(recovery.attempt, payload, target, transport),
            )
            self._inflight[key] = task
            task.add_done_callback(
                lambda completed, flight_key=key: self._finish_flight(
                    flight_key, completed,
                ),
            )
        return await asyncio.shield(task)

    async def _resolve_prepare_start_send(
        self,
        claim: DeliveryClaim,
        payload: DispatchPayload,
        target: WecomSmartRobotDispatchTarget,
        identity: ProviderDispatchIdentity,
    ) -> SmartRobotDispatchResult:
        try:
            transport = await self._transport_resolver.resolve_smart_transport(
                target.org_id,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            return _unavailable(payload)
        if not _valid_transport(transport, target.org_id):
            return _unavailable(payload)
        attempt = await self._repository.prepare_dispatch(claim, identity)
        if (
            attempt.outcome is not AttemptOperationOutcome.PREPARED
            or attempt.status is not AttemptStatus.PREPARED
        ):
            return _already_persisted(payload)
        return await self._start_and_send(attempt, payload, target, transport)

    def _finish_flight(
        self,
        key: tuple[str, str, str, int],
        task: asyncio.Task[SmartRobotDispatchResult],
    ) -> None:
        if self._inflight.get(key) is task:
            self._inflight.pop(key, None)
        if not task.cancelled():
            task.exception()

    async def _start_and_send(
        self, attempt: DispatchAttempt, payload: DispatchPayload,
        target: WecomSmartRobotDispatchTarget,
        transport: SmartRobotProactiveTransportPort,
    ) -> SmartRobotDispatchResult:
        attempt = await self._repository.start_dispatch(attempt)
        if (
            attempt.outcome is not AttemptOperationOutcome.DISPATCH_STARTED
            or attempt.status is not AttemptStatus.DISPATCH_STARTED
        ):
            return _already_persisted(payload)
        identity = attempt.identity
        outcome_request_id = scheduled_wecom_request_id(
            "outcome", f"{attempt.attempt_id}:{identity.idempotency_key}",
        )
        try:
            transport_result = await transport.send_proactive_typed(
                identity.provider_request_id,
                target.chatid,
                "markdown",
                {"content": payload.text},
            )
            outcome, evidence = _transport_outcome(transport_result, identity)
        except asyncio.CancelledError:
            await _best_effort_unknown_after_cancellation(
                self._repository, attempt, outcome_request_id,
            )
            raise
        except Exception:
            outcome, evidence = DispatchOutcome.UNKNOWN, None
        receipt = await self._repository.record_dispatch_outcome(
            attempt, request_id=outcome_request_id,
            dispatch_outcome=outcome, evidence=evidence,
        )
        return SmartRobotDispatchResult(
            outcome=SmartRobotDispatchOutcome(outcome.value),
            intent_id=payload.intent_id, item_id=payload.item_id,
            dispatch_receipt=receipt,
        )


def _validate_routed_smart(
    claim: DeliveryClaim, payload: DispatchPayload,
) -> WecomSmartRobotDispatchTarget:
    fence = claim.fence
    if claim.outcome is DeliveryClaimOutcome.FENCED:
        raise ScheduledWecomSmartDispatchError("SCHEDULED_WECOM_SMART_CLAIM_FENCED")
    if (
        payload.intent_id, payload.item_id, payload.delivery_state_version,
        payload.item_state_version,
    ) != (
        fence.intent_id, fence.item_id, fence.delivery_state_version,
        fence.item_state_version,
    ):
        raise ScheduledWecomSmartDispatchError("SCHEDULED_WECOM_SMART_ROUTE_FENCED")
    if (
        payload.outcome is not DispatchPayloadOutcome.PAYLOAD
        or payload.channel is not DispatchChannel.SMART_ROBOT
        or not isinstance(payload.target, WecomSmartRobotDispatchTarget)
    ):
        raise ScheduledWecomSmartDispatchError("SCHEDULED_WECOM_SMART_CHANNEL_REQUIRED")
    return payload.target


def _validate_recovered_smart(
    recovery: PreparedRecovery, payload: DispatchPayload,
) -> WecomSmartRobotDispatchTarget:
    attempt = recovery.attempt
    payload_versions = attempt.payload_versions
    if (
        recovery.outcome not in (RecoveryOutcome.RECOVERED, RecoveryOutcome.READBACK)
        or attempt.outcome is not AttemptOperationOutcome.READBACK
        or attempt.status is not AttemptStatus.PREPARED
    ):
        raise ScheduledWecomSmartDispatchError(
            "SCHEDULED_WECOM_SMART_RECOVERY_STATE_FENCED",
        )
    if (
        payload.intent_id, payload.item_id, payload.delivery_state_version,
        payload.item_state_version,
    ) != (
        attempt.fence.intent_id, attempt.fence.item_id,
        payload_versions.delivery_state_version, payload_versions.item_state_version,
    ):
        raise ScheduledWecomSmartDispatchError(
            "SCHEDULED_WECOM_SMART_RECOVERY_ROUTE_FENCED",
        )
    if (
        payload.outcome is not DispatchPayloadOutcome.PAYLOAD
        or payload.channel is not DispatchChannel.SMART_ROBOT
        or not isinstance(payload.target, WecomSmartRobotDispatchTarget)
    ):
        raise ScheduledWecomSmartDispatchError(
            "SCHEDULED_WECOM_SMART_RECOVERY_CHANNEL_FENCED",
        )
    return payload.target


def _singleflight_key(
    item_id: str, identity: ProviderDispatchIdentity,
) -> tuple[str, str, str, int]:
    return (
        item_id, identity.provider_request_id,
        identity.idempotency_key, identity.provider_revision,
    )


def _valid_transport(
    transport: SmartRobotProactiveTransportPort | None, org_id: str,
) -> bool:
    return bool(
        transport is not None
        and getattr(transport, "org_id", None) == org_id
        and getattr(transport, "is_connected", None) is True
        and callable(getattr(transport, "send_proactive_typed", None))
    )


def _transport_outcome(
    result: WecomOutboundAckResult, identity: ProviderDispatchIdentity,
) -> tuple[DispatchOutcome, ProviderReceiptEvidence | None]:
    if result.provider_request_id != identity.provider_request_id:
        return DispatchOutcome.UNKNOWN, None
    if result.status is WecomOutboundStatus.ACKNOWLEDGED:
        if result.errcode is not None or result.error_class is not None:
            return DispatchOutcome.UNKNOWN, None
        return DispatchOutcome.ACCEPTED, _evidence(
            DispatchOutcome.ACCEPTED, "acknowledged", ReceiptMetadata(), identity,
        )
    if result.status is WecomOutboundStatus.REJECTED:
        if (
            result.error_class is not WecomOutboundErrorClass.PROVIDER_REJECTED
            or isinstance(result.errcode, bool) or not isinstance(result.errcode, int)
            or not -(2**31) <= result.errcode < 2**31
        ):
            return DispatchOutcome.UNKNOWN, None
        return DispatchOutcome.REJECTED, _evidence(
            DispatchOutcome.REJECTED, "provider_rejected",
            ReceiptMetadata(wecom_errcode=result.errcode), identity,
        )
    return DispatchOutcome.UNKNOWN, None


def _evidence(
    outcome: DispatchOutcome,
    code: str,
    metadata: ReceiptMetadata,
    identity: ProviderDispatchIdentity,
) -> ProviderReceiptEvidence:
    return ProviderReceiptEvidence(
        receipt_type=ReceiptType.WECOM_SMART_ROBOT,
        receipt_hash=scheduled_wecom_receipt_hash(
            dispatch_outcome=outcome,
            receipt_type=ReceiptType.WECOM_SMART_ROBOT,
            receipt_code=code,
            metadata=metadata,
            identity=identity,
        ),
        receipt_code=code,
        metadata=metadata,
    )


async def _best_effort_unknown_after_cancellation(
    repository: ScheduledWecomDeliveryRepositoryPort,
    attempt: DispatchAttempt,
    request_id: str,
) -> None:
    current = asyncio.current_task()
    if current is None:
        raise RuntimeError("SCHEDULED_WECOM_CANCELLATION_TASK_REQUIRED")
    cancellation_count = 0
    while current.cancelling():
        current.uncancel()
        cancellation_count += 1
    task = asyncio.create_task(repository.record_dispatch_outcome(
        attempt, request_id=request_id,
        dispatch_outcome=DispatchOutcome.UNKNOWN, evidence=None,
    ))
    try:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                current.uncancel()
                cancellation_count += 1
            except Exception:
                break
        try:
            task.result()
        except (asyncio.CancelledError, Exception):
            logger.warning(
                "Scheduled WeCom cancellation UNKNOWN persistence failed | "
                "attempt_id={} item_id={}",
                attempt.attempt_id, attempt.fence.item_id,
            )
    finally:
        for _ in range(cancellation_count):
            current.cancel()


def _already_persisted(payload: DispatchPayload) -> SmartRobotDispatchResult:
    return SmartRobotDispatchResult(
        outcome=SmartRobotDispatchOutcome.ALREADY_PERSISTED,
        intent_id=payload.intent_id, item_id=payload.item_id,
    )


def _unavailable(payload: DispatchPayload) -> SmartRobotDispatchResult:
    return SmartRobotDispatchResult(
        outcome=SmartRobotDispatchOutcome.UNAVAILABLE,
        intent_id=payload.intent_id, item_id=payload.item_id,
    )
