"""ModelPort 对现有 Provider adapter 与模型注册表的基础设施适配。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from loguru import logger

from services.agent.runtime.infrastructure.model.projection import (
    provider_kwargs,
    validate_request_projection,
)
from services.agent.runtime.infrastructure.model.response import (
    ResponseAccumulator,
)
from services.agent.runtime.ports.model import (
    ModelCallUnknownError,
    ModelProviderError,
    ModelStepRequest,
    ModelStepResult,
    ProviderAttemptOutcome,
    ProviderAttemptReceipt,
)


AdapterFactory = Callable[..., Any]

_SAFE_RETRY_STATUS_CODES = frozenset({429, 502, 503, 504})


class ExistingProviderModelAdapter:
    """复用现有 factory/adapter，提供确定的逻辑 ModelStep 边界。"""

    def __init__(
        self,
        *,
        org_id: str | None = None,
        db: Any = None,
        adapter_factory: AdapterFactory | None = None,
    ) -> None:
        self._org_id = org_id
        self._db = db
        self._adapter_factory = adapter_factory

    async def complete(self, request: ModelStepRequest) -> ModelStepResult:
        validate_request_projection(request)
        provider = _provider_name(request.model_id)
        attempts: list[ProviderAttemptReceipt] = []
        attempt_numbers = range(1, request.options.max_provider_attempts + 1)
        for attempt_number in attempt_numbers:
            accumulator = ResponseAccumulator(str(request.model_step_id))
            adapter = None
            try:
                adapter = self._create_adapter(request)
                async with asyncio.timeout(request.options.timeout_seconds):
                    messages, tools = request.context_plan.project()
                    async for chunk in adapter.stream_chat(
                        messages=messages,
                        tools=tools,
                        **provider_kwargs(request.options),
                    ):
                        accumulator.add(chunk)
                attempt = ProviderAttemptReceipt(
                    attempt_number=attempt_number,
                    provider=provider,
                    outcome=ProviderAttemptOutcome.COMPLETED,
                    response_started=accumulator.response_started,
                )
                attempts.append(attempt)
                return _complete_result(
                    accumulator,
                    request,
                    provider,
                    attempts,
                )
            except asyncio.CancelledError:
                _log_cancelled(request, provider)
                raise
            except TimeoutError as error:
                attempt = ProviderAttemptReceipt(
                    attempt_number=attempt_number,
                    provider=provider,
                    outcome=ProviderAttemptOutcome.UNKNOWN,
                    response_started=accumulator.response_started,
                )
                attempts.append(attempt)
                self._log_error(request, provider, "timeout_unknown", error)
                raise ModelCallUnknownError(
                    "provider timeout after dispatch",
                    model_step_id=request.model_step_id,
                    provider=provider,
                    request_hash=request.request_hash,
                    attempts=tuple(attempts),
                ) from error
            except Exception as error:
                terminal = _semantic_terminal_result(
                    error=error,
                    accumulator=accumulator,
                    request=request,
                    provider=provider,
                    attempts=attempts,
                    attempt_number=attempt_number,
                )
                if terminal is not None:
                    return terminal
                if _is_timeout_error(error):
                    attempts.append(ProviderAttemptReceipt(
                        attempt_number=attempt_number,
                        provider=provider,
                        outcome=ProviderAttemptOutcome.UNKNOWN,
                        response_started=accumulator.response_started,
                    ))
                    self._log_error(
                        request, provider, "timeout_unknown", error
                    )
                    raise ModelCallUnknownError(
                        "provider timeout after dispatch",
                        model_step_id=request.model_step_id,
                        provider=provider,
                        request_hash=request.request_hash,
                        attempts=tuple(attempts),
                    ) from error
                status_code = _status_code(error)
                can_retry = (
                    not accumulator.response_started
                    and status_code in _SAFE_RETRY_STATUS_CODES
                    and attempt_number
                    < request.options.max_provider_attempts
                )
                attempt = ProviderAttemptReceipt(
                    attempt_number=attempt_number,
                    provider=provider,
                    outcome=_attempt_error_outcome(
                        can_retry=can_retry,
                        response_started=accumulator.response_started,
                    ),
                    status_code=status_code,
                    response_started=accumulator.response_started,
                    retry_reason=(
                        f"http_{status_code}" if can_retry else None
                    ),
                )
                attempts.append(attempt)
                if can_retry:
                    continue
                self._log_error(request, provider, "provider_error", error)
                error_type = (
                    ModelCallUnknownError
                    if accumulator.response_started
                    else ModelProviderError
                )
                raise error_type(
                    "provider stream did not form a complete result",
                    model_step_id=request.model_step_id,
                    provider=provider,
                    request_hash=request.request_hash,
                    attempts=tuple(attempts),
                ) from error
            finally:
                if adapter is not None:
                    await adapter.close()
        raise AssertionError("provider attempt loop did not terminate")

    def _create_adapter(self, request: ModelStepRequest) -> Any:
        if self._adapter_factory is not None:
            return self._adapter_factory(
                request.model_id,
                org_id=self._org_id,
                db=self._db,
            )
        from services.adapters.factory import create_chat_adapter

        return create_chat_adapter(
            request.model_id,
            org_id=self._org_id,
            db=self._db,
        )

    @staticmethod
    def _log_error(
        request: ModelStepRequest,
        provider: str,
        outcome: str,
        error: Exception,
    ) -> None:
        logger.error(
            "model_call_failed | "
            f"model_step_id={request.model_step_id} | "
            f"model_id={request.model_id} | provider={provider} | "
            f"request_hash={request.request_hash} | outcome={outcome} | "
            f"error={type(error).__name__}"
        )


def _provider_name(model_id: str) -> str:
    from services.adapters.factory import get_model_config

    config = get_model_config(model_id)
    if config is None:
        raise ValueError(f"unknown model_id: {model_id}")
    return config.provider.value


def _log_cancelled(request: ModelStepRequest, provider: str) -> None:
    logger.info(
        "model_call_cancelled | "
        f"model_step_id={request.model_step_id} | "
        f"model_id={request.model_id} | provider={provider} | "
        f"request_hash={request.request_hash}"
    )


def _status_code(error: Exception) -> int | None:
    value = getattr(error, "status_code", None)
    return value if isinstance(value, int) else None


def _attempt_error_outcome(
    *,
    can_retry: bool,
    response_started: bool,
) -> ProviderAttemptOutcome:
    if can_retry:
        return ProviderAttemptOutcome.RETRYING
    if response_started:
        return ProviderAttemptOutcome.UNKNOWN
    return ProviderAttemptOutcome.FAILED


def _is_timeout_error(error: BaseException) -> bool:
    current: BaseException | None = error
    while current is not None:
        if isinstance(current, (TimeoutError, asyncio.TimeoutError)):
            return True
        if "timeout" in type(current).__name__.lower():
            return True
        current = current.__cause__ or current.__context__
    return False


def _complete_result(
    accumulator: ResponseAccumulator,
    request: ModelStepRequest,
    provider: str,
    attempts: list[ProviderAttemptReceipt],
) -> ModelStepResult:
    (
        stop_reason,
        output,
        tool_calls,
        response_receipt,
        response_hash,
    ) = accumulator.complete(
        provider=provider,
        structured_output=request.options.structured_output,
        schema_revision=request.options.response_schema_revision,
    )
    return ModelStepResult(
        stop_reason=stop_reason,
        provider_stop_reason=accumulator.finish_reason,
        response_hash=response_hash,
        response_receipt=response_receipt,
        output=output,
        tool_calls=tool_calls,
        usage=accumulator.usage,
        attempts=tuple(attempts),
    )


def _semantic_terminal_result(
    *,
    error: Exception,
    accumulator: ResponseAccumulator,
    request: ModelStepRequest,
    provider: str,
    attempts: list[ProviderAttemptReceipt],
    attempt_number: int,
) -> ModelStepResult | None:
    if type(error).__name__ != "GoogleContentFilterError":
        return None
    accumulator.finish_reason = "content_filter"
    attempts.append(ProviderAttemptReceipt(
        attempt_number=attempt_number,
        provider=provider,
        outcome=ProviderAttemptOutcome.COMPLETED,
        response_started=accumulator.response_started,
    ))
    return _complete_result(accumulator, request, provider, attempts)
