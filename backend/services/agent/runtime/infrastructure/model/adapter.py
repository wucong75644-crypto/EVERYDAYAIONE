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
    ModelResponseStartObserver,
    ModelStepRequest,
    ModelStepResult,
    ProviderAttemptOutcome,
    ProviderAttemptReceipt,
)


AdapterFactory = Callable[..., Any]

_UNKNOWN_HTTP_STATUS_CODES = frozenset({502, 503, 504})
_DEFAULT_CLOSE_TIMEOUT_SECONDS = 5.0


class ExistingProviderModelAdapter:
    """复用现有 factory/adapter，提供确定的逻辑 ModelStep 边界。"""

    def __init__(
        self,
        *,
        org_id: str | None = None,
        db: Any = None,
        adapter_factory: AdapterFactory | None = None,
        close_timeout_seconds: float = _DEFAULT_CLOSE_TIMEOUT_SECONDS,
    ) -> None:
        if close_timeout_seconds <= 0:
            raise ValueError("close_timeout_seconds must be positive")
        self._org_id = org_id
        self._db = db
        self._adapter_factory = adapter_factory
        self._close_timeout_seconds = close_timeout_seconds

    async def complete(
        self,
        request: ModelStepRequest,
        *,
        observer: ModelResponseStartObserver | None = None,
    ) -> ModelStepResult:
        validate_request_projection(request)
        provider = _provider_name(request.model_id)
        attempts: list[ProviderAttemptReceipt] = []
        accumulator = ResponseAccumulator(str(request.model_step_id))
        adapter = None
        provider_request_id: str | None = None
        try:
            adapter = await self._create_adapter(request)
            async with asyncio.timeout(request.options.timeout_seconds):
                messages, tools = request.context_plan.project()
                async for chunk in adapter.stream_chat(
                    messages=messages,
                    tools=tools,
                    **provider_kwargs(request.options),
                ):
                    provider_request_id = await _consume_chunk(
                        chunk=chunk, accumulator=accumulator,
                        provider_request_id=provider_request_id,
                        observer=observer, request=request, provider=provider,
                        attempts=attempts,
                    )
            attempts.append(ProviderAttemptReceipt(
                attempt_number=1,
                provider=provider,
                outcome=ProviderAttemptOutcome.COMPLETED,
                response_started=accumulator.response_started,
                provider_request_id=provider_request_id,
            ))
            return _complete_result(accumulator, request, provider, attempts)
        except asyncio.CancelledError:
            _log_cancelled(request, provider)
            raise
        except ModelCallUnknownError:
            raise
        except Exception as error:
            terminal = _semantic_terminal_result(
                error=error, accumulator=accumulator, request=request,
                provider=provider, attempts=attempts,
            )
            if terminal is not None:
                return terminal
            status_code = _status_code(error)
            unknown = (
                _is_timeout_error(error)
                or accumulator.response_started
                or status_code in _UNKNOWN_HTTP_STATUS_CODES
                or status_code == 429
            )
            attempts.append(ProviderAttemptReceipt(
                attempt_number=1,
                provider=provider,
                outcome=(
                    ProviderAttemptOutcome.UNKNOWN
                    if unknown else ProviderAttemptOutcome.FAILED
                ),
                status_code=status_code,
                response_started=accumulator.response_started,
                provider_request_id=provider_request_id,
                ambiguity_evidence=(
                    {"kind": "provider_outcome_unproven"} if unknown else None
                ),
            ))
            self._log_error(request, provider, "provider_error", error)
            error_type = ModelCallUnknownError if unknown else ModelProviderError
            raise error_type(
                "provider stream did not form a complete result",
                model_step_id=request.model_step_id,
                provider=provider,
                request_hash=request.request_hash,
                attempts=tuple(attempts),
            ) from error
        finally:
            if adapter is not None:
                await self._close_adapter(
                    adapter, request=request, provider=provider,
                )

    async def _close_adapter(
        self,
        adapter: Any,
        *,
        request: ModelStepRequest,
        provider: str,
    ) -> None:
        try:
            async with asyncio.timeout(self._close_timeout_seconds):
                await adapter.close()
        except asyncio.CancelledError as error:
            task = asyncio.current_task()
            if task is not None and task.cancelling():
                raise
            self._log_close_error(
                request,
                provider,
                "error",
                error,
            )
        except TimeoutError as error:
            self._log_close_error(
                request,
                provider,
                "timeout",
                error,
            )
        except Exception as error:
            self._log_close_error(
                request,
                provider,
                "error",
                error,
            )

    async def _create_adapter(self, request: ModelStepRequest) -> Any:
        if self._adapter_factory is not None:
            return self._adapter_factory(
                request.model_id,
                org_id=request.org_id or self._org_id,
                db=self._db,
            )
        from services.agent.runtime.credential_broker import CredentialLease

        lease = request.credential_lease
        scope = request.credential_scope
        purpose = request.credential_purpose
        if not isinstance(lease, CredentialLease) or scope is None:
            raise ValueError("RUNTIME_CREDENTIAL_LEASE_REQUIRED")
        if not isinstance(purpose, str) or not purpose.strip():
            raise ValueError("CREDENTIAL_PURPOSE_REQUIRED")
        from services.adapters.factory import create_chat_adapter

        provider = _provider_name(request.model_id)
        return await lease.use(
            scope=scope, provider=provider, revision=request.model_revision,
            purpose=purpose,
            consumer=lambda material: create_chat_adapter(
                request.model_id,
                org_id=request.org_id or self._org_id,
                db=self._db,
                api_key_override=material,
            ),
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

    @staticmethod
    def _log_close_error(
        request: ModelStepRequest,
        provider: str,
        outcome: str,
        error: BaseException,
    ) -> None:
        logger.warning(
            "model_adapter_close_failed | "
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
) -> ModelStepResult | None:
    if type(error).__name__ != "GoogleContentFilterError":
        return None
    accumulator.finish_reason = "content_filter"
    attempts.append(ProviderAttemptReceipt(
        attempt_number=1,
        provider=provider,
        outcome=ProviderAttemptOutcome.COMPLETED,
        response_started=accumulator.response_started,
    ))
    return _complete_result(accumulator, request, provider, attempts)


async def _observe_response_start(
    observer: ModelResponseStartObserver,
    request: ModelStepRequest,
    provider: str,
    provider_request_id: str | None,
    attempts: list[ProviderAttemptReceipt],
) -> None:
    try:
        await observer.response_started(
            provider=provider,
            provider_request_id=provider_request_id,
        )
    except asyncio.CancelledError:
        raise
    except Exception as error:
        attempts.append(ProviderAttemptReceipt(
            attempt_number=1,
            provider=provider,
            outcome=ProviderAttemptOutcome.UNKNOWN,
            response_started=True,
            provider_request_id=provider_request_id,
            ambiguity_evidence={
                "kind": "response_start_observer_failed",
            },
        ))
        raise ModelCallUnknownError(
            "response start persistence is ambiguous",
            model_step_id=request.model_step_id,
            provider=provider,
            request_hash=request.request_hash,
            attempts=tuple(attempts),
        ) from error


async def _consume_chunk(
    *, chunk: Any, accumulator: ResponseAccumulator,
    provider_request_id: str | None,
    observer: ModelResponseStartObserver | None,
    request: ModelStepRequest, provider: str,
    attempts: list[ProviderAttemptReceipt],
) -> str | None:
    if not accumulator.response_started:
        provider_request_id = _provider_request_id(chunk)
        accumulator.provider_request_id = provider_request_id
        if observer is not None:
            await _observe_response_start(
                observer, request, provider, provider_request_id, attempts,
            )
    accumulator.add(chunk)
    return provider_request_id


def _provider_request_id(chunk: Any) -> str | None:
    value = getattr(chunk, "provider_request_id", None)
    return value.strip() if isinstance(value, str) and value.strip() else None
