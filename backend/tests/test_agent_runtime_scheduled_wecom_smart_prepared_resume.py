"""Safe PREPARED-attempt resume coverage for Scheduled WeCom Smart Robot."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from services.agent.runtime.application.scheduled_wecom_receipt import (
    scheduled_wecom_smart_identity,
)
from services.agent.runtime.application.scheduled_wecom_smart_dispatch import (
    ScheduledWecomSmartDispatchService,
)
from services.agent.runtime.ports.scheduled_wecom_delivery import (
    AttemptOperationOutcome,
    AttemptStatus,
    DispatchAttempt,
    DispatchChannel,
    DispatchOutcome,
    DispatchPayloadVersions,
    PreparedRecovery,
    RecoveryOutcome,
)
from services.agent.runtime.ports.scheduled_wecom_smart_dispatch import (
    ScheduledWecomSmartDispatchError,
    SmartRobotDispatchOutcome,
)
from tests.test_agent_runtime_scheduled_wecom_smart_dispatch import (
    ATTEMPT,
    NOW,
    ORG,
    _claim,
    _GateTransport,
    _payload,
    _Repository,
    _Transport,
)


def _recovery(
    outcome: RecoveryOutcome = RecoveryOutcome.RECOVERED,
    *,
    attempt: DispatchAttempt | None = None,
) -> PreparedRecovery:
    payload = _payload()
    current_fence = replace(
        _claim().fence,
        delivery_state_version=payload.delivery_state_version + 2,
        item_state_version=payload.item_state_version + 1,
    )
    recovered_attempt = attempt or DispatchAttempt(
        outcome=AttemptOperationOutcome.READBACK,
        fence=current_fence,
        attempt_id=ATTEMPT,
        attempt_number=1,
        identity=scheduled_wecom_smart_identity(payload),
        payload_versions=DispatchPayloadVersions(
            delivery_state_version=payload.delivery_state_version,
            item_state_version=payload.item_state_version,
        ),
        status=AttemptStatus.PREPARED,
    )
    return PreparedRecovery(
        outcome=outcome,
        attempt=recovered_attempt,
        lease_expires_at=NOW,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", (RecoveryOutcome.RECOVERED, RecoveryOutcome.READBACK))
async def test_recovered_and_readback_resume_without_prepare(outcome) -> None:
    repository = _Repository()
    transport = _Transport()

    result = await ScheduledWecomSmartDispatchService(
        repository, transport,
    ).dispatch_recovered_prepared(_recovery(outcome), _payload())

    assert result.outcome is SmartRobotDispatchOutcome.ACCEPTED
    assert repository.prepare_calls == []
    assert repository.start_calls == len(transport.calls) == len(repository.outcome_calls) == 1


@pytest.mark.asyncio
async def test_50_recovered_calls_share_one_start_send_and_outcome() -> None:
    repository = _Repository()
    transport = _Transport(delay=0.02)
    service = ScheduledWecomSmartDispatchService(repository, transport)

    results = await asyncio.gather(*(
        service.dispatch_recovered_prepared(_recovery(), _payload())
        for _ in range(50)
    ))

    assert all(result.outcome is SmartRobotDispatchOutcome.ACCEPTED for result in results)
    assert repository.prepare_calls == []
    assert repository.start_calls == len(transport.calls) == len(repository.outcome_calls) == 1


@pytest.mark.asyncio
async def test_recovered_and_fresh_paths_share_the_identity_flight() -> None:
    repository = _Repository()
    transport = _GateTransport()
    service = ScheduledWecomSmartDispatchService(repository, transport)
    recovered = asyncio.create_task(
        service.dispatch_recovered_prepared(_recovery(), _payload()),
    )
    await transport.started.wait()
    fresh = asyncio.create_task(service.dispatch_claimed(_claim(), _payload()))

    transport.release.set()
    recovered_result, fresh_result = await asyncio.gather(recovered, fresh)

    assert recovered_result == fresh_result
    assert repository.prepare_calls == []
    assert repository.start_calls == len(transport.calls) == len(repository.outcome_calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "drift",
    (
        "recovery_outcome", "attempt_outcome", "attempt_status", "fence",
        "channel", "payload", "provider_revision", "idempotency", "provider_request",
    ),
)
async def test_recovery_drift_fails_before_resolve_or_start(drift: str) -> None:
    payload = _payload()
    recovery = _recovery()
    attempt = recovery.attempt
    if drift == "recovery_outcome":
        recovery = replace(recovery, outcome=RecoveryOutcome.FENCED)
    elif drift == "attempt_outcome":
        recovery = replace(
            recovery, attempt=replace(attempt, outcome=AttemptOperationOutcome.PREPARED),
        )
    elif drift == "attempt_status":
        recovery = replace(
            recovery, attempt=replace(attempt, status=AttemptStatus.DISPATCH_STARTED),
        )
    elif drift == "fence":
        recovery = replace(
            recovery,
            attempt=replace(
                attempt,
                fence=replace(
                    attempt.fence,
                    item_id="99999999-9999-9999-9999-999999999999",
                ),
            ),
        )
    elif drift == "channel":
        payload = _payload(DispatchChannel.APP)
    elif drift == "payload":
        payload = replace(payload, payload_hash="0" * 64)
    elif drift == "provider_revision":
        recovery = replace(
            recovery,
            attempt=replace(
                attempt,
                identity=replace(attempt.identity, provider_revision=99),
            ),
        )
    elif drift == "idempotency":
        recovery = replace(
            recovery,
            attempt=replace(
                attempt,
                identity=replace(attempt.identity, idempotency_key="0" * 64),
            ),
        )
    else:
        recovery = replace(
            recovery,
            attempt=replace(
                attempt,
                identity=replace(attempt.identity, provider_request_id="drift"),
            ),
        )
    repository = _Repository()
    transport = _Transport()

    with pytest.raises(ScheduledWecomSmartDispatchError):
        await ScheduledWecomSmartDispatchService(
            repository, transport,
        ).dispatch_recovered_prepared(recovery, payload)

    assert repository.prepare_calls == []
    assert repository.start_calls == 0
    assert transport.resolve_calls == []
    assert transport.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "transport",
    (
        _Transport(is_connected=False),
        _Transport(org_id="88888888-8888-8888-8888-888888888888"),
    ),
)
async def test_unavailable_transport_has_no_persistence(transport) -> None:
    repository = _Repository()

    result = await ScheduledWecomSmartDispatchService(
        repository, transport,
    ).dispatch_recovered_prepared(_recovery(), _payload())

    assert result.outcome is SmartRobotDispatchOutcome.UNAVAILABLE
    assert repository.prepare_calls == []
    assert repository.start_calls == 0
    assert transport.calls == []


@pytest.mark.asyncio
async def test_resolver_cancellation_propagates_without_persistence() -> None:
    class _CancelledResolver:
        async def resolve_smart_transport(self, org_id: str) -> None:
            raise asyncio.CancelledError

    repository = _Repository()
    with pytest.raises(asyncio.CancelledError):
        await ScheduledWecomSmartDispatchService(
            repository, _CancelledResolver(),
        ).dispatch_recovered_prepared(_recovery(), _payload())

    assert repository.prepare_calls == []
    assert repository.start_calls == 0


@pytest.mark.asyncio
async def test_start_readback_never_sends() -> None:
    repository = _Repository(start_readback=True)
    transport = _Transport()

    result = await ScheduledWecomSmartDispatchService(
        repository, transport,
    ).dispatch_recovered_prepared(_recovery(), _payload())

    assert result.outcome is SmartRobotDispatchOutcome.ALREADY_PERSISTED
    assert repository.prepare_calls == []
    assert repository.start_calls == 1
    assert transport.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("error", (RuntimeError("provider failed"), asyncio.CancelledError()))
async def test_post_start_transport_failure_preserves_unknown_contract(error) -> None:
    repository = _Repository()
    service = ScheduledWecomSmartDispatchService(
        repository, _Transport(error=error),
    )

    if isinstance(error, asyncio.CancelledError):
        with pytest.raises(asyncio.CancelledError):
            await service.dispatch_recovered_prepared(_recovery(), _payload())
    else:
        result = await service.dispatch_recovered_prepared(_recovery(), _payload())
        assert result.outcome is SmartRobotDispatchOutcome.UNKNOWN

    assert repository.prepare_calls == []
    assert repository.start_calls == 1
    assert repository.outcome_calls[0][1:] == (DispatchOutcome.UNKNOWN, None)
