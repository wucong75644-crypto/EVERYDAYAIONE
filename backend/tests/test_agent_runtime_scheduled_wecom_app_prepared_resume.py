"""Safe PREPARED-attempt resume coverage for Scheduled WeCom App."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from services.agent.runtime.application.scheduled_wecom_app_dispatch import (
    ScheduledWecomAppDispatchService,
)
from services.agent.runtime.application.scheduled_wecom_receipt import (
    scheduled_wecom_app_identity,
)
from services.agent.runtime.ports.scheduled_wecom_app_dispatch import (
    AppDispatchOutcome,
    ScheduledWecomAppDispatchError,
)
from services.agent.runtime.ports.scheduled_wecom_delivery import (
    AttemptOperationOutcome,
    AttemptStatus,
    DispatchAttempt,
    DispatchChannel,
    DispatchOutcome,
    PreparedRecovery,
    RecoveryOutcome,
)
from tests.test_agent_runtime_scheduled_wecom_app_dispatch import (
    ATTEMPT,
    NOW,
    _binding,
    _claim,
    _payload,
    _Repository,
    _Transport,
)
from tests.test_agent_runtime_scheduled_wecom_app_dispatch_concurrency import (
    _GateTransport,
)


def _recovery(
    outcome: RecoveryOutcome = RecoveryOutcome.RECOVERED,
    *,
    attempt: DispatchAttempt | None = None,
) -> PreparedRecovery:
    payload = _payload()
    recovered_attempt = attempt or DispatchAttempt(
        outcome=AttemptOperationOutcome.READBACK,
        fence=_claim().fence,
        attempt_id=ATTEMPT,
        attempt_number=1,
        identity=scheduled_wecom_app_identity(
            payload,
            org_id=payload.target.org_id,
            corp_id=payload.target.corp_id,
            agent_id=1000006,
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

    result = await ScheduledWecomAppDispatchService(
        repository,
    ).dispatch_recovered_prepared(_recovery(outcome), _payload(), _binding(transport))

    assert result.outcome is AppDispatchOutcome.ACCEPTED
    assert repository.prepare_calls == []
    assert repository.start_calls == len(transport.calls) == len(repository.outcome_calls) == 1


@pytest.mark.asyncio
async def test_50_recovered_calls_share_one_start_send_and_outcome() -> None:
    repository = _Repository()
    transport = _Transport(delay=0.02)
    binding = _binding(transport)
    service = ScheduledWecomAppDispatchService(repository)

    results = await asyncio.gather(*(
        service.dispatch_recovered_prepared(_recovery(), _payload(), binding)
        for _ in range(50)
    ))

    assert all(result.outcome is AppDispatchOutcome.ACCEPTED for result in results)
    assert repository.prepare_calls == []
    assert repository.start_calls == len(transport.calls) == len(repository.outcome_calls) == 1


@pytest.mark.asyncio
async def test_recovered_and_fresh_paths_share_the_identity_flight() -> None:
    repository = _Repository()
    transport = _GateTransport()
    binding = _binding(transport)
    service = ScheduledWecomAppDispatchService(repository)
    recovered = asyncio.create_task(
        service.dispatch_recovered_prepared(_recovery(), _payload(), binding),
    )
    await transport.started.wait()
    fresh = asyncio.create_task(
        service.dispatch_claimed(_claim(), _payload(), binding),
    )

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
        "binding_org", "binding_corp", "binding_agent",
    ),
)
async def test_recovery_or_binding_drift_fails_before_start(drift: str) -> None:
    payload = _payload()
    recovery = _recovery()
    attempt = recovery.attempt
    binding_changes: dict[str, object] = {}
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
                fence=replace(attempt.fence, delivery_state_version=99),
            ),
        )
    elif drift == "channel":
        payload = _payload(DispatchChannel.SMART_ROBOT)
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
    elif drift == "provider_request":
        recovery = replace(
            recovery,
            attempt=replace(
                attempt,
                identity=replace(attempt.identity, provider_request_id="drift"),
            ),
        )
    elif drift == "binding_org":
        binding_changes["org_id"] = "88888888-8888-8888-8888-888888888888"
    elif drift == "binding_corp":
        binding_changes["corp_id"] = "other-corp"
    else:
        binding_changes["agent_id"] = 1000007
    repository = _Repository()
    transport = _Transport()

    with pytest.raises(ScheduledWecomAppDispatchError):
        await ScheduledWecomAppDispatchService(
            repository,
        ).dispatch_recovered_prepared(
            recovery, payload, _binding(transport, **binding_changes),
        )

    assert repository.prepare_calls == []
    assert repository.start_calls == 0
    assert transport.calls == []


@pytest.mark.asyncio
async def test_start_readback_never_sends() -> None:
    repository = _Repository(start_readback=True)
    transport = _Transport()

    result = await ScheduledWecomAppDispatchService(
        repository,
    ).dispatch_recovered_prepared(_recovery(), _payload(), _binding(transport))

    assert result.outcome is AppDispatchOutcome.ALREADY_PERSISTED
    assert repository.prepare_calls == []
    assert repository.start_calls == 1
    assert transport.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("error", (RuntimeError("provider failed"), asyncio.CancelledError()))
async def test_post_start_transport_failure_preserves_unknown_contract(error) -> None:
    repository = _Repository()
    transport = _Transport(error=error)
    service = ScheduledWecomAppDispatchService(repository)

    if isinstance(error, asyncio.CancelledError):
        with pytest.raises(asyncio.CancelledError):
            await service.dispatch_recovered_prepared(
                _recovery(), _payload(), _binding(transport),
            )
    else:
        result = await service.dispatch_recovered_prepared(
            _recovery(), _payload(), _binding(transport),
        )
        assert result.outcome is AppDispatchOutcome.UNKNOWN

    assert repository.prepare_calls == []
    assert repository.start_calls == 1
    assert repository.outcome_calls[0][1:] == (DispatchOutcome.UNKNOWN, None)
