"""AR-09 ModelPort 与现有 Provider adapter 的确定边界测试。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from services.adapters.types import StreamChunk, ToolCallDelta
from services.agent.runtime.context import ProviderContextPlan
from services.agent.runtime.domain import ModelStepId, StopReason
from services.agent.runtime.infrastructure.model import (
    ExistingProviderModelAdapter,
    compute_request_hash,
    resolve_model_revision,
)
from services.agent.runtime.ports import (
    ModelCallUnknownError,
    ModelInputReceipt,
    ModelRequestOptions,
    ModelStepRequest,
)


MODEL_ID = "qwen3.5-plus"


class ProviderFailure(RuntimeError):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"provider status {status_code}")
        self.status_code = status_code


class FakeAdapter:
    def __init__(self, events: list[Any]) -> None:
        self.events = events
        self.closed = False
        self.payload: dict[str, Any] = {}

    async def stream_chat(self, **kwargs: Any):
        self.payload = kwargs
        for event in self.events:
            if isinstance(event, BaseException):
                raise event
            if callable(event):
                await event()
                continue
            yield event

    async def close(self) -> None:
        self.closed = True


def _plan() -> ProviderContextPlan:
    return ProviderContextPlan.build(
        messages=[
            {"role": "system", "content": "secret system prompt"},
            {"role": "user", "content": "sensitive user text"},
        ],
        tools=[{
            "type": "function",
            "function": {
                "name": "lookup",
                "parameters": {"type": "object"},
            },
        }],
        context_epoch_id="epoch-1",
        model_step=1,
        stable_prefix_blocks=1,
    )


def _request(
    *,
    options: ModelRequestOptions | None = None,
    request_hash: str | None = None,
) -> ModelStepRequest:
    plan = _plan()
    actual_options = options or ModelRequestOptions()
    model_revision = resolve_model_revision(MODEL_ID)
    actual_hash = compute_request_hash(
        model_id=MODEL_ID,
        model_revision=model_revision,
        prompt_revision="prompt-r1",
        tool_catalog_revision="tools-r1",
        input_receipt_hash="input-hash",
        context_plan_hash=plan.plan_hash,
        options=actual_options,
    )
    return ModelStepRequest(
        model_step_id=ModelStepId("step-1"),
        model_id=MODEL_ID,
        request_hash=request_hash or actual_hash,
        input_receipt=ModelInputReceipt(
            receipt_id="receipt-1",
            receipt_hash="input-hash",
            context_plan_hash=plan.plan_hash,
        ),
        context_plan=plan,
        model_revision=model_revision,
        prompt_revision="prompt-r1",
        tool_catalog_revision="tools-r1",
        options=actual_options,
    )


def _port(adapters: list[FakeAdapter]) -> ExistingProviderModelAdapter:
    remaining = list(adapters)

    def factory(*_args: Any, **_kwargs: Any) -> FakeAdapter:
        return remaining.pop(0)

    return ExistingProviderModelAdapter(
        org_id="org-secret",
        db=object(),
        adapter_factory=factory,
    )


@pytest.mark.asyncio
async def test_normal_text_usage_and_provider_projection() -> None:
    adapter = FakeAdapter([StreamChunk(
        content="answer",
        finish_reason="stop",
        prompt_tokens=11,
        completion_tokens=7,
        reasoning_tokens=3,
        cached_tokens=5,
        cache_creation_tokens=2,
    )])

    result = await _port([adapter]).complete(_request())

    assert result.stop_reason is StopReason.FINAL
    assert result.output and result.output.content == "answer"
    assert result.usage.as_tuple() == (11, 7, 3, 5, 2)
    assert adapter.payload["messages"] == _plan().project()[0]
    assert adapter.payload["tools"] == _plan().project()[1]
    assert "org-secret" not in str(adapter.payload)
    assert adapter.closed is True


@pytest.mark.asyncio
async def test_multiple_tool_calls_are_complete_and_ordered() -> None:
    adapter = FakeAdapter([
        StreamChunk(tool_calls=[
            ToolCallDelta(1, "call-b", "second", '{"b":'),
            ToolCallDelta(0, "call-a", "first", '{"a":'),
        ]),
        StreamChunk(
            finish_reason="tool_calls",
            tool_calls=[
                ToolCallDelta(1, arguments_delta="2}"),
                ToolCallDelta(0, arguments_delta="1}"),
            ],
        ),
    ])

    result = await _port([adapter]).complete(_request())

    assert result.stop_reason is StopReason.TOOL_CALLS
    assert [call.call_id for call in result.tool_calls] == [
        "call-a",
        "call-b",
    ]
    assert [call.arguments_json for call in result.tool_calls] == [
        '{"a":1}',
        '{"b":2}',
    ]


@pytest.mark.asyncio
async def test_missing_provider_tool_id_gets_stable_runtime_id() -> None:
    event = StreamChunk(
        finish_reason="tool_calls",
        tool_calls=[
            ToolCallDelta(0, None, "lookup", '{"id":1}'),
        ],
    )

    first = await _port([FakeAdapter([event])]).complete(_request())
    second = await _port([FakeAdapter([event])]).complete(_request())

    assert first.tool_calls[0].call_id == second.tool_calls[0].call_id
    assert first.tool_calls[0].call_id.startswith("runtime-")
    assert first.tool_calls[0].provider_call_id is None


@pytest.mark.asyncio
async def test_structured_final_is_canonicalized() -> None:
    options = ModelRequestOptions(
        structured_output=True,
        response_schema_revision="schema-r1",
    )
    adapter = FakeAdapter([
        StreamChunk(content='{"b":2, "a":1}', finish_reason="stop"),
    ])

    result = await _port([adapter]).complete(_request(options=options))

    assert result.stop_reason is StopReason.STRUCTURED_FINAL
    assert result.output and result.output.content == '{"a":1,"b":2}'
    assert result.output.schema_revision == "schema-r1"
    assert adapter.payload["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
async def test_repeated_cumulative_usage_is_not_double_counted() -> None:
    usage_chunk = StreamChunk(
        prompt_tokens=10,
        completion_tokens=5,
        reasoning_tokens=2,
        cached_tokens=3,
        cache_creation_tokens=1,
    )
    adapter = FakeAdapter([
        usage_chunk,
        StreamChunk(
            content="answer",
            finish_reason="stop",
            prompt_tokens=10,
            completion_tokens=5,
            reasoning_tokens=2,
            cached_tokens=3,
            cache_creation_tokens=1,
        ),
    ])

    result = await _port([adapter]).complete(_request())

    assert result.usage.as_tuple() == (10, 5, 2, 3, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_reason", "content", "expected"),
    (
        ("stop", None, StopReason.PROTOCOL_ERROR),
        ("length", None, StopReason.LENGTH),
        ("content_filter", None, StopReason.CONTENT_FILTER),
        ("refusal", None, StopReason.MODEL_REFUSAL),
        ("unrecognized", "answer", StopReason.PROTOCOL_ERROR),
    ),
)
async def test_stop_reason_mapping_is_closed(
    provider_reason: str,
    content: str | None,
    expected: StopReason,
) -> None:
    adapter = FakeAdapter([
        StreamChunk(content=content, finish_reason=provider_reason),
    ])

    result = await _port([adapter]).complete(_request())

    assert result.stop_reason is expected
    assert result.provider_stop_reason == provider_reason


@pytest.mark.asyncio
async def test_explicit_refusal_overrides_provider_stop() -> None:
    adapter = FakeAdapter([
        StreamChunk(finish_reason="stop", refusal=True),
    ])

    result = await _port([adapter]).complete(_request())

    assert result.stop_reason is StopReason.MODEL_REFUSAL
    assert result.provider_stop_reason == "stop"


@pytest.mark.asyncio
async def test_incomplete_tool_call_is_protocol_error() -> None:
    adapter = FakeAdapter([StreamChunk(
        finish_reason="tool_calls",
        tool_calls=[ToolCallDelta(0, "call-1", "lookup", '{"x":')],
    )])

    result = await _port([adapter]).complete(_request())

    assert result.stop_reason is StopReason.PROTOCOL_ERROR
    assert result.tool_calls == ()


@pytest.mark.asyncio
async def test_429_does_not_redispatch_without_typed_evidence() -> None:
    first = FakeAdapter([ProviderFailure(429)])
    second = FakeAdapter([
        StreamChunk(content="ok", finish_reason="stop"),
    ])
    options = ModelRequestOptions(max_provider_attempts=2)

    with pytest.raises(ModelCallUnknownError) as caught:
        await _port([first, second]).complete(_request(options=options))

    assert caught.value.attempts[0].outcome == "unknown"
    assert first.closed is True
    assert second.closed is False


@pytest.mark.asyncio
async def test_http_rejection_without_nonexecution_proof_is_unknown() -> None:
    adapter = FakeAdapter([ProviderFailure(400)])

    with pytest.raises(ModelCallUnknownError) as caught:
        await _port([adapter]).complete(_request())

    assert caught.value.attempts[0].outcome == "unknown"
    assert caught.value.attempts[0].status_code == 400


@pytest.mark.asyncio
async def test_partial_stream_failure_is_unknown_and_not_retried() -> None:
    first = FakeAdapter([
        StreamChunk(content="partial"),
        ProviderFailure(503),
    ])
    unused = FakeAdapter([
        StreamChunk(content="must not run", finish_reason="stop"),
    ])
    options = ModelRequestOptions(max_provider_attempts=2)

    with pytest.raises(ModelCallUnknownError) as caught:
        await _port([first, unused]).complete(_request(options=options))

    assert caught.value.attempts[0].response_started is True
    assert caught.value.attempts[0].outcome == "unknown"
    assert unused.closed is False


@pytest.mark.asyncio
async def test_timeout_is_unknown() -> None:
    async def block() -> None:
        await asyncio.sleep(1)

    options = ModelRequestOptions(timeout_seconds=0.01)
    adapter = FakeAdapter([block])

    with pytest.raises(ModelCallUnknownError) as caught:
        await _port([adapter]).complete(_request(options=options))

    assert caught.value.attempts[0].outcome == "unknown"
    assert adapter.closed is True


@pytest.mark.asyncio
async def test_provider_wrapped_timeout_is_unknown_and_not_retried() -> None:
    try:
        raise TimeoutError("read timeout")
    except TimeoutError as cause:
        wrapped = RuntimeError("provider request failed")
        wrapped.__cause__ = cause
    first = FakeAdapter([wrapped])
    unused = FakeAdapter([
        StreamChunk(content="must not run", finish_reason="stop"),
    ])
    options = ModelRequestOptions(max_provider_attempts=2)

    with pytest.raises(ModelCallUnknownError) as caught:
        await _port([first, unused]).complete(_request(options=options))

    assert caught.value.attempts[0].outcome == "unknown"
    assert unused.closed is False


@pytest.mark.asyncio
async def test_cancellation_propagates_and_closes_adapter() -> None:
    started = asyncio.Event()

    async def block() -> None:
        started.set()
        await asyncio.sleep(10)

    adapter = FakeAdapter([block])
    task = asyncio.create_task(_port([adapter]).complete(_request()))
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert adapter.closed is True


@pytest.mark.asyncio
async def test_request_hash_mismatch_fails_before_provider() -> None:
    adapter = FakeAdapter([
        StreamChunk(content="must not run", finish_reason="stop"),
    ])

    with pytest.raises(ValueError, match="MODEL_REQUEST_HASH_MISMATCH"):
        await _port([adapter]).complete(_request(request_hash="bad-hash"))

    assert adapter.closed is False


@pytest.mark.asyncio
async def test_model_revision_mismatch_fails_before_provider() -> None:
    request = _request()
    invalid = ModelStepRequest(
        model_step_id=request.model_step_id,
        model_id=request.model_id,
        request_hash=request.request_hash,
        input_receipt=request.input_receipt,
        context_plan=request.context_plan,
        model_revision="stale-revision",
        prompt_revision=request.prompt_revision,
        tool_catalog_revision=request.tool_catalog_revision,
        options=request.options,
    )
    adapter = FakeAdapter([
        StreamChunk(content="must not run", finish_reason="stop"),
    ])

    with pytest.raises(ValueError, match="MODEL_REVISION_MISMATCH"):
        await _port([adapter]).complete(invalid)

    assert adapter.closed is False


@pytest.mark.asyncio
async def test_context_projection_mismatch_fails_before_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = FakeAdapter([
        StreamChunk(content="must not run", finish_reason="stop"),
    ])
    monkeypatch.setattr(
        ProviderContextPlan,
        "matches",
        lambda *_args: False,
    )

    with pytest.raises(ValueError, match="CONTEXT_PLAN_PROJECTION_MISMATCH"):
        await _port([adapter]).complete(_request())

    assert adapter.closed is False


@pytest.mark.asyncio
async def test_receipts_do_not_contain_sensitive_content() -> None:
    adapter = FakeAdapter([
        StreamChunk(content="private answer", finish_reason="stop"),
    ])

    result = await _port([adapter]).complete(_request())

    serialized = str(result.response_receipt)
    assert "private answer" not in serialized
    assert "secret system prompt" not in serialized
    assert "sensitive user text" not in serialized
    assert "org-secret" not in serialized
