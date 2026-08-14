"""Runtime-owned router for one Scheduled WeCom delivery claim."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from services.agent.runtime.application.scheduled_wecom_app_dispatch import (
    ScheduledWecomAppDispatchService,
)
from services.agent.runtime.application.scheduled_wecom_receipt import (
    scheduled_wecom_app_identity,
    scheduled_wecom_request_id,
    scheduled_wecom_smart_identity,
)
from services.agent.runtime.application.scheduled_wecom_smart_dispatch import (
    ScheduledWecomSmartDispatchService,
)
from services.agent.runtime.ports.scheduled_wecom_app_dispatch import (
    AppBindingResolverPort,
    AppDispatchOutcome,
    AppDispatchResult,
    ScheduledWecomAppBinding,
)
from services.agent.runtime.ports.scheduled_wecom_delivery import (
    AttemptOperationOutcome,
    AttemptStatus,
    DeliveryClaim,
    DeliveryClaimOutcome,
    DispatchChannel,
    DispatchPayload,
    DispatchPayloadOutcome,
    PreparedRecovery,
    RecoveryOutcome,
    UnsupportedDispatchPayload,
    UnavailableDispatchPayload,
    WecomAppDispatchTarget,
    WecomSmartRobotDispatchTarget,
    ScheduledWecomDeliveryRepositoryPort,
)
from services.agent.runtime.ports.scheduled_wecom_router import (
    ScheduledWecomRouteOutcome,
    ScheduledWecomRouteResult,
)
from services.agent.runtime.ports.scheduled_wecom_smart_dispatch import (
    SmartRobotDispatchOutcome,
    SmartRobotDispatchResult,
)


@dataclass(frozen=True)
class _RouterFlight:
    worker_id: str
    lease_seconds: int
    task: asyncio.Task[ScheduledWecomRouteResult]


class ScheduledWecomRouter:
    """Claim, read once, and route without owning credentials or recovery loops."""

    def __init__(
        self,
        repository: ScheduledWecomDeliveryRepositoryPort,
        smart_dispatch: ScheduledWecomSmartDispatchService,
        app_dispatch: ScheduledWecomAppDispatchService,
        app_binding_resolver: AppBindingResolverPort,
    ) -> None:
        self._repository = repository
        self._smart_dispatch = smart_dispatch
        self._app_dispatch = app_dispatch
        self._app_binding_resolver = app_binding_resolver
        self._inflight: dict[str, _RouterFlight] = {}
        self._recovery_inflight: dict[str, _RouterFlight] = {}

    async def dispatch_once(
        self, *, request_id: str, worker_id: str, lease_seconds: int = 60,
    ) -> ScheduledWecomRouteResult:
        flight = self._inflight.get(request_id)
        if flight is not None and (
            flight.worker_id != worker_id or flight.lease_seconds != lease_seconds
        ):
            return ScheduledWecomRouteResult(
                outcome=ScheduledWecomRouteOutcome.UNAVAILABLE,
            )
        if flight is None:
            task = asyncio.create_task(
                self._claim_and_route(
                    request_id=request_id,
                    worker_id=worker_id,
                    lease_seconds=lease_seconds,
                ),
            )
            flight = _RouterFlight(worker_id, lease_seconds, task)
            self._inflight[request_id] = flight
            task.add_done_callback(
                lambda completed, key=request_id: self._finish_flight(key, completed),
            )
        return await asyncio.shield(flight.task)

    async def recover_prepared_once(
        self, *, request_id: str, worker_id: str, lease_seconds: int = 60,
    ) -> ScheduledWecomRouteResult:
        """Resume one durable PREPARED attempt without preparing a new attempt."""
        flight = self._recovery_inflight.get(request_id)
        if flight is not None and (
            flight.worker_id != worker_id or flight.lease_seconds != lease_seconds
        ):
            return ScheduledWecomRouteResult(
                outcome=ScheduledWecomRouteOutcome.UNAVAILABLE,
            )
        if flight is None:
            task = asyncio.create_task(self._recover_prepared_and_route(
                request_id=request_id,
                worker_id=worker_id,
                lease_seconds=lease_seconds,
            ))
            flight = _RouterFlight(worker_id, lease_seconds, task)
            self._recovery_inflight[request_id] = flight
            task.add_done_callback(
                lambda completed, key=request_id: self._finish_recovery_flight(
                    key, completed,
                ),
            )
        return await asyncio.shield(flight.task)

    async def _recover_prepared_and_route(
        self, *, request_id: str, worker_id: str, lease_seconds: int,
    ) -> ScheduledWecomRouteResult:
        try:
            recovery = await self._repository.recover_prepared(
                request_id=request_id,
                worker_id=worker_id,
                lease_seconds=lease_seconds,
            )
        except Exception:
            return ScheduledWecomRouteResult(
                outcome=ScheduledWecomRouteOutcome.UNAVAILABLE,
            )
        if recovery is None:
            return ScheduledWecomRouteResult(outcome=ScheduledWecomRouteOutcome.EMPTY)
        if not _readable_prepared_recovery(recovery):
            return _recovery_result(ScheduledWecomRouteOutcome.UNAVAILABLE, recovery)
        try:
            payload = await self._repository.read_prepared_dispatch_payload(recovery)
        except Exception:
            return _recovery_result(ScheduledWecomRouteOutcome.UNAVAILABLE, recovery)
        if isinstance(payload, UnavailableDispatchPayload):
            return ScheduledWecomRouteResult(
                outcome=ScheduledWecomRouteOutcome.UNAVAILABLE,
                intent_id=recovery.attempt.fence.intent_id,
                item_id=recovery.attempt.fence.item_id,
                unavailable_reason=payload.reason,
            )
        if not isinstance(payload, DispatchPayload):
            return _recovery_result(ScheduledWecomRouteOutcome.UNAVAILABLE, recovery)
        if not _prepared_payload_matches_recovery(recovery, payload):
            return _recovery_result(ScheduledWecomRouteOutcome.UNAVAILABLE, recovery)
        if payload.channel is DispatchChannel.SMART_ROBOT:
            if not isinstance(payload.target, WecomSmartRobotDispatchTarget):
                return _recovery_result(
                    ScheduledWecomRouteOutcome.UNAVAILABLE, recovery,
                )
            if scheduled_wecom_smart_identity(payload) != recovery.attempt.identity:
                return _recovery_result(
                    ScheduledWecomRouteOutcome.UNAVAILABLE, recovery,
                )
            result = await self._smart_dispatch.dispatch_recovered_prepared(
                recovery, payload,
            )
            return _smart_result(result)
        if payload.channel is DispatchChannel.APP:
            return await self._route_recovered_app(recovery, payload)
        return _recovery_result(ScheduledWecomRouteOutcome.UNAVAILABLE, recovery)

    async def _claim_and_route(
        self, *, request_id: str, worker_id: str, lease_seconds: int,
    ) -> ScheduledWecomRouteResult:
        try:
            claim = await self._repository.claim_delivery(
                request_id=request_id,
                worker_id=worker_id,
                lease_seconds=lease_seconds,
            )
        except Exception:
            return ScheduledWecomRouteResult(
                outcome=ScheduledWecomRouteOutcome.UNAVAILABLE,
            )
        if claim is None:
            return ScheduledWecomRouteResult(outcome=ScheduledWecomRouteOutcome.EMPTY)
        if claim.outcome is DeliveryClaimOutcome.FENCED:
            return _claim_result(ScheduledWecomRouteOutcome.UNAVAILABLE, claim)
        try:
            payload = await self._repository.read_dispatch_payload(claim)
        except Exception:
            return _claim_result(ScheduledWecomRouteOutcome.UNAVAILABLE, claim)
        if payload is None:
            return _claim_result(ScheduledWecomRouteOutcome.UNAVAILABLE, claim)
        if isinstance(payload, UnavailableDispatchPayload):
            return ScheduledWecomRouteResult(
                outcome=ScheduledWecomRouteOutcome.UNAVAILABLE,
                intent_id=claim.fence.intent_id,
                item_id=claim.fence.item_id,
                unavailable_reason=payload.reason,
            )
        if isinstance(payload, UnsupportedDispatchPayload):
            return await self._terminalize_unsupported(claim, payload, request_id)
        if not _payload_matches_claim(claim, payload):
            return _claim_result(ScheduledWecomRouteOutcome.UNAVAILABLE, claim)
        if payload.channel is DispatchChannel.SMART_ROBOT:
            if not isinstance(payload.target, WecomSmartRobotDispatchTarget):
                return _claim_result(ScheduledWecomRouteOutcome.UNAVAILABLE, claim)
            result = await self._smart_dispatch.dispatch_claimed(claim, payload)
            return _smart_result(result)
        if payload.channel is DispatchChannel.APP:
            return await self._route_app(claim, payload)
        return _claim_result(ScheduledWecomRouteOutcome.UNAVAILABLE, claim)

    async def _terminalize_unsupported(
        self,
        claim: DeliveryClaim,
        payload: UnsupportedDispatchPayload,
        claim_request_id: str,
    ) -> ScheduledWecomRouteResult:
        request_id = scheduled_wecom_request_id(
            "unsupported",
            ":".join((
                claim.fence.intent_id,
                claim.fence.item_id,
                claim_request_id,
                payload.reason.value,
            )),
        )
        try:
            receipt = await self._repository.terminalize_unsupported(
                claim, request_id=request_id,
            )
        except Exception:
            return _claim_result(ScheduledWecomRouteOutcome.UNAVAILABLE, claim)
        return ScheduledWecomRouteResult(
            outcome=ScheduledWecomRouteOutcome.UNSUPPORTED,
            intent_id=receipt.intent_id,
            item_id=receipt.item_id,
            unsupported_reason=receipt.reason,
            terminalization_receipt=receipt,
        )

    async def _route_app(
        self, claim: DeliveryClaim, payload: DispatchPayload,
    ) -> ScheduledWecomRouteResult:
        target = payload.target
        if not isinstance(target, WecomAppDispatchTarget):
            return _claim_result(ScheduledWecomRouteOutcome.UNAVAILABLE, claim)
        try:
            binding = await self._app_binding_resolver.resolve_app_binding(
                org_id=target.org_id,
                corp_id=target.corp_id,
            )
        except Exception:
            binding = None
        if not _valid_app_binding(binding, target):
            return _claim_result(ScheduledWecomRouteOutcome.CONFIG_UNAVAILABLE, claim)
        result = await self._app_dispatch.dispatch_claimed(claim, payload, binding)
        return _app_result(result)

    async def _route_recovered_app(
        self, recovery: PreparedRecovery, payload: DispatchPayload,
    ) -> ScheduledWecomRouteResult:
        target = payload.target
        if not isinstance(target, WecomAppDispatchTarget):
            return _recovery_result(ScheduledWecomRouteOutcome.UNAVAILABLE, recovery)
        try:
            binding = await self._app_binding_resolver.resolve_app_binding(
                org_id=target.org_id,
                corp_id=target.corp_id,
            )
        except Exception:
            binding = None
        if not _valid_app_binding(binding, target):
            return _recovery_result(
                ScheduledWecomRouteOutcome.CONFIG_UNAVAILABLE, recovery,
            )
        identity = scheduled_wecom_app_identity(
            payload,
            org_id=binding.org_id,
            corp_id=binding.corp_id,
            agent_id=binding.agent_id,
        )
        if identity != recovery.attempt.identity:
            return _recovery_result(ScheduledWecomRouteOutcome.UNAVAILABLE, recovery)
        result = await self._app_dispatch.dispatch_recovered_prepared(
            recovery, payload, binding,
        )
        return _app_result(result)

    def _finish_flight(
        self, key: str, task: asyncio.Task[ScheduledWecomRouteResult],
    ) -> None:
        flight = self._inflight.get(key)
        if flight is not None and flight.task is task:
            self._inflight.pop(key, None)
        if not task.cancelled():
            task.exception()

    def _finish_recovery_flight(
        self, key: str, task: asyncio.Task[ScheduledWecomRouteResult],
    ) -> None:
        flight = self._recovery_inflight.get(key)
        if flight is not None and flight.task is task:
            self._recovery_inflight.pop(key, None)
        if not task.cancelled():
            task.exception()


def _payload_matches_claim(claim: DeliveryClaim, payload: DispatchPayload) -> bool:
    fence = claim.fence
    return payload.outcome is DispatchPayloadOutcome.PAYLOAD and (
        payload.intent_id,
        payload.item_id,
        payload.delivery_state_version,
        payload.item_state_version,
    ) == (
        fence.intent_id,
        fence.item_id,
        fence.delivery_state_version,
        fence.item_state_version,
    )


def _readable_prepared_recovery(recovery: PreparedRecovery) -> bool:
    attempt = recovery.attempt
    return (
        recovery.outcome in (RecoveryOutcome.RECOVERED, RecoveryOutcome.READBACK)
        and attempt.outcome is AttemptOperationOutcome.READBACK
        and attempt.status is AttemptStatus.PREPARED
    )


def _prepared_payload_matches_recovery(
    recovery: PreparedRecovery, payload: DispatchPayload,
) -> bool:
    attempt = recovery.attempt
    versions = attempt.payload_versions
    return payload.outcome is DispatchPayloadOutcome.PAYLOAD and (
        payload.intent_id,
        payload.item_id,
        payload.delivery_state_version,
        payload.item_state_version,
        payload.provider_revision,
    ) == (
        attempt.fence.intent_id,
        attempt.fence.item_id,
        versions.delivery_state_version,
        versions.item_state_version,
        attempt.identity.provider_revision,
    )


def _valid_app_binding(
    binding: ScheduledWecomAppBinding | None,
    target: WecomAppDispatchTarget,
) -> bool:
    return bool(
        isinstance(binding, ScheduledWecomAppBinding)
        and binding.org_id == target.org_id
        and binding.corp_id == target.corp_id
        and isinstance(binding.agent_id, int)
        and not isinstance(binding.agent_id, bool)
        and binding.agent_id > 0
        and callable(getattr(binding.transport, "send_typed", None))
    )


def _claim_result(
    outcome: ScheduledWecomRouteOutcome, claim: DeliveryClaim,
) -> ScheduledWecomRouteResult:
    return ScheduledWecomRouteResult(
        outcome=outcome,
        intent_id=claim.fence.intent_id,
        item_id=claim.fence.item_id,
    )


def _recovery_result(
    outcome: ScheduledWecomRouteOutcome, recovery: PreparedRecovery,
) -> ScheduledWecomRouteResult:
    return ScheduledWecomRouteResult(
        outcome=outcome,
        intent_id=recovery.attempt.fence.intent_id,
        item_id=recovery.attempt.fence.item_id,
    )


def _smart_result(result: SmartRobotDispatchResult) -> ScheduledWecomRouteResult:
    outcome = (
        ScheduledWecomRouteOutcome.UNAVAILABLE
        if result.outcome is SmartRobotDispatchOutcome.UNAVAILABLE
        else ScheduledWecomRouteOutcome(result.outcome.value)
    )
    return ScheduledWecomRouteResult(
        outcome=outcome,
        intent_id=result.intent_id,
        item_id=result.item_id,
        dispatch_receipt=result.dispatch_receipt,
    )


def _app_result(result: AppDispatchResult) -> ScheduledWecomRouteResult:
    return ScheduledWecomRouteResult(
        outcome=ScheduledWecomRouteOutcome(result.outcome.value),
        intent_id=result.intent_id,
        item_id=result.item_id,
        dispatch_receipt=result.dispatch_receipt,
    )
