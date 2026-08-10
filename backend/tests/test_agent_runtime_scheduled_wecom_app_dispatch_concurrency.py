"""Concurrency coverage for Scheduled WeCom App orchestration."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from services.agent.runtime.application.scheduled_wecom_app_dispatch import (
    ScheduledWecomAppDispatchService,
)
from services.agent.runtime.ports.scheduled_wecom_app_dispatch import AppDispatchOutcome
from services.agent.runtime.ports.scheduled_wecom_delivery import (
    AttemptOperationOutcome,
    AttemptStatus,
    DeliveryClaim,
    DispatchAttempt,
    ProviderDispatchIdentity,
)
from services.wecom.app_outbound import (
    WecomAppOutboundReceipt,
    WecomAppOutboundStatus,
)
from tests.test_agent_runtime_scheduled_wecom_app_dispatch import (
    ATTEMPT,
    _binding,
    _claim,
    _payload,
    _Repository,
    _Transport,
)


class _GateTransport(_Transport):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def send_typed(
        self, *, provider_request_id: str, target: str, payload: object,
    ) -> WecomAppOutboundReceipt:
        self.calls.append((provider_request_id, target, payload))
        self.started.set()
        await self.release.wait()
        return WecomAppOutboundReceipt(
            provider_request_id,
            WecomAppOutboundStatus.ACKNOWLEDGED,
            0,
            "msg-001",
            None,
        )


class _ReversedPrepareRepository(_Repository):
    def __init__(self) -> None:
        super().__init__()
        self.fresh_started = asyncio.Event()
        self.release_fresh = asyncio.Event()

    async def prepare_dispatch(
        self, claim: DeliveryClaim, identity: ProviderDispatchIdentity,
    ) -> DispatchAttempt:
        self.prepare_calls.append(identity)
        if self.attempt is None:
            self.attempt = DispatchAttempt(
                outcome=AttemptOperationOutcome.PREPARED,
                fence=claim.fence,
                attempt_id=ATTEMPT,
                attempt_number=1,
                identity=identity,
                status=AttemptStatus.PREPARED,
            )
            self.fresh_started.set()
            await self.release_fresh.wait()
            return self.attempt
        return replace(self.attempt, outcome=AttemptOperationOutcome.READBACK)


@pytest.mark.asyncio
async def test_owner_caller_cancellation_keeps_shared_flight() -> None:
    repository = _Repository()
    transport = _GateTransport()
    service = ScheduledWecomAppDispatchService(repository)
    owner = asyncio.create_task(
        service.dispatch_claimed(_claim(), _payload(), _binding(transport)),
    )
    await transport.started.wait()
    duplicate = asyncio.create_task(
        service.dispatch_claimed(_claim(), _payload(), _binding(transport)),
    )
    owner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await owner
    transport.release.set()
    result = await duplicate

    assert result.outcome is AppDispatchOutcome.ACCEPTED
    assert len(repository.prepare_calls) == 1
    assert repository.start_calls == len(transport.calls) == len(repository.outcome_calls) == 1


@pytest.mark.asyncio
async def test_50_same_service_calls_use_one_http_transport() -> None:
    repository = _Repository()
    transport = _Transport(delay=0.02)
    service = ScheduledWecomAppDispatchService(repository)
    results = await asyncio.gather(*(
        service.dispatch_claimed(_claim(), _payload(), _binding(transport))
        for _ in range(50)
    ))

    assert all(result.outcome is AppDispatchOutcome.ACCEPTED for result in results)
    assert len(repository.prepare_calls) == 1
    assert repository.start_calls == len(transport.calls) == len(repository.outcome_calls) == 1


@pytest.mark.asyncio
async def test_reversed_prepare_readback_order_keeps_one_shared_owner() -> None:
    repository = _ReversedPrepareRepository()
    transport = _Transport()
    service = ScheduledWecomAppDispatchService(repository)
    callers = [
        asyncio.create_task(
            service.dispatch_claimed(_claim(), _payload(), _binding(transport)),
        )
        for _ in range(50)
    ]
    await repository.fresh_started.wait()
    readback = await repository.prepare_dispatch(_claim(), repository.prepare_calls[0])
    assert readback.outcome is AttemptOperationOutcome.READBACK
    repository.release_fresh.set()
    results = await asyncio.gather(*callers)

    assert all(result == results[0] for result in results)
    assert results[0].outcome is AppDispatchOutcome.ACCEPTED
    assert len(repository.prepare_calls) == 2
    assert repository.start_calls == len(transport.calls) == len(repository.outcome_calls) == 1
