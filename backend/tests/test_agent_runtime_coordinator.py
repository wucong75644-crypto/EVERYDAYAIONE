"""Application-level behavior for the PostgreSQL-first Coordinator."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from services.agent.runtime.application.coordinator import (
    CommandClaimLeaseLost,
    RuntimeCoordinator,
)
from services.agent.runtime.domain import FencingToken, RunId, SessionId
from services.agent.runtime.ports.command_claim import (
    CommandClaim,
    CommandClaimOutcome,
    CommandClaimReceipt,
)


CLAIM = CommandClaim(
    command_id="11111111-1111-1111-1111-111111111111",
    session_id=SessionId("22222222-2222-2222-2222-222222222222"),
    run_id=RunId("33333333-3333-3333-3333-333333333333"),
    worker_id="worker-1",
    fencing_token=FencingToken("44444444-4444-4444-4444-444444444444"),
    lease_expires_at=datetime(2026, 7, 27, tzinfo=timezone.utc),
    attempt_number=1,
    command_type="submit_input",
)


class _Repository:
    def __init__(self, receipts: list[CommandClaimReceipt]) -> None:
        self.receipts = receipts
        self.finished: list[CommandClaimOutcome] = []

    async def claim_next(self, *_args: object, **_kwargs: object):
        return self.receipts.pop(0)

    async def renew(self, *_args: object, **_kwargs: object):
        return CommandClaimReceipt(CommandClaimOutcome.RENEWED)

    async def finish(
        self, _claim: CommandClaim, outcome: CommandClaimOutcome,
        **_kwargs: object,
    ):
        self.finished.append(outcome)
        return CommandClaimReceipt(outcome)


@pytest.mark.asyncio
async def test_run_once_finishes_claimed_command() -> None:
    repository = _Repository([
        CommandClaimReceipt(CommandClaimOutcome.CLAIMED, CLAIM),
    ])

    coordinator = RuntimeCoordinator(
        repository, "worker-1",
        lambda _claim: _completed(CommandClaimOutcome.COMPLETED),
    )

    assert await coordinator.run_once() is True
    assert repository.finished == [CommandClaimOutcome.COMPLETED]


@pytest.mark.asyncio
async def test_empty_postgres_scan_is_not_work() -> None:
    repository = _Repository([
        CommandClaimReceipt(CommandClaimOutcome.NOT_FOUND),
    ])
    coordinator = RuntimeCoordinator(
        repository, "worker-1",
        lambda _claim: _completed(CommandClaimOutcome.COMPLETED),
    )

    assert await coordinator.run_once() is False


@pytest.mark.asyncio
async def test_already_processed_command_does_not_call_handler() -> None:
    repository = _Repository([
        CommandClaimReceipt(CommandClaimOutcome.ALREADY_PROCESSED),
    ])
    called = False

    async def handler(_claim: CommandClaim) -> CommandClaimOutcome:
        nonlocal called
        called = True
        return CommandClaimOutcome.COMPLETED

    coordinator = RuntimeCoordinator(repository, "worker-1", handler)

    assert await coordinator.run_once() is True
    assert called is False
    assert repository.finished == []


@pytest.mark.asyncio
async def test_redis_wakeup_failure_does_not_block_postgres_polling() -> None:
    repository = _Repository([
        CommandClaimReceipt(CommandClaimOutcome.NOT_FOUND),
    ])

    async def unavailable(_timeout: float) -> None:
        raise ConnectionError("redis unavailable")

    coordinator = RuntimeCoordinator(
        repository, "worker-1",
        lambda _claim: _completed(CommandClaimOutcome.COMPLETED),
        poll_interval=0.001, wakeup_waiter=unavailable,
    )

    assert await coordinator.run_once() is False
    await coordinator._wait_for_work()


@pytest.mark.asyncio
async def test_lease_loss_cancels_handler_without_terminal_finish() -> None:
    class _LeaseLostRepository(_Repository):
        async def renew(self, *_args: object, **_kwargs: object):
            raise RuntimeError("ownership_lost")

    repository = _LeaseLostRepository([
        CommandClaimReceipt(CommandClaimOutcome.CLAIMED, CLAIM),
    ])

    async def blocked(_claim: CommandClaim) -> CommandClaimOutcome:
        await _never()
        return CommandClaimOutcome.COMPLETED

    coordinator = RuntimeCoordinator(
        repository, "worker-1", blocked, renew_interval=0.001,
    )

    with pytest.raises(CommandClaimLeaseLost):
        await coordinator.run_once()
    assert repository.finished == []


async def _completed(
    outcome: CommandClaimOutcome,
) -> CommandClaimOutcome:
    return outcome


async def _never() -> None:
    import asyncio

    await asyncio.Event().wait()
