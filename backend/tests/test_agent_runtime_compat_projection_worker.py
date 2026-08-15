from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import pytest

from services.agent.runtime.application.projection_worker import (
    CompatibilityProjectionNotifier, CompatibilityProjectionWorker,
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


class _Query:
    def __init__(self, data):
        self.data = data

    def select(self, _fields):
        return self

    def eq(self, _field, _value):
        return self

    def maybe_single(self):
        return self

    async def execute(self):
        return SimpleNamespace(data=self.data)


class _Database:
    def __init__(self, rows):
        self.rows = rows

    def rpc(self, _name, _params):
        return _Query(self.rows)


class _Websocket:
    def __init__(self):
        self.calls = []

    async def send_to_task_or_user(self, **kwargs):
        self.calls.append(kwargs)


@pytest.mark.asyncio
async def test_notifier_publishes_failed_terminal_message() -> None:
    websocket = _Websocket()
    notifier = CompatibilityProjectionNotifier(
        _Database({
            "outcome": "found",
            "task": {
                "id": "task", "client_task_id": None,
                "external_task_id": "external-task", "user_id": "user",
                "org_id": None, "conversation_id": "conversation",
                "status": "failed", "error_message": "provider denied",
            },
            "message": {"id": "message"},
        }),
        websocket,
    )

    await notifier.notify(_claim("run.failed"), {
        "outcome": "applied",
        "result": {
            "projection_action": "run_failed",
            "task_id": "task", "message_id": "message",
        },
    })

    assert len(websocket.calls) == 1
    event = websocket.calls[0]["message"]
    assert event["type"] == "message_error"
    assert event["task_id"] == "external-task"
    assert event["message_id"] == "message"
