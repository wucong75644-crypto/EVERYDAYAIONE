from __future__ import annotations

from datetime import datetime, timezone
import pytest

from services.agent.runtime.application.projection_worker import (
    CompatibilityProjectionWorker,
)
from services.agent.runtime.domain import (
    EventDurability,
    EventSequence,
    RuntimeActorType,
    RuntimeEvent,
    RuntimeScope,
    ScopeKind,
)
from services.agent.runtime.ports.projection import ProjectionClaim


def _claim(event_type: str = "run.created") -> ProjectionClaim:
    event = RuntimeEvent(
        event_id="event", session_id="session",
        scope=RuntimeScope(
            kind=ScopeKind.USER, scope_id="user", user_id="user", org_id=None,
        ),
        event_type=event_type, event_version=1,
        durability=EventDurability.DURABLE, correlation_id="correlation",
        actor_type=RuntimeActorType.SYSTEM, payload_hash="hash",
        occurred_at=datetime.now(timezone.utc), redaction_revision="v1",
        sequence=EventSequence(1),
    )
    return ProjectionClaim(
        outbox_id="outbox", projection_kind="web_runtime",
        lease_token="lease", lease_expires_at=datetime.now(timezone.utc),
        attempt_count=1, checkpoint={}, event=event,
    )


class _Projection:
    def __init__(self, *, apply_error: Exception | None = None,
                 readback: object | None = None) -> None:
        self.apply_error = apply_error
        self.readback_result = readback
        self.applied: list[str] = []
        self.failed: list[str] = []

    async def claim(self, batch_size: int = 50):
        return (_claim(),)

    async def apply(self, claim, action: str):
        self.applied.append(action)
        if self.apply_error:
            raise self.apply_error
        return {"outcome": "applied"}

    async def readback(self, claim):
        return self.readback_result

    async def fail(self, claim, error_code: str):
        self.failed.append(error_code)


@pytest.mark.asyncio
async def test_worker_applies_server_checked_action() -> None:
    projection = _Projection()

    count = await CompatibilityProjectionWorker(projection).run_once()

    assert count == 1
    assert projection.applied == ["run_pending"]
    assert projection.failed == []


@pytest.mark.asyncio
async def test_response_loss_uses_durable_readback_without_retry() -> None:
    projection = _Projection(
        apply_error=ConnectionError("response lost"),
        readback={"outcome": "found"},
    )

    await CompatibilityProjectionWorker(projection).run_once()

    assert projection.failed == []


@pytest.mark.asyncio
async def test_uncommitted_failure_releases_outbox_for_retry() -> None:
    projection = _Projection(apply_error=RuntimeError("rollback"))

    await CompatibilityProjectionWorker(projection).run_once()

    assert projection.failed == ["apply_runtimeerror"]
