"""Runtime stream event and publish-only transport contracts."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from services.agent.runtime.domain import StopReason
from services.agent.runtime.infrastructure.stream_publisher import (
    RedisRuntimeStreamPublisher,
    RuntimeWebSocketStreamObserver,
)
from services.agent.runtime.application.model_stream_hooks import await_model_work
from services.agent.runtime.infrastructure.stream_composition import (
    build_runtime_stream_components,
)
from services.agent.runtime.ports.model import (
    ModelOutput,
    ModelOutputKind,
    ModelResponseReceipt,
    ModelStepResult,
    ModelStreamDelta,
    ModelToolCall,
    ModelUsage,
    ProviderAttemptOutcome,
    ProviderAttemptReceipt,
)
from services.agent.runtime.ports.stream import RuntimeStreamTarget


TARGET = RuntimeStreamTarget(
    task_id="client-task",
    user_id="user-1",
    conversation_id="conversation-1",
    message_id="message-1",
    org_id="org-1",
)


class FakePublisher:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []

    async def publish(self, *, target, message) -> None:
        assert target == TARGET
        self.messages.append(dict(message))

    async def close(self) -> None:
        return


def _final_result(stop_reason: StopReason = StopReason.FINAL) -> ModelStepResult:
    tool_calls = (
        ModelToolCall(
            index=0,
            call_id="call-1",
            name="lookup",
            arguments_json="{}",
        ),
    ) if stop_reason is StopReason.TOOL_CALLS else ()
    return ModelStepResult(
        stop_reason=stop_reason,
        provider_stop_reason="stop",
        response_hash="a" * 64,
        response_receipt=ModelResponseReceipt(
            output_kind=ModelOutputKind.TEXT,
            output_characters=7,
            tool_call_count=len(tool_calls),
            invalid_tool_call_count=0,
            usage=ModelUsage(),
            provider="test",
        ),
        output=ModelOutput(kind=ModelOutputKind.TEXT, content="answer"),
        tool_calls=tool_calls,
        usage=ModelUsage(),
        attempts=(ProviderAttemptReceipt(
            attempt_number=1,
            provider="test",
            outcome=ProviderAttemptOutcome.COMPLETED,
        ),),
    )


@pytest.mark.asyncio
async def test_observer_maps_runtime_lifecycle_to_existing_ws_contract() -> None:
    publisher = FakePublisher()
    observer = RuntimeWebSocketStreamObserver(
        publisher=publisher, target=TARGET, model_id="model-1",
    )

    await observer.stream_started(model_id="model-1")
    await observer.stream_started(model_id="model-1")
    await observer.stream_delta(
        delta=ModelStreamDelta(kind="text", value={"text": "answer"}),
    )
    await observer.stream_completed(result=_final_result())

    assert [message["type"] for message in publisher.messages] == [
        "message_start", "message_chunk", "stream_end",
    ]
    assert publisher.messages[1]["payload"] == {"chunk": "answer"}


@pytest.mark.asyncio
async def test_tool_call_does_not_end_stream_before_next_model_turn() -> None:
    publisher = FakePublisher()
    observer = RuntimeWebSocketStreamObserver(
        publisher=publisher, target=TARGET, model_id="model-1",
    )

    await observer.stream_started(model_id="model-1")
    await observer.stream_completed(
        result=_final_result(StopReason.TOOL_CALLS),
    )

    assert [message["type"] for message in publisher.messages] == [
        "message_start", "tool_call",
    ]


@pytest.mark.asyncio
async def test_stream_failure_emits_terminal_message_error() -> None:
    publisher = FakePublisher()
    observer = RuntimeWebSocketStreamObserver(
        publisher=publisher, target=TARGET, model_id="model-1",
    )

    await observer.stream_failed(error_code="RUNTIME_MODEL_STREAM_FAILED")

    assert publisher.messages[0]["type"] == "message_error"
    assert publisher.messages[0]["payload"] == {
        "error": {
            "code": "RUNTIME_MODEL_STREAM_FAILED",
            "message": "生成服务暂时不可用，请稍后重试",
        },
    }


@pytest.mark.asyncio
async def test_model_failure_notifies_observer_before_error_propagates() -> None:
    class Observer:
        def __init__(self) -> None:
            self.error_codes: list[str] = []

        async def stream_failed(self, *, error_code: str) -> None:
            self.error_codes.append(error_code)

    observer = Observer()

    async def fail() -> ModelStepResult:
        raise RuntimeError("provider unavailable")

    with pytest.raises(RuntimeError, match="provider unavailable"):
        await await_model_work(fail(), observer)

    assert observer.error_codes == ["RUNTIME_MODEL_STREAM_FAILED"]


@pytest.mark.asyncio
async def test_redis_publisher_emits_existing_ws_envelope_without_subscribing() -> None:
    client = AsyncMock()
    with patch(
        "services.agent.runtime.infrastructure.stream_publisher.Redis",
        return_value=client,
    ) as redis_factory:
        publisher = RedisRuntimeStreamPublisher(
            host="redis.internal", port=6380, password="secret",
            db=3, ssl=True, worker_id="runtime-1",
        )
        await publisher.publish(target=TARGET, message={"type": "message_chunk"})
        await publisher.close()

    redis_factory.assert_called_once_with(
        host="redis.internal", port=6380, password="secret", db=3, ssl=True,
        encoding="utf-8", decode_responses=True,
        socket_timeout=5.0, socket_connect_timeout=5.0,
    )
    payload = json.loads(client.publish.call_args.args[1])
    assert payload["target_type"] == "user"
    assert payload["target_id"] == "user-1"
    assert payload["org_id"] == "org-1"
    assert payload["message"] == {"type": "message_chunk"}
    client.subscribe.assert_not_called()
    client.get.assert_not_called()
    client.aclose.assert_awaited_once()


def test_runtime_stream_is_disabled_without_explicit_process_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AGENT_RUNTIME_STREAM_ENABLED", raising=False)
    publisher, builder = build_runtime_stream_components(
        object(), worker_id="runtime-1",
    )

    assert publisher is None
    assert builder is None
