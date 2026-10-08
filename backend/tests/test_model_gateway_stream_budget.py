"""Long, healthy output must not consume the stream inactivity timeout."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.agent.execution_budget import ExecutionBudget
from services.model_gateway import ModelCallRequest, ModelGatewaySession, ModelGatewayTimeoutError


def chunk():
    return SimpleNamespace(content="输出", thinking_content=None, tool_calls=None)


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit_timeout,budgeted,should_complete", [
    (None, True, True), (0.2, True, False), (None, False, False),
])
async def test_healthy_stream_uses_turn_budget_but_keeps_explicit_deadline(
    monkeypatch, explicit_timeout, budgeted, should_complete,
):
    monkeypatch.setattr("services.timeout_resolver.resolve_stream_timeout", lambda _: 0.2)

    async def stream_chat(**kwargs):
        for _ in range(6):
            await asyncio.sleep(0.05)
            yield chunk()

    adapter = SimpleNamespace(stream_chat=stream_chat, close=AsyncMock())
    session = ModelGatewaySession(adapter, ModelCallRequest(
        model_id="budget-test-model", timeout=explicit_timeout,
        budget=ExecutionBudget(max_wall_time=2) if budgeted else None,
    ))
    try:
        if should_complete:
            assert len([c async for c in session.stream_chat(messages=[])]) == 6
        else:
            with pytest.raises(ModelGatewayTimeoutError):
                [c async for c in session.stream_chat(messages=[])]
    finally:
        await session.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("first_chunk", [False, True])
async def test_budgeted_stream_still_times_out_when_provider_stalls(monkeypatch, first_chunk):
    monkeypatch.setattr("services.timeout_resolver.resolve_stream_timeout", lambda _: 0.02)

    async def stream_chat(**kwargs):
        if first_chunk:
            yield chunk()
        await asyncio.Event().wait()
        yield chunk()

    adapter = SimpleNamespace(stream_chat=stream_chat, close=AsyncMock())
    session = ModelGatewaySession(adapter, ModelCallRequest(
        model_id="budget-test-model", budget=ExecutionBudget(max_wall_time=2),
    ))
    with pytest.raises(ModelGatewayTimeoutError) as error:
        [c async for c in session.stream_chat(messages=[])]
    assert error.value.phase == ("stream" if first_chunk else "first_chunk")
    adapter.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_continuous_chunks_cannot_extend_turn_budget(monkeypatch):
    monkeypatch.setattr("services.timeout_resolver.resolve_stream_timeout", lambda _: 0.2)

    async def stream_chat(**kwargs):
        while True:
            await asyncio.sleep(0.005)
            yield chunk()

    adapter = SimpleNamespace(stream_chat=stream_chat, close=AsyncMock())
    budget = ExecutionBudget(max_wall_time=0.04)
    session = ModelGatewaySession(adapter, ModelCallRequest(model_id="budget-test-model", budget=budget))
    with pytest.raises(ModelGatewayTimeoutError):
        [c async for c in session.stream_chat(messages=[])]
    assert budget.remaining == 0
    adapter.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_request_idle_override_allows_active_stream_beyond_default_and_idle_limit(monkeypatch):
    monkeypatch.setattr("services.timeout_resolver.resolve_stream_timeout", lambda _: 0.005)
    async def stream_chat(**kwargs):
        for _ in range(6):
            await asyncio.sleep(0.01)
            yield chunk()
    adapter = SimpleNamespace(stream_chat=stream_chat, close=AsyncMock())
    session = ModelGatewaySession(adapter, ModelCallRequest(model_id="planner-model", idle_timeout=0.04,
        budget=ExecutionBudget(max_wall_time=1)))
    try:
        assert len([c async for c in session.stream_chat(messages=[])]) == 6
    finally:
        await session.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["first", "stalled", "heartbeat", "budget"])
async def test_explicit_idle_override_never_extends_stall_or_total_budget(mode):
    async def stream_chat(**kwargs):
        if mode == "first":
            await asyncio.Event().wait()
        yield chunk()
        if mode == "stalled":
            await asyncio.Event().wait()
        while True:
            await asyncio.sleep(0.005)
            yield SimpleNamespace(content=None, thinking_content=None, tool_calls=None) if mode == "heartbeat" else chunk()
    adapter = SimpleNamespace(stream_chat=stream_chat, close=AsyncMock())
    session = ModelGatewaySession(adapter, ModelCallRequest(model_id="planner-model", idle_timeout=0.03,
        budget=ExecutionBudget(max_wall_time=0.06 if mode == "budget" else 1)))
    with pytest.raises(ModelGatewayTimeoutError):
        [c async for c in session.stream_chat(messages=[])]
    adapter.close.assert_awaited_once()


@pytest.mark.parametrize("idle,timeout,budget", [(0, None, True), (float('inf'), None, True),
    (1, 1, True), (1, None, False)])
def test_idle_override_requires_finite_timeout_and_total_budget(idle, timeout, budget):
    with pytest.raises(ValueError, match="MODEL_GATEWAY_IDLE_TIMEOUT_REQUIRES_BUDGET"):
        ModelCallRequest(model_id="planner-model", timeout=timeout, idle_timeout=idle,
            budget=ExecutionBudget() if budget else None)
