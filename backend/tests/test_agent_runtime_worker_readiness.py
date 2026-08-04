from types import SimpleNamespace

import pytest

from agent_runtime_worker_main import (
    _apply_gate_state,
    _can_run_cycle,
    _health_payload,
    _redacted_error,
    _report_heartbeat,
)
from services.agent.runtime.composition import RuntimeOwner


def test_health_payload_is_not_ready_before_gate_and_is_liveness_only() -> None:
    state = {
        "liveness": True,
        "ready": False,
        "draining": False,
        "status": "disabled",
        "reason": "GATE_CLOSED",
    }

    payload = _health_payload(state, "agent_runtime", False)

    assert payload["liveness"] is True
    assert payload["ready"] is False
    assert payload["status"] == "disabled"
    assert payload["reason"] == "GATE_CLOSED"


def test_gate_closed_does_not_enter_claim_cycle() -> None:
    owner = object()
    cycle = lambda: None

    assert _can_run_cycle(owner, cycle, gate_enabled=False, ready=False) is False


def test_unready_composition_does_not_enter_claim_cycle() -> None:
    owner = SimpleNamespace(ready=False)
    assert _can_run_cycle(owner, lambda: None, gate_enabled=True, ready=True) is False


def test_gate_close_immediately_clears_readiness() -> None:
    state = {
        "liveness": True,
        "ready": True,
        "draining": False,
        "status": "ready",
        "reason": "ACCEPTING",
    }

    _apply_gate_state(state, True, False, "GATE_CLOSED")

    assert state["ready"] is False
    assert state["status"] == "disabled"


def test_health_payload_is_not_ready_while_draining() -> None:
    payload = _health_payload(
        {
            "liveness": True,
            "ready": True,
            "draining": True,
            "status": "draining",
            "reason": "SIGTERM",
        },
        "agent_runtime",
        False,
    )

    assert payload["ready"] is False
    assert payload["draining"] is True


def test_startup_failure_reason_is_redacted() -> None:
    assert _redacted_error(RuntimeError("SERVICE_WIRING_NOT_READY:/secret")) == (
        "SERVICE_WIRING_NOT_READY"
    )
    assert _redacted_error(RuntimeError("/private/path")) == "RUNTIMEERROR"


@pytest.mark.asyncio
async def test_heartbeat_reports_actual_readiness_and_draining() -> None:
    calls = []

    class Rpc:
        async def execute(self):
            return SimpleNamespace(data={})

    class Db:
        def rpc(self, name, params):
            calls.append((name, params))
            return Rpc()

    settings = SimpleNamespace(
        agent_runtime_worker_id="worker",
        agent_runtime_release_revision="revision",
    )

    assert await _report_heartbeat(
        Db(), settings, "agent_runtime", ready=False, draining=False,
        status_code="gate_closed",
    )
    assert await _report_heartbeat(
        Db(), settings, "agent_runtime", ready=False, draining=True,
        status_code="draining",
    )
    assert calls[0][1]["p_ready"] is False
    assert calls[0][1]["p_draining"] is False
    assert calls[1][1]["p_ready"] is False
    assert calls[1][1]["p_draining"] is True


@pytest.mark.asyncio
async def test_runtime_owner_drain_is_idempotent_and_stops_next_claim() -> None:
    events = []

    class Commands:
        async def run_once(self):
            events.append("command")
            return True

        def stop(self):
            events.append("command_stop")

    class Runtime:
        async def run_once(self):
            events.append("run")
            return True

        async def action_once(self):
            events.append("action")
            return True

        async def reconciliation_once(self):
            events.append("reconcile")
            return True

        def stop(self):
            events.append("runtime_stop")

    owner = RuntimeOwner(Commands(), Runtime())
    owner.drain()
    owner.drain()

    assert await owner.run_once() is False
    assert events == ["command_stop", "runtime_stop"]
