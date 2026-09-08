"""验证 Gateway 事件经过真实发布器和生产日志格式后仍可查询。"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from loguru import logger

from core import logging_config
from services.agent.observability.model_sampling import (
    ModelSamplingEvent,
    ObservabilitySamplingEventPublisher,
    SamplingEventType,
)
from services.model_gateway import ModelCallRequest, ModelGateway, ModelGatewayTimeoutError
from services.circuit_breaker import reset_all


@pytest.fixture
def sampling_logs(tmp_path, monkeypatch):
    reset_all()
    # 运行生产 setup_logging，只把文件目录移到临时目录；不替换 logger/sink。
    core_dir = tmp_path / "backend" / "core"
    core_dir.mkdir(parents=True)
    monkeypatch.setattr(logging_config, "__file__", str(core_dir / "logging_config.py"))
    handler_ids = []
    add = logger.add

    def track_add(*args, **kwargs):
        handler_id = add(*args, **kwargs)
        handler_ids.append(handler_id)
        return handler_id

    monkeypatch.setattr(logger, "add", track_add)
    generation = Mock()
    monkeypatch.setattr(
        "services.agent.observability.langfuse_integration.create_model_gateway_generation",
        generation,
    )
    try:
        logging_config.setup_logging()
        yield SimpleNamespace(directory=core_dir.parent / "logs", generation=generation)
    finally:
        for handler_id in handler_ids:
            logger.remove(handler_id)
        reset_all()


def read_log(logs):
    return "".join(path.read_text() for path in logs.directory.glob("app_*.log"))


def read_events(logs):
    return [
        json.loads(line.split("ModelGateway sampling event", 1)[1].strip())
        for line in read_log(logs).splitlines() if "ModelGateway sampling event" in line
    ]


@pytest.mark.parametrize("event_type", list(SamplingEventType))
def test_every_sampling_event_is_queryable_in_configured_log(sampling_logs, event_type):
    event = ModelSamplingEvent(
        event=event_type, request_id="request-1", attempt_id="attempt-2",
        model_id="model-1", provider="provider-1", task_id="task-1", trace_id="trace-1",
        request_index=2, turn_index=3, usage={"prompt_tokens": 4, "completion_tokens": 2},
        queue_wait_ms=12.5, rejection_reason="queue_timeout", error_code="MODEL_TIMEOUT",
        stop_reason="not_retryable", error_type="ModelGatewayTimeoutError",
        previous_attempt_id="attempt-1",
    )
    # 上下文中的任意 extra 不应随着安全事件字段被序列化。
    with logger.contextualize(api_key="private-key", prompt="private-prompt"):
        ObservabilitySamplingEventPublisher().publish(event)

    assert read_events(sampling_logs) == [{
        "event": event_type.value, "request_id": "request-1", "attempt_id": "attempt-2",
        "model_id": "model-1", "provider": "provider-1", "task_id": "task-1", "trace_id": "trace-1",
        "request_index": 2, "turn_index": 3, "usage": {"prompt_tokens": 4, "completion_tokens": 2},
        "queue_wait_ms": 12.5, "rejection_reason": "queue_timeout", "error_code": "MODEL_TIMEOUT",
        "stop_reason": "not_retryable", "error_type": "ModelGatewayTimeoutError",
        "previous_attempt_id": "attempt-1",
    }]
    assert "private-" not in read_log(sampling_logs)
    if event_type in {SamplingEventType.COMPLETED, SamplingEventType.FAILED, SamplingEventType.CANCELLED}:
        sampling_logs.generation.assert_called_once_with(
            trace_id="trace-1", model="model-1", metadata=read_events(sampling_logs)[0],
        )
        sampling_logs.generation.return_value.end.assert_called_once_with(usage=dict(event.usage))
    else:
        sampling_logs.generation.assert_not_called()


@pytest.mark.asyncio
async def test_queued_gateway_timeout_and_success_reach_log_without_sensitive_content(sampling_logs):
    from services.adapters.types import StreamChunk

    started, release = asyncio.Event(), asyncio.Event()

    async def stream(**_kwargs):
        started.set()
        await release.wait()
        yield StreamChunk(content="private-response", prompt_tokens=3, completion_tokens=1)

    gateway = ModelGateway(
        adapter_factory=lambda *_args, **_kwargs: SimpleNamespace(stream_chat=stream, close=AsyncMock()),
        max_concurrency=1,
    )

    async def consume(task_id, timeout):
        session = gateway.open_chat(ModelCallRequest(model_id="fake", task_id=task_id, timeout=timeout))
        try:
            return [chunk async for chunk in session.stream_chat(
                messages=[{"role": "user", "content": "private-prompt"}], api_key="private-key",
            )]
        finally:
            await session.close()

    active = asyncio.create_task(consume("active", 2))
    try:
        await asyncio.wait_for(started.wait(), 1)
        with pytest.raises(ModelGatewayTimeoutError) as raised:
            await consume("queued", 0.02)
        assert raised.value.phase == "queue"
        release.set()
        assert (await active)[0].content == "private-response"
        async with asyncio.timeout(2):
            while len(read_events(sampling_logs)) < 7 or sampling_logs.generation.call_count < 2:
                await asyncio.sleep(0.005)
        events = read_events(sampling_logs)
        queued = {event["event"]: event for event in events if event["task_id"] == "queued"}
        assert set(queued) == {"started", "concurrency_rejected", "failed", "request_failed"}
        assert queued["concurrency_rejected"]["queue_wait_ms"] >= 10
        assert queued["failed"]["rejection_reason"] == "queue_timeout"
        assert queued["request_failed"]["error_code"] == "MODEL_TIMEOUT"
        assert len({event["request_id"] for event in queued.values()}) == 1
        assert len({event["attempt_id"] for event in queued.values()}) == 1
        completed = next(event for event in events if event["event"] == "completed")
        assert completed["task_id"] == "active"
        assert completed["usage"] == {"prompt_tokens": 3, "completion_tokens": 1}
        assert "private-" not in read_log(sampling_logs)
    finally:
        release.set()
        await gateway.close()
        await asyncio.gather(active, return_exceptions=True)


@pytest.mark.asyncio
async def test_provider_overload_retry_and_final_failure_are_linked_in_log(sampling_logs):
    from schemas.message import GenerationType
    from services.adapters.dashscope.chat_adapter import DashScopeAPIError
    from services.intent_router import RetryContext
    from services.model_gateway import ModelGatewayError, ModelRetryPolicy

    async def stream(**_kwargs):
        raise DashScopeAPIError("private-provider-error-with-key", status_code=503)
        yield  # 保留 Provider async iterator 协议。

    context = RetryContext(True, "private-prompt", GenerationType.CHAT)

    def build_context(model, error, _context):
        context.add_failure(model, str(error))
        return context

    policy = ModelRetryPolicy(
        build_context=build_context,
        route=AsyncMock(return_value=SimpleNamespace(recommended_model="gemini-3-pro")),
        record_breaker=Mock(),
    )
    gateway = ModelGateway(adapter_factory=lambda *_args, **_kwargs: SimpleNamespace(
        stream_chat=stream, close=AsyncMock(),
    ))
    session = gateway.open_chat(ModelCallRequest(
        model_id="qwen3.5-plus", task_id="retry-task", retry_policy=policy, timeout=1,
    ))
    try:
        with pytest.raises(ModelGatewayError):
            [chunk async for chunk in session.stream_chat(messages=[{
                "role": "user", "content": "private-prompt",
            }])]
        async with asyncio.timeout(2):
            while len(read_events(sampling_logs)) < 8 or sampling_logs.generation.call_count < 2:
                await asyncio.sleep(0.005)
        events = read_events(sampling_logs)
        assert len([e for e in events if e["event"] == "provider_overloaded"]) == 2
        failed = [e for e in events if e["event"] == "failed"]
        assert len(failed) == 2
        retry = next(e for e in events if e["event"] == "retry_started")
        first = next(e for e in failed if e["model_id"] == "qwen3.5-plus")
        assert retry["previous_attempt_id"] == first["attempt_id"]
        assert retry["model_id"] == "gemini-3-pro"
        final = next(e for e in events if e["event"] == "request_failed")
        assert final["error_code"] == "GENERATION_FAILED"
        assert final["stop_reason"] == "retry_exhausted"
        assert len({e["request_id"] for e in events}) == 1
        assert "private-" not in read_log(sampling_logs)
        assert sampling_logs.generation.call_count == 2
    finally:
        await gateway.close()
