import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.agent.runtime.application.scheduled_delivery_projection import (
    ScheduledDeliveryProjectionWorker,
    _wakeup_payload,
)
from services.agent.runtime.infrastructure.postgres.scheduled_delivery_projection import (
    PostgresScheduledDeliveryProjection,
    ScheduledWebProjectionClaim,
)
from core.db_scope import DatabaseAccessKind, DatabaseScope


def _claim(**changes) -> ScheduledWebProjectionClaim:
    claim = ScheduledWebProjectionClaim(
        intent_id="11111111-1111-1111-1111-111111111111",
        scheduled_run_id="22222222-2222-2222-2222-222222222222",
        runtime_run_id="33333333-3333-3333-3333-333333333333",
        task_id="44444444-4444-4444-4444-444444444444",
        org_id="55555555-5555-5555-5555-555555555555",
        user_id="66666666-6666-6666-6666-666666666666",
        target_hash="a" * 64, content_identity_hash="b" * 64,
        terminal_status="completed", scheduled_run_status="success",
        task_status="paused", summary="safe summary", reason_code=None,
        next_run_at=None, consecutive_failures=0,
        claim_token="77777777-7777-7777-7777-777777777777",
        state_version=1, projected=False,
    )
    return replace(claim, **changes)


@pytest.mark.asyncio
async def test_projection_is_durable_before_successful_wakeup() -> None:
    claim = _claim()
    projected = replace(claim, projected=True)
    projection = MagicMock(
        claim=AsyncMock(return_value=claim), apply=AsyncMock(return_value=projected),
        complete_wakeup=AsyncMock(return_value={"outcome": "completed"}),
    )
    websocket = MagicMock(send_to_user=AsyncMock(return_value=True))

    assert await ScheduledDeliveryProjectionWorker(projection, websocket).run_once()

    projection.apply.assert_awaited_once_with(claim)
    websocket.send_to_user.assert_awaited_once_with(
        claim.user_id, {
            "type": "scheduled_task_completed",
            "data": {
                "task_id": claim.task_id, "run_id": claim.scheduled_run_id,
                "status": "success", "task_status": "paused",
                "next_run_at": None, "summary": "safe summary",
            },
        }, org_id=claim.org_id,
    )
    projection.complete_wakeup.assert_awaited_once_with(
        projected, delivered=True, error_code=None,
    )


@pytest.mark.asyncio
async def test_false_send_result_is_recorded_as_unavailable() -> None:
    projected = _claim(projected=True)
    projection = MagicMock(
        claim=AsyncMock(return_value=projected), apply=AsyncMock(return_value=projected),
        complete_wakeup=AsyncMock(return_value={"outcome": "completed"}),
    )
    websocket = MagicMock(send_to_user=AsyncMock(return_value=False))

    assert await ScheduledDeliveryProjectionWorker(projection, websocket).run_once()
    projection.complete_wakeup.assert_awaited_once_with(
        projected, delivered=False, error_code="ws_not_connected",
    )


@pytest.mark.asyncio
async def test_websocket_exception_does_not_undo_durable_projection() -> None:
    projected = _claim(projected=True)
    projection = MagicMock(
        claim=AsyncMock(return_value=projected), apply=AsyncMock(return_value=projected),
        complete_wakeup=AsyncMock(return_value={"outcome": "completed"}),
    )
    websocket = MagicMock(send_to_user=AsyncMock(side_effect=RuntimeError("secret")))

    assert await ScheduledDeliveryProjectionWorker(projection, websocket).run_once()
    projection.complete_wakeup.assert_awaited_once_with(
        projected, delivered=False, error_code="ws_runtimeerror",
    )


@pytest.mark.asyncio
async def test_crash_before_send_leaves_wakeup_unattempted() -> None:
    projection = MagicMock(
        claim=AsyncMock(return_value=_claim()),
        apply=AsyncMock(side_effect=RuntimeError("db unavailable")),
        complete_wakeup=AsyncMock(),
    )
    websocket = MagicMock(send_to_user=AsyncMock())

    with pytest.raises(RuntimeError, match="db unavailable"):
        await ScheduledDeliveryProjectionWorker(projection, websocket).run_once()
    websocket.send_to_user.assert_not_awaited()
    projection.complete_wakeup.assert_not_awaited()


@pytest.mark.asyncio
async def test_crash_after_send_before_completion_allows_duplicate_recovery() -> None:
    projected = _claim(projected=True)
    projection = MagicMock(
        claim=AsyncMock(return_value=projected), apply=AsyncMock(return_value=projected),
        complete_wakeup=AsyncMock(),
    )
    websocket = MagicMock(send_to_user=AsyncMock(side_effect=asyncio.CancelledError()))

    with pytest.raises(asyncio.CancelledError):
        await ScheduledDeliveryProjectionWorker(projection, websocket).run_once()
    projection.complete_wakeup.assert_not_awaited()


def test_failed_payload_contains_only_safe_refresh_fields() -> None:
    payload = _wakeup_payload(_claim(
        terminal_status="failed", scheduled_run_status="failed", task_status="error",
        summary=None, reason_code="redacted_terminal_reason", consecutive_failures=3,
    ))

    assert payload == {
        "type": "scheduled_task_failed",
        "data": {
            "task_id": "44444444-4444-4444-4444-444444444444",
            "run_id": "22222222-2222-2222-2222-222222222222",
            "status": "failed", "task_status": "error", "next_run_at": None,
            "reason": "redacted_terminal_reason", "consecutive_failures": 3,
        },
    }
    assert not ({"content", "token", "secret", "path", "provider"} & payload["data"].keys())


class _Response:
    def __init__(self, data):
        self.data = data


class _RpcCall:
    def __init__(self, response):
        self.response = response

    async def execute(self):
        if isinstance(self.response, Exception):
            raise self.response
        return _Response(self.response)


class _Database:
    def __init__(self, responses):
        self.scope = DatabaseScope(
            actor_user_id=None, org_id=None,
            access_kind=DatabaseAccessKind.PROJECTION, request_id="test",
        )
        self.responses = responses
        self.calls = []

    def rpc(self, name, params):
        self.calls.append((name, params))
        return _RpcCall(self.responses[name])


def _row(projected_at=None):
    claim = _claim(projected=projected_at is not None)
    return {
        "outcome": "claimed", **claim.__dict__, "projected_at": projected_at,
    }


@pytest.mark.asyncio
async def test_claim_recovers_committed_response_loss_from_request_readback() -> None:
    database = _Database({
        "claim_agent_runtime_scheduled_web_projection_v1": RuntimeError("response lost"),
        "read_agent_runtime_scheduled_web_projection_claim_v1": _row(),
    })

    claim = await PostgresScheduledDeliveryProjection(database, "worker").claim()

    assert claim is not None and claim.intent_id == _claim().intent_id
    assert [name for name, _ in database.calls] == [
        "claim_agent_runtime_scheduled_web_projection_v1",
        "read_agent_runtime_scheduled_web_projection_claim_v1",
    ]
    assert database.calls[0][1]["p_request_id"] == database.calls[1][1]["p_request_id"]


@pytest.mark.asyncio
async def test_apply_recovers_committed_response_loss_from_durable_receipt() -> None:
    row = _row("2026-08-10T10:00:00+00:00")
    row.update(outcome="projected", projection_receipt_hash="c" * 64)
    database = _Database({
        "apply_agent_runtime_scheduled_web_projection_v1": RuntimeError("response lost"),
        "get_agent_runtime_scheduled_web_projection_v1": row,
    })

    projected = await PostgresScheduledDeliveryProjection(
        database, "worker",
    ).apply(_claim())

    assert projected.projected is True
    assert [name for name, _ in database.calls] == [
        "apply_agent_runtime_scheduled_web_projection_v1",
        "get_agent_runtime_scheduled_web_projection_v1",
    ]


@pytest.mark.asyncio
async def test_projection_owner_preserves_worker_order_and_regression() -> None:
    from services.agent.runtime.composition import ProjectionOwner

    calls = []

    def worker(name, result):
        async def run_once():
            calls.append(name)
            return result
        return MagicMock(run_once=run_once)

    owner = ProjectionOwner(
        worker("compatibility", 1), worker("confirmation", False),
        worker("scheduled", True),
    )
    assert await owner.run_once() is True
    assert calls == ["compatibility", "scheduled", "confirmation"]

    calls.clear()
    failing = MagicMock(run_once=AsyncMock(side_effect=RuntimeError("unavailable")))
    owner = ProjectionOwner(
        worker("compatibility", False), worker("confirmation", True), failing,
    )
    assert await owner.run_once() is True
    assert calls == ["compatibility", "confirmation"]


def test_scheduled_projection_flag_defaults_off() -> None:
    from agent_runtime_worker_main import ProjectionProcessSettings

    assert ProjectionProcessSettings.model_fields[
        "agent_runtime_scheduled_web_projection_enabled"
    ].default is False
