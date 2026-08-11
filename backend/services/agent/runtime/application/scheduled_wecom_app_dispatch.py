"""One-shot Runtime-owned Scheduled WeCom App orchestration."""

from __future__ import annotations

import asyncio

from loguru import logger

from services.agent.runtime.application.scheduled_wecom_receipt import (
    scheduled_wecom_app_identity,
    scheduled_wecom_receipt_hash,
    scheduled_wecom_request_id,
)
from services.agent.runtime.ports.scheduled_wecom_app_dispatch import (
    AppDispatchOutcome,
    AppDispatchResult,
    ScheduledWecomAppBinding,
    ScheduledWecomAppDispatchError,
)
from services.agent.runtime.ports.scheduled_wecom_delivery import (
    AttemptOperationOutcome,
    AttemptStatus,
    DeliveryClaim,
    DeliveryClaimOutcome,
    DispatchAttempt,
    DispatchChannel,
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
    WecomAppDispatchTarget,
)
from services.wecom.app_outbound import (
    PROVIDER_MESSAGE_ID_PATTERN,
    WecomAppOutboundErrorClass,
    WecomAppOutboundReceipt,
    WecomAppOutboundStatus,
)


class ScheduledWecomAppDispatchService:
    """Settle one already-routed App item without transport retries."""

    def __init__(self, repository: ScheduledWecomDeliveryRepositoryPort) -> None:
        self._repository = repository
        self._inflight: dict[
            tuple[str, str, str, int], asyncio.Task[AppDispatchResult]
        ] = {}

    async def dispatch_claimed(
        self,
        claim: DeliveryClaim,
        payload: DispatchPayload,
        binding: ScheduledWecomAppBinding,
    ) -> AppDispatchResult:
        target = _validate_routed_app(claim, payload, binding)
        identity = scheduled_wecom_app_identity(
            payload,
            org_id=binding.org_id,
            corp_id=binding.corp_id,
            agent_id=binding.agent_id,
        )
        key = _singleflight_key(claim.fence.item_id, identity)
        task = self._inflight.get(key)
        if task is None:
            task = asyncio.create_task(
                self._prepare_start_send(claim, payload, binding, target, identity),
            )
            self._inflight[key] = task
            task.add_done_callback(
                lambda completed, flight_key=key: self._finish_flight(
                    flight_key, completed,
                ),
            )
        return await asyncio.shield(task)

    async def dispatch_recovered_prepared(
        self,
        recovery: PreparedRecovery,
        payload: DispatchPayload,
        binding: ScheduledWecomAppBinding,
    ) -> AppDispatchResult:
        """Resume one verified PREPARED attempt without preparing again."""
        target = _validate_recovered_app(recovery, payload, binding)
        identity = scheduled_wecom_app_identity(
            payload,
            org_id=binding.org_id,
            corp_id=binding.corp_id,
            agent_id=binding.agent_id,
        )
        if identity != recovery.attempt.identity:
            raise ScheduledWecomAppDispatchError(
                "SCHEDULED_WECOM_APP_RECOVERY_IDENTITY_FENCED",
            )
        key = _singleflight_key(recovery.attempt.fence.item_id, identity)
        task = self._inflight.get(key)
        if task is None:
            task = asyncio.create_task(
                self._start_and_send(
                    recovery.attempt, payload, binding, target,
                ),
            )
            self._inflight[key] = task
            task.add_done_callback(
                lambda completed, flight_key=key: self._finish_flight(
                    flight_key, completed,
                ),
            )
        return await asyncio.shield(task)

    async def _prepare_start_send(
        self,
        claim: DeliveryClaim,
        payload: DispatchPayload,
        binding: ScheduledWecomAppBinding,
        target: WecomAppDispatchTarget,
        identity: ProviderDispatchIdentity,
    ) -> AppDispatchResult:
        attempt = await self._repository.prepare_dispatch(claim, identity)
        if (
            attempt.outcome is not AttemptOperationOutcome.PREPARED
            or attempt.status is not AttemptStatus.PREPARED
        ):
            return _already_persisted(payload)
        return await self._start_and_send(attempt, payload, binding, target)

    def _finish_flight(
        self,
        key: tuple[str, str, str, int],
        task: asyncio.Task[AppDispatchResult],
    ) -> None:
        if self._inflight.get(key) is task:
            self._inflight.pop(key, None)
        if not task.cancelled():
            task.exception()

    async def _start_and_send(
        self,
        attempt: DispatchAttempt,
        payload: DispatchPayload,
        binding: ScheduledWecomAppBinding,
        target: WecomAppDispatchTarget,
    ) -> AppDispatchResult:
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
            transport_result = await binding.transport.send_typed(
                provider_request_id=identity.provider_request_id,
                target=target.wecom_userid,
                payload={
                    "touser": target.wecom_userid,
                    "msgtype": "text",
                    "agentid": binding.agent_id,
                    "text": {"content": payload.text},
                },
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
            attempt,
            request_id=outcome_request_id,
            dispatch_outcome=outcome,
            evidence=evidence,
        )
        return AppDispatchResult(
            outcome=AppDispatchOutcome(outcome.value),
            intent_id=payload.intent_id,
            item_id=payload.item_id,
            dispatch_receipt=receipt,
        )


def _validate_routed_app(
    claim: DeliveryClaim,
    payload: DispatchPayload,
    binding: ScheduledWecomAppBinding,
) -> WecomAppDispatchTarget:
    fence = claim.fence
    if claim.outcome is DeliveryClaimOutcome.FENCED:
        raise ScheduledWecomAppDispatchError("SCHEDULED_WECOM_APP_CLAIM_FENCED")
    if (
        payload.intent_id,
        payload.item_id,
        payload.delivery_state_version,
        payload.item_state_version,
    ) != (
        fence.intent_id,
        fence.item_id,
        fence.delivery_state_version,
        fence.item_state_version,
    ):
        raise ScheduledWecomAppDispatchError("SCHEDULED_WECOM_APP_ROUTE_FENCED")
    if (
        payload.outcome is not DispatchPayloadOutcome.PAYLOAD
        or payload.channel is not DispatchChannel.APP
        or not isinstance(payload.target, WecomAppDispatchTarget)
    ):
        raise ScheduledWecomAppDispatchError("SCHEDULED_WECOM_APP_CHANNEL_REQUIRED")
    if (
        payload.target.org_id != binding.org_id
        or payload.target.corp_id != binding.corp_id
        or isinstance(binding.agent_id, bool)
        or not isinstance(binding.agent_id, int)
        or binding.agent_id <= 0
        or not callable(getattr(binding.transport, "send_typed", None))
    ):
        raise ScheduledWecomAppDispatchError("SCHEDULED_WECOM_APP_BINDING_FENCED")
    return payload.target


def _validate_recovered_app(
    recovery: PreparedRecovery,
    payload: DispatchPayload,
    binding: ScheduledWecomAppBinding,
) -> WecomAppDispatchTarget:
    attempt = recovery.attempt
    if (
        recovery.outcome not in (RecoveryOutcome.RECOVERED, RecoveryOutcome.READBACK)
        or attempt.outcome is not AttemptOperationOutcome.READBACK
        or attempt.status is not AttemptStatus.PREPARED
    ):
        raise ScheduledWecomAppDispatchError(
            "SCHEDULED_WECOM_APP_RECOVERY_STATE_FENCED",
        )
    if (
        payload.intent_id, payload.item_id, payload.delivery_state_version,
        payload.item_state_version,
    ) != (
        attempt.fence.intent_id, attempt.fence.item_id,
        attempt.fence.delivery_state_version, attempt.fence.item_state_version,
    ):
        raise ScheduledWecomAppDispatchError(
            "SCHEDULED_WECOM_APP_RECOVERY_ROUTE_FENCED",
        )
    if (
        payload.outcome is not DispatchPayloadOutcome.PAYLOAD
        or payload.channel is not DispatchChannel.APP
        or not isinstance(payload.target, WecomAppDispatchTarget)
    ):
        raise ScheduledWecomAppDispatchError(
            "SCHEDULED_WECOM_APP_RECOVERY_CHANNEL_FENCED",
        )
    if (
        payload.target.org_id != binding.org_id
        or payload.target.corp_id != binding.corp_id
        or isinstance(binding.agent_id, bool)
        or not isinstance(binding.agent_id, int)
        or binding.agent_id <= 0
        or not callable(getattr(binding.transport, "send_typed", None))
    ):
        raise ScheduledWecomAppDispatchError(
            "SCHEDULED_WECOM_APP_RECOVERY_BINDING_FENCED",
        )
    return payload.target


def _singleflight_key(
    item_id: str,
    identity: ProviderDispatchIdentity,
) -> tuple[str, str, str, int]:
    return (
        item_id,
        identity.provider_request_id,
        identity.idempotency_key,
        identity.provider_revision,
    )


def _transport_outcome(
    result: WecomAppOutboundReceipt,
    identity: ProviderDispatchIdentity,
) -> tuple[DispatchOutcome, ProviderReceiptEvidence | None]:
    if result.provider_request_id != identity.provider_request_id:
        return DispatchOutcome.UNKNOWN, None
    if result.status is WecomAppOutboundStatus.ACKNOWLEDGED:
        if (
            result.errcode != 0
            or result.error_class is not None
            or not _valid_message_id(result.provider_message_id)
        ):
            return DispatchOutcome.UNKNOWN, None
        metadata = ReceiptMetadata(
            provider_message_id=result.provider_message_id,
            wecom_errcode=0,
        )
        return DispatchOutcome.ACCEPTED, _evidence(
            DispatchOutcome.ACCEPTED, "acknowledged", metadata, identity,
        )
    if result.status is WecomAppOutboundStatus.REJECTED:
        rejection = _rejection_evidence(result, identity)
        if rejection is not None:
            return DispatchOutcome.REJECTED, rejection
    return DispatchOutcome.UNKNOWN, None


def _rejection_evidence(
    result: WecomAppOutboundReceipt,
    identity: ProviderDispatchIdentity,
) -> ProviderReceiptEvidence | None:
    if (
        result.provider_message_id is not None
        or isinstance(result.errcode, bool)
        or not isinstance(result.errcode, int)
        or not -(2**31) <= result.errcode < 2**31
    ):
        return None
    if (
        result.error_class is WecomAppOutboundErrorClass.PROVIDER_REJECTED
        and result.errcode != 0
    ):
        code = "provider_rejected"
    elif (
        result.error_class is WecomAppOutboundErrorClass.PROVIDER_PARTIAL_REJECTED
        and result.errcode == 0
    ):
        code = "provider_partial_rejected"
    else:
        return None
    return _evidence(
        DispatchOutcome.REJECTED,
        code,
        ReceiptMetadata(wecom_errcode=result.errcode),
        identity,
    )


def _valid_message_id(value: str | None) -> bool:
    return value is None or bool(PROVIDER_MESSAGE_ID_PATTERN.fullmatch(value))


def _evidence(
    outcome: DispatchOutcome,
    code: str,
    metadata: ReceiptMetadata,
    identity: ProviderDispatchIdentity,
) -> ProviderReceiptEvidence:
    return ProviderReceiptEvidence(
        receipt_type=ReceiptType.WECOM_APP,
        receipt_hash=scheduled_wecom_receipt_hash(
            dispatch_outcome=outcome,
            receipt_type=ReceiptType.WECOM_APP,
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
        raise RuntimeError("SCHEDULED_WECOM_APP_CANCELLATION_TASK_REQUIRED")
    cancellation_count = 0
    while current.cancelling():
        current.uncancel()
        cancellation_count += 1
    task = asyncio.create_task(repository.record_dispatch_outcome(
        attempt,
        request_id=request_id,
        dispatch_outcome=DispatchOutcome.UNKNOWN,
        evidence=None,
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
                "Scheduled WeCom App cancellation UNKNOWN persistence failed | "
                "attempt_id={} item_id={}",
                attempt.attempt_id,
                attempt.fence.item_id,
            )
    finally:
        for _ in range(cancellation_count):
            current.cancel()


def _already_persisted(payload: DispatchPayload) -> AppDispatchResult:
    return AppDispatchResult(
        outcome=AppDispatchOutcome.ALREADY_PERSISTED,
        intent_id=payload.intent_id,
        item_id=payload.item_id,
    )
