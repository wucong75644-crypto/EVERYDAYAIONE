"""Runtime-owned scheduled finalizer planning and lifecycle contracts."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from services.agent.runtime.application.scheduled_finalizer import (
    ScheduledRuntimeFinalizer,
)
from services.agent.runtime.composition import RuntimeOwner
from services.agent.runtime.ports.scheduled_finalization import (
    ScheduledFinalizationClaim,
    ScheduledFinalizationContext,
    ScheduledFinalizationOutcome,
    ScheduledFinalizationReceipt,
    ScheduledTerminalStatus,
)


RUN_ID = "11111111-1111-1111-1111-111111111111"
TASK_ID = "22222222-2222-2222-2222-222222222222"
TOKEN = "33333333-3333-3333-3333-333333333333"
BASELINE = datetime(2026, 8, 10, 1, 2, tzinfo=timezone.utc)


def _claim() -> ScheduledFinalizationClaim:
    return ScheduledFinalizationClaim(
        scheduled_run_id=RUN_ID,
        claim_token=TOKEN,
        intent_state_version=4,
        claim_lease_expires_at=datetime(
            2026, 8, 10, 2, tzinfo=timezone.utc,
        ),
    )


def _context(
    status: ScheduledTerminalStatus = ScheduledTerminalStatus.COMPLETED,
) -> ScheduledFinalizationContext:
    return ScheduledFinalizationContext(
        scheduled_run_id=RUN_ID,
        terminal_status=status,
        terminal_baseline=BASELINE,
        intent_state_version=4,
        task_state_version=7,
        schedule_hash="a" * 64,
        schedule_type="daily",
        cron_expr="0 9 * * *",
        timezone="Asia/Shanghai",
        retry_count=1,
        consecutive_failures=0,
    )


class _Repository:
    def __init__(self, context=None, *, claim=True, error=None) -> None:
        self.claim = _claim() if claim else None
        self.context = context or _context()
        self.error = error
        self.projections = []

    async def claim_next(self, worker_id, *, lease_seconds=90):
        assert worker_id == "worker-1" and lease_seconds == 90
        return self.claim

    async def read_context(self, claim):
        assert claim == self.claim
        return self.context

    async def apply(self, claim, context, projection):
        self.projections.append(projection)
        if self.error:
            raise self.error
        return ScheduledFinalizationReceipt(
            outcome=ScheduledFinalizationOutcome.APPLIED,
            scheduled_run_id=claim.scheduled_run_id,
            scheduled_task_id=TASK_ID,
            terminal_status=context.terminal_status,
            scheduled_run_status="success",
            task_status="active",
            task_state_version=8,
        )


@pytest.mark.asyncio
async def test_no_intent_is_a_fast_noop() -> None:
    assert not await ScheduledRuntimeFinalizer(
        _Repository(claim=False), "worker-1",
    ).run_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("context", "expected"),
    (
        (_context(), datetime(2026, 8, 10, 1, tzinfo=timezone.utc)  # next day
         .replace(day=11)),
        (replace(_context(), schedule_type="once", cron_expr=None), None),
        (replace(
            _context(ScheduledTerminalStatus.CANCELLED),
            schedule_type="once", cron_expr=None,
        ), None),
        (_context(ScheduledTerminalStatus.FAILED),
         datetime(2026, 8, 10, 1, 7, tzinfo=timezone.utc)),
        (replace(
            _context(ScheduledTerminalStatus.FAILED),
            consecutive_failures=1,
        ), datetime(2026, 8, 11, 1, tzinfo=timezone.utc)),
        (replace(
            _context(ScheduledTerminalStatus.FAILED),
            consecutive_failures=2,
        ), None),
        (_context(ScheduledTerminalStatus.CANCELLED),
         datetime(2026, 8, 11, 1, tzinfo=timezone.utc)),
    ),
)
async def test_terminal_projection_uses_frozen_baseline(context, expected) -> None:
    repository = _Repository(context)
    assert await ScheduledRuntimeFinalizer(repository, "worker-1").run_once()
    assert repository.projections[0].next_run_at == expected


@pytest.mark.asyncio
async def test_request_id_is_deterministic_for_same_claim() -> None:
    first = _Repository()
    second = _Repository()
    await ScheduledRuntimeFinalizer(first, "worker-1").run_once()
    await ScheduledRuntimeFinalizer(second, "worker-1").run_once()
    assert first.projections[0].request_id == second.projections[0].request_id


@pytest.mark.asyncio
async def test_owner_orders_finalizer_last_and_processes_at_most_one() -> None:
    events = []

    class Commands:
        async def run_once(self): events.append("command"); return False
        def stop(self): events.append("command_stop")

    class Runtime:
        async def run_once(self): events.append("run"); return False
        async def action_once(self): events.append("action"); return False
        async def child_cancel_once(self): events.append("child"); return False
        async def reconciliation_once(self): events.append("reconcile"); return False
        def stop(self): events.append("runtime_stop")

    class Finalizer:
        async def run_once(self): events.append("finalize"); return True

    owner = RuntimeOwner(Commands(), Runtime(), finalizer=Finalizer())
    assert await owner.run_once()
    assert events == ["command", "run", "action", "child", "reconcile", "finalize"]


@pytest.mark.asyncio
async def test_finalizer_failure_is_observable_but_does_not_break_owner(
    caplog,
) -> None:
    class Loop:
        async def run_once(self): return False
        async def action_once(self): return False
        async def child_cancel_once(self): return False
        async def reconciliation_once(self): return False
        def stop(self): pass

    class Finalizer:
        async def run_once(self): raise RuntimeError("must-not-be-logged")

    owner = RuntimeOwner(Loop(), Loop(), finalizer=Finalizer())
    assert not await owner.run_once()
    assert "RuntimeError" in caplog.text
    assert "must-not-be-logged" not in caplog.text


@pytest.mark.asyncio
async def test_drain_prevents_new_finalization_claim() -> None:
    class Loop:
        async def run_once(self): raise AssertionError("must not claim")
        def stop(self): pass

    owner = RuntimeOwner(Loop(), Loop(), finalizer=Loop())
    owner.drain()
    assert not await owner.run_once()


def test_finalizer_has_no_legacy_or_external_side_effect_dependencies() -> None:
    runtime_root = Path(__file__).parents[1] / "services" / "agent" / "runtime"
    source = "\n".join((runtime_root / relative).read_text() for relative in (
        "application/scheduled_finalizer.py",
        "infrastructure/postgres/scheduled_finalization_repository.py",
        "ports/scheduled_finalization.py",
    ))
    for forbidden in (
        "ScheduledTaskExecutor", "MessageGateway", "websocket", "wecom",
        "redis", "get_oss_service", "credit_lock", "ProviderTransport",
    ):
        assert forbidden not in source
