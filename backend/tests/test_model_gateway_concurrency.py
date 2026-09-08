"""真实 asyncio stream 的准入、取消、关闭和熔断时序回归。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from core.error_classifier import classify_error
from services.adapters.types import StreamChunk, ProviderUnavailableError
from services.adapters.factory import MODEL_REGISTRY
from services.circuit_breaker import get_breaker, reset_all
from services.model_gateway import ModelCallRequest, ModelGateway, ModelGatewayTimeoutError


@pytest.fixture(autouse=True)
def clean_breakers():
    reset_all()
    yield
    reset_all()


async def until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0)


class Streams:
    def __init__(self, limit=1):
        self.started = []
        self.finished = []
        self.active = 0
        self.peak = 0
        self.events = []
        self.controls = {}
        self.adapters = {}
        self.gateway = ModelGateway(
            adapter_factory=self.factory, max_concurrency=limit,
            event_publisher=SimpleNamespace(publish=self.events.append),
        )

    def factory(self, model, **_kwargs):
        # 使用 org_id 区分请求，允许同模型的多个会话参与竞争。
        key = _kwargs.get("org_id") or model
        control = self.controls.setdefault(key, asyncio.Event())

        async def stream(**kwargs):
            self.started.append(key)
            self.active += 1
            self.peak = max(self.peak, self.active)
            try:
                if kwargs.get("first_chunk"):
                    yield StreamChunk(content="partial")
                await control.wait()
                if kwargs.get("error"):
                    raise kwargs["error"]
                yield StreamChunk(content="ok", prompt_tokens=2, completion_tokens=1)
            finally:
                self.active -= 1
                self.finished.append(key)

        adapter = SimpleNamespace(stream_chat=stream, close=AsyncMock(), supports_google_search=False)
        self.adapters[key] = adapter
        return adapter

    def session(self, key, model="fake", **kwargs):
        return self.gateway.open_chat(ModelCallRequest(
            model_id=model, org_id=key, task_id=key, timeout=kwargs.pop("timeout", 2), **kwargs,
        ))

    async def run(self, key, **kwargs):
        session = self.session(key, **kwargs)
        return await consume(session)


async def consume(session, **kwargs):
    try:
        return [chunk async for chunk in session.stream_chat(messages=[], **kwargs)]
    finally:
        await session.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [1, 3, 5])
async def test_limit_and_fifo_queue_across_sessions(limit):
    streams = Streams(limit)
    tasks = [asyncio.create_task(streams.run(str(i))) for i in range(limit + 3)]
    await until(lambda: len(streams.started) == limit)
    await until(lambda: len([e for e in streams.events if e.event.value == "started"]) == limit + 3)
    assert streams.started == [str(i) for i in range(limit)]
    for control in streams.controls.values():
        control.set()
    await asyncio.gather(*tasks)
    assert streams.started == [str(i) for i in range(limit + 3)]
    assert streams.peak == limit
    assert streams.active == 0
    assert all(e.queue_wait_ms >= 0 for e in streams.events if e.is_terminal)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_kind", ["task", "token", "poll_token", "close"])
async def test_queued_cancellation_never_calls_provider_or_consumes_slot(cancel_kind):
    streams = Streams()
    active = asyncio.create_task(streams.run("active"))
    await until(lambda: streams.started == ["active"])
    token = asyncio.Event()
    request_token = SimpleNamespace(is_set=token.is_set) if cancel_kind == "poll_token" else token
    queued_session = streams.session("queued", cancel_token=request_token)
    queued = asyncio.create_task(consume(queued_session))
    await until(lambda: queued_session._acquire_task is not None)
    if cancel_kind == "task":
        queued.cancel()
    elif cancel_kind == "close":
        await queued_session.close()
    else:
        token.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(queued, 1)
    successor = asyncio.create_task(streams.run("successor"))
    streams.controls["active"].set()
    await until(lambda: "successor" in streams.started)
    streams.controls["successor"].set()
    await asyncio.gather(active, successor)
    assert "queued" not in streams.started
    assert streams.peak == 1
    terminal = [e for e in streams.events if e.task_id == "queued" and e.is_terminal]
    assert [e.event.value for e in terminal] == ["cancelled"]


@pytest.mark.asyncio
@pytest.mark.parametrize("token_cancel", [False, True])
async def test_running_cancellation_closes_provider_and_releases_slot(token_cancel):
    streams = Streams()
    token = asyncio.Event()
    active = asyncio.create_task(streams.run("active", cancel_token=token))
    await until(lambda: streams.active == 1)
    queued = asyncio.create_task(streams.run("queued"))
    if token_cancel:
        token.set()
    else:
        active.cancel()
    with pytest.raises(asyncio.CancelledError):
        await active
    await until(lambda: "queued" in streams.started)
    streams.controls["queued"].set()
    await queued
    assert streams.peak == 1
    streams.adapters["active"].close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("first_chunk", [False, True])
async def test_execution_timeout_releases_slot(first_chunk):
    streams = Streams()
    session = streams.session("timeout", timeout=0.02)
    active = asyncio.create_task(consume(session, first_chunk=first_chunk))
    await until(lambda: streams.active == 1)
    queued = asyncio.create_task(streams.run("queued"))
    with pytest.raises(ModelGatewayTimeoutError) as error:
        await active
    assert error.value.phase == ("stream" if first_chunk else "first_chunk")
    await until(lambda: "queued" in streams.started)
    streams.controls["queued"].set()
    await queued
    assert streams.peak == 1
    assert "timeout" in streams.finished


@pytest.mark.asyncio
async def test_queue_timeout_is_observed_without_poisoning_provider_breaker():
    streams = Streams()
    active = asyncio.create_task(streams.run("active"))
    await until(lambda: streams.active == 1)
    with pytest.raises(ModelGatewayTimeoutError) as error:
        await streams.run("queued", model="qwen3.5-plus", timeout=0.02)
    classified = classify_error(error.value, model_call=True)
    assert classified.error_code == "MODEL_TIMEOUT"
    assert not classified.is_retryable and not classified.should_record_breaker
    assert error.value.phase == "queue"
    events = [e for e in streams.events if e.task_id == "queued"]
    assert [e.event.value for e in events] == ["started", "concurrency_rejected", "failed", "request_failed"]
    assert events[-2].queue_wait_ms >= 10
    assert events[-2].rejection_reason == "queue_timeout"
    assert events[-1].error_code == "MODEL_TIMEOUT"
    assert "queued" not in streams.started
    streams.controls["active"].set()
    await active
    successor = streams.session("successor")
    streams.controls["successor"].set()
    await consume(successor)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 503, 529, 400])
async def test_provider_error_releases_slot_and_records_only_structured_overload(status):
    from services.adapters.dashscope.chat_adapter import DashScopeAPIError
    streams = Streams()
    failed = streams.session("failed", model="qwen3.5-plus")
    streams.controls["failed"].set()
    with pytest.raises(DashScopeAPIError):
        await consume(failed, error=DashScopeAPIError("secret prompt or api key", status_code=status))
    successor = streams.session("successor")
    streams.controls["successor"].set()
    await consume(successor)
    overloads = [e for e in streams.events if e.event.value == "provider_overloaded"]
    assert bool(overloads) == (status in {429, 503, 529})
    assert "secret" not in repr([e.log_fields() for e in streams.events])
    assert streams.peak == 1 and streams.active == 0


@pytest.mark.asyncio
async def test_shutdown_cancels_waiting_and_running_streams_and_rejects_new_sessions():
    streams = Streams()
    sessions = [streams.session(str(i)) for i in range(4)]
    tasks = [asyncio.create_task(consume(s)) for s in sessions]
    await until(lambda: streams.active == 1 and all(s._acquire_task for s in sessions[1:]))
    await asyncio.wait_for(streams.gateway.close(), 1)
    outcomes = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 1)
    assert all(isinstance(result, asyncio.CancelledError) for result in outcomes)
    assert streams.started == ["0"] and streams.active == 0
    assert all(s._lease is None for s in sessions)
    for adapter in streams.adapters.values():
        adapter.close.assert_awaited_once()
    await streams.gateway.close()
    with pytest.raises(RuntimeError, match="MODEL_GATEWAY_CLOSED"):
        streams.session("late")


@pytest.mark.asyncio
async def test_shutdown_closes_provider_even_when_consumer_suspended_after_yield():
    streams = Streams()
    session = streams.session("suspended")
    iterator = session.stream_chat(messages=[], first_chunk=True)
    assert (await anext(iterator)).content == "partial"
    await streams.gateway.close()
    assert streams.active == 0 and session._lease is None
    assert session.last_result.status == "cancelled"
    with pytest.raises(asyncio.CancelledError):
        await anext(iterator)
    assert session.last_result.status == "cancelled"


@pytest.mark.asyncio
async def test_parallel_calls_on_same_session_cannot_corrupt_active_request():
    streams = Streams()
    session = streams.session("one")
    running = asyncio.create_task(consume(session))
    await until(lambda: streams.active == 1)
    request_id = session.last_attempt_context.request_id
    with pytest.raises(RuntimeError, match="MODEL_GATEWAY_SESSION_BUSY"):
        await anext(session.stream_chat(messages=[]))
    streams.controls["one"].set()
    await running
    assert session.last_result.request_id == request_id
    assert len(session.last_result.attempts) == 1
    assert session.last_result.status == "completed"


@pytest.mark.asyncio
async def test_shutdown_wins_when_provider_chunk_is_already_ready():
    streams = Streams()
    session = streams.session("race")
    streams.controls["race"].set()
    pending = asyncio.create_task(consume(session))
    await until(lambda: session._next_chunk is not None and session._next_chunk.done())
    await streams.gateway.close()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert not any(e.event.value == "first_chunk" for e in streams.events)
    assert session.last_result.status == "cancelled"


@pytest.mark.asyncio
async def test_cancel_and_acquire_same_tick_does_not_leak_or_overrelease():
    streams = Streams()
    for i in range(12):
        token = asyncio.Event()
        session = streams.session(f"race-{i}", cancel_token=token)
        # 让底层 semaphore 已经授予槽位，再同时取消 owner/token。
        pending = asyncio.create_task(consume(session))
        await until(lambda: session._acquire_task is not None and session._acquire_task.done())
        token.set()
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
    first = asyncio.create_task(streams.run("first"))
    second = asyncio.create_task(streams.run("second"))
    await until(lambda: "first" in streams.started)
    assert "second" not in streams.started
    streams.controls["first"].set()
    await until(lambda: "second" in streams.started)
    streams.controls["second"].set()
    await asyncio.gather(first, second)
    assert streams.peak == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("queue_first", [False, True])
async def test_preopened_session_rechecks_breaker_before_dispatch_and_while_queued(queue_first):
    streams = Streams()
    active = asyncio.create_task(streams.run("unrelated"))
    await until(lambda: streams.active == 1)
    session = streams.session("qwen", model="qwen3.5-plus")
    if queue_first:
        pending = asyncio.create_task(consume(session))
        await until(lambda: session._acquire_task is not None)
    breaker = get_breaker(MODEL_REGISTRY["qwen3.5-plus"].provider)
    for _ in range(3):
        breaker.record_failure()
    if not queue_first:
        pending = asyncio.create_task(consume(session))
    with pytest.raises(ProviderUnavailableError):
        await asyncio.wait_for(pending, 0.5)
    assert streams.started == ["unrelated"]  # 不等待无关 stream 完成
    assert len(breaker._failure_timestamps) == 3
    assert any(e.rejection_reason == "circuit_open" for e in streams.events)
    breaker._opened_at -= 31
    streams.controls["unrelated"].set()
    await active
    recovered = streams.session("recovered", model="qwen3.5-plus")
    streams.controls["recovered"].set()
    await consume(recovered)
    assert breaker.state.value == "closed"


@pytest.mark.asyncio
async def test_execution_budget_also_bounds_queue():
    streams = Streams()
    active = asyncio.create_task(streams.run("active"))
    await until(lambda: streams.active == 1)
    with pytest.raises(ModelGatewayTimeoutError) as error:
        await streams.run("queued", budget=SimpleNamespace(remaining=0))
    assert error.value.phase == "queue"
    streams.controls["active"].set()
    await active


def test_configuration_default_and_invalid_limit():
    from core.config import Settings
    from pydantic import ValidationError
    assert Settings(_env_file=None).model_gateway_max_concurrency == 5
    with pytest.raises(ValidationError):
        Settings(_env_file=None, model_gateway_max_concurrency=0)
    with pytest.raises(ValueError):
        ModelGateway(max_concurrency=0)
