"""AR-09 Provider retry 与 cleanup 审计阻塞项回归测试。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from services.adapters.types import StreamChunk
from services.agent.runtime.context import ProviderContextPlan
from services.agent.runtime.domain import ModelStepId
from services.agent.runtime.infrastructure.model import (
    ExistingProviderModelAdapter,
    compute_request_hash,
    resolve_model_revision,
)
from services.agent.runtime.ports import (
    ModelCallUnknownError,
    ModelInputReceipt,
    ModelProviderError,
    ModelRequestOptions,
    ModelStepRequest,
)


MODEL_ID = "qwen3.5-plus"
CloseBehavior = Callable[[], Awaitable[None]]


class ProviderFailure(RuntimeError):
    def __init__(self, status_code: int) -> None:
        super().__init__("provider request failed")
        self.status_code = status_code


class AuditAdapter:
    def __init__(
        self,
        events: list[Any],
        *,
        close_behavior: CloseBehavior | None = None,
    ) -> None:
        self.events = events
        self.close_behavior = close_behavior
        self.close_attempts = 0

    async def stream_chat(self, **_kwargs: Any):
        for event in self.events:
            if isinstance(event, BaseException):
                raise event
            if callable(event):
                await event()
                continue
            yield event

    async def close(self) -> None:
        self.close_attempts += 1
        if self.close_behavior is not None:
            await self.close_behavior()


def _request(*, max_attempts: int = 1) -> ModelStepRequest:
    plan = ProviderContextPlan.build(
        messages=[{"role": "user", "content": "sensitive input"}],
        tools=[],
        context_epoch_id="epoch-audit",
        model_step=1,
        stable_prefix_blocks=0,
    )
    options = ModelRequestOptions(max_provider_attempts=max_attempts)
    model_revision = resolve_model_revision(MODEL_ID)
    request_hash = compute_request_hash(
        model_id=MODEL_ID,
        model_revision=model_revision,
        prompt_revision="prompt-r1",
        tool_catalog_revision="tools-r1",
        input_receipt_hash="input-hash",
        context_plan_hash=plan.plan_hash,
        options=options,
    )
    return ModelStepRequest(
        model_step_id=ModelStepId("step-audit"),
        model_id=MODEL_ID,
        request_hash=request_hash,
        input_receipt=ModelInputReceipt(
            receipt_id="receipt-audit",
            receipt_hash="input-hash",
            context_plan_hash=plan.plan_hash,
        ),
        context_plan=plan,
        model_revision=model_revision,
        prompt_revision="prompt-r1",
        tool_catalog_revision="tools-r1",
        options=options,
    )


def _port(
    adapters: list[AuditAdapter],
    *,
    close_timeout_seconds: float = 5.0,
) -> ExistingProviderModelAdapter:
    remaining = list(adapters)

    def factory(*_args: Any, **_kwargs: Any) -> AuditAdapter:
        return remaining.pop(0)

    return ExistingProviderModelAdapter(
        adapter_factory=factory,
        close_timeout_seconds=close_timeout_seconds,
    )


async def _close_error() -> None:
    raise RuntimeError("sensitive close detail")


async def _spurious_close_cancel() -> None:
    raise asyncio.CancelledError


@pytest.mark.asyncio
async def test_429_without_response_is_safely_retried() -> None:
    first = AuditAdapter([ProviderFailure(429)])
    second = AuditAdapter([
        StreamChunk(content="ok", finish_reason="stop"),
    ])

    result = await _port([first, second]).complete(
        _request(max_attempts=2)
    )

    assert [attempt.outcome for attempt in result.attempts] == [
        "retrying",
        "completed",
    ]
    assert result.attempts[0].retry_reason == "http_429"
    assert first.close_attempts == second.close_attempts == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", (502, 503, 504))
async def test_5xx_without_acceptance_evidence_is_unknown(
    status_code: int,
) -> None:
    first = AuditAdapter([ProviderFailure(status_code)])
    unused = AuditAdapter([
        StreamChunk(content="must not run", finish_reason="stop"),
    ])

    with pytest.raises(ModelCallUnknownError) as caught:
        await _port([first, unused]).complete(_request(max_attempts=2))

    attempt = caught.value.attempts[0]
    assert attempt.outcome == "unknown"
    assert attempt.status_code == status_code
    assert attempt.response_started is False
    assert attempt.retry_reason is None
    assert first.close_attempts == 1
    assert unused.close_attempts == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", (502, 503, 504))
async def test_partial_response_5xx_is_unknown_and_not_retried(
    status_code: int,
) -> None:
    first = AuditAdapter([
        StreamChunk(content="partial"),
        ProviderFailure(status_code),
    ])
    unused = AuditAdapter([])

    with pytest.raises(ModelCallUnknownError) as caught:
        await _port([first, unused]).complete(_request(max_attempts=2))

    assert caught.value.attempts[0].outcome == "unknown"
    assert caught.value.attempts[0].response_started is True
    assert first.close_attempts == 1
    assert unused.close_attempts == 0


@pytest.mark.asyncio
async def test_success_is_preserved_when_close_fails() -> None:
    adapter = AuditAdapter(
        [StreamChunk(content="ok", finish_reason="stop")],
        close_behavior=_close_error,
    )

    result = await _port([adapter]).complete(_request())

    assert result.output and result.output.content == "ok"
    assert adapter.close_attempts == 1


@pytest.mark.asyncio
async def test_success_is_preserved_when_close_self_cancels() -> None:
    adapter = AuditAdapter(
        [StreamChunk(content="ok", finish_reason="stop")],
        close_behavior=_spurious_close_cancel,
    )

    result = await _port([adapter]).complete(_request())

    assert result.output and result.output.content == "ok"
    assert adapter.close_attempts == 1


@pytest.mark.asyncio
async def test_provider_error_is_preserved_when_close_fails() -> None:
    adapter = AuditAdapter(
        [ProviderFailure(400)],
        close_behavior=_close_error,
    )

    with pytest.raises(ModelProviderError) as caught:
        await _port([adapter]).complete(_request())

    assert caught.value.attempts[0].outcome == "failed"
    assert adapter.close_attempts == 1


@pytest.mark.asyncio
async def test_unknown_is_preserved_when_close_fails() -> None:
    adapter = AuditAdapter(
        [ProviderFailure(503)],
        close_behavior=_close_error,
    )

    with pytest.raises(ModelCallUnknownError) as caught:
        await _port([adapter]).complete(_request())

    assert caught.value.attempts[0].outcome == "unknown"
    assert adapter.close_attempts == 1


@pytest.mark.asyncio
async def test_cancelled_error_is_preserved_when_close_fails() -> None:
    started = asyncio.Event()

    async def block() -> None:
        started.set()
        await asyncio.sleep(10)

    adapter = AuditAdapter([block], close_behavior=_close_error)
    task = asyncio.create_task(_port([adapter]).complete(_request()))
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert adapter.close_attempts == 1


@pytest.mark.asyncio
async def test_close_timeout_is_bounded() -> None:
    async def block_close() -> None:
        await asyncio.sleep(10)

    adapter = AuditAdapter(
        [StreamChunk(content="ok", finish_reason="stop")],
        close_behavior=block_close,
    )

    result = await asyncio.wait_for(
        _port(
            [adapter],
            close_timeout_seconds=0.01,
        ).complete(_request()),
        timeout=0.2,
    )

    assert result.output and result.output.content == "ok"
    assert adapter.close_attempts == 1


@pytest.mark.asyncio
async def test_retry_continues_after_first_close_failure() -> None:
    first = AuditAdapter(
        [ProviderFailure(429)],
        close_behavior=_close_error,
    )
    second = AuditAdapter([
        StreamChunk(content="ok", finish_reason="stop"),
    ])

    result = await _port([first, second]).complete(
        _request(max_attempts=2)
    )

    assert [attempt.outcome for attempt in result.attempts] == [
        "retrying",
        "completed",
    ]
    assert first.close_attempts == second.close_attempts == 1
