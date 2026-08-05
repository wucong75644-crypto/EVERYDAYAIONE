"""Secret-free Provider stream consumption shared by Runtime and Gateway."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, AsyncIterator, Mapping

from services.agent.runtime.infrastructure.model.projection import provider_kwargs
from services.agent.runtime.infrastructure.model.response import ResponseAccumulator
from services.agent.runtime.ports.model import (
    ModelRequestOptions,
    ModelResponseStartObserver,
    ModelStepResult,
    ProviderAttemptOutcome,
    ProviderAttemptReceipt,
)


_UNKNOWN_HTTP_STATUS_CODES = frozenset({429, 502, 503, 504})


@dataclass(frozen=True)
class NormalizedStreamDelta:
    kind: str
    value: Mapping[str, object]


@dataclass(frozen=True)
class CompletedProviderStream:
    result: ModelStepResult


class ProviderStreamError(RuntimeError):
    """Stable stream failure without retaining the Provider exception."""

    def __init__(
        self, *, unknown: bool, status_code: int | None,
        response_started: bool, provider_request_id: str | None,
        error_code: str = "GATEWAY_PROVIDER_FAILED",
    ) -> None:
        self.unknown = unknown
        self.status_code = status_code
        self.response_started = response_started
        self.provider_request_id = provider_request_id
        self.error_code = error_code
        super().__init__(error_code)

    def __repr__(self) -> str:
        return f"ProviderStreamError(error_code={self.error_code!r})"


StreamUpdate = NormalizedStreamDelta | CompletedProviderStream


async def iterate_provider_stream(
    adapter: Any,
    *,
    model_step_id: str,
    provider: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    options: ModelRequestOptions,
    observer: ModelResponseStartObserver | None = None,
) -> AsyncIterator[StreamUpdate]:
    """Consume one adapter stream and expose only normalized, Secret-free state."""
    accumulator = ResponseAccumulator(model_step_id)
    provider_request_id: str | None = None
    response_seen = False
    try:
        async with asyncio.timeout(options.timeout_seconds):
            async for chunk in adapter.stream_chat(
                messages=messages,
                tools=tools,
                **provider_kwargs(options),
            ):
                response_seen = True
                if not accumulator.response_started:
                    provider_request_id = _provider_request_id(chunk)
                    accumulator.provider_request_id = provider_request_id
                    if observer is not None:
                        await _observe_response_start(
                            observer, provider, provider_request_id,
                        )
                accumulator.add(chunk)
                for delta in _normalize_chunk(chunk, provider_request_id):
                    yield delta
    except asyncio.CancelledError:
        raise
    except ProviderStreamError:
        raise
    except Exception as error:
        if type(error).__name__ == "GoogleContentFilterError":
            accumulator.finish_reason = "content_filter"
        else:
            status_code = _status_code(error)
            raise ProviderStreamError(
                unknown=(
                    _is_timeout_error(error)
                    or response_seen
                    or status_code in _UNKNOWN_HTTP_STATUS_CODES
                ),
                status_code=status_code,
                response_started=response_seen,
                provider_request_id=provider_request_id,
            ) from None
    attempts = (ProviderAttemptReceipt(
        attempt_number=1,
        provider=provider,
        outcome=ProviderAttemptOutcome.COMPLETED,
        response_started=response_seen,
        provider_request_id=provider_request_id,
    ),)
    yield CompletedProviderStream(_complete_result(
        accumulator, provider=provider, options=options, attempts=attempts,
    ))


async def _observe_response_start(
    observer: ModelResponseStartObserver,
    provider: str,
    provider_request_id: str | None,
) -> None:
    try:
        await observer.response_started(
            provider=provider,
            provider_request_id=provider_request_id,
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        raise ProviderStreamError(
            unknown=True,
            status_code=None,
            response_started=True,
            provider_request_id=provider_request_id,
            error_code="GATEWAY_RESPONSE_START_PERSIST_FAILED",
        ) from None


def _complete_result(
    accumulator: ResponseAccumulator,
    *,
    provider: str,
    options: ModelRequestOptions,
    attempts: tuple[ProviderAttemptReceipt, ...],
) -> ModelStepResult:
    stop_reason, output, calls, receipt, response_hash = accumulator.complete(
        provider=provider,
        structured_output=options.structured_output,
        schema_revision=options.response_schema_revision,
    )
    return ModelStepResult(
        stop_reason=stop_reason,
        provider_stop_reason=accumulator.finish_reason,
        response_hash=response_hash,
        response_receipt=receipt,
        output=output,
        tool_calls=calls,
        usage=accumulator.usage,
        attempts=attempts,
    )


def _normalize_chunk(
    chunk: Any,
    provider_request_id: str | None,
) -> tuple[NormalizedStreamDelta, ...]:
    deltas: list[NormalizedStreamDelta] = []
    if chunk.content:
        deltas.append(NormalizedStreamDelta("text", {"text": str(chunk.content)}))
    for call in chunk.tool_calls or ():
        value: dict[str, object] = {"index": int(call.index)}
        if call.id is not None:
            value["id"] = str(call.id)
        if call.name is not None:
            value["name"] = str(call.name)
        if call.arguments_delta is not None:
            value["arguments"] = str(call.arguments_delta)
        if len(value) > 1:
            deltas.append(NormalizedStreamDelta("tool_call", value))
    usage = {
        key: amount for key, amount in (
            ("input_tokens", _token_value(chunk, "prompt_tokens")),
            ("output_tokens", _token_value(chunk, "completion_tokens")),
            ("cache_read_tokens", _token_value(chunk, "cached_tokens")),
            ("cache_write_tokens", _token_value(chunk, "cache_creation_tokens")),
        ) if amount
    }
    if usage:
        deltas.append(NormalizedStreamDelta("usage", usage))
    metadata: dict[str, object] = {}
    if provider_request_id:
        metadata["provider_request_id"] = provider_request_id
    if chunk.finish_reason:
        metadata["finish_reason"] = str(chunk.finish_reason)
    if metadata:
        deltas.append(NormalizedStreamDelta("provider_metadata", metadata))
    return tuple(deltas)


def _provider_request_id(chunk: Any) -> str | None:
    value = getattr(chunk, "provider_request_id", None)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _status_code(error: Exception) -> int | None:
    value = getattr(error, "status_code", None)
    return value if isinstance(value, int) else None


def _is_timeout_error(error: BaseException) -> bool:
    current: BaseException | None = error
    while current is not None:
        if isinstance(current, (TimeoutError, asyncio.TimeoutError)):
            return True
        if "timeout" in type(current).__name__.lower():
            return True
        current = current.__cause__ or current.__context__
    return False


def _token_value(chunk: Any, field_name: str) -> int:
    return max(0, int(getattr(chunk, field_name, 0) or 0))


__all__ = [
    "CompletedProviderStream",
    "NormalizedStreamDelta",
    "ProviderStreamError",
    "StreamUpdate",
    "iterate_provider_stream",
]
