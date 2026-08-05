"""ModelPort 对现有 Provider adapter 与模型注册表的基础设施适配。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from loguru import logger

from services.agent.runtime.infrastructure.model.projection import (
    validate_request_projection,
)
from services.agent.runtime.infrastructure.model.stream_execution import (
    CompletedProviderStream,
    ProviderStreamError,
    iterate_provider_stream,
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
        adapter = None
        try:
            adapter = await self._create_adapter(request)
            messages, tools = request.context_plan.project()
            async for update in iterate_provider_stream(
                adapter,
                model_step_id=str(request.model_step_id),
                provider=provider,
                messages=messages,
                tools=tools,
                options=request.options,
                observer=observer,
            ):
                if isinstance(update, CompletedProviderStream):
                    return update.result
            raise RuntimeError("PROVIDER_STREAM_TERMINAL_MISSING")
        except asyncio.CancelledError:
            _log_cancelled(request, provider)
            raise
        except ModelCallUnknownError:
            raise
        except ProviderStreamError as error:
            attempts = (ProviderAttemptReceipt(
                attempt_number=1,
                provider=provider,
                outcome=(
                    ProviderAttemptOutcome.UNKNOWN
                    if error.unknown else ProviderAttemptOutcome.FAILED
                ),
                status_code=error.status_code,
                response_started=error.response_started,
                provider_request_id=error.provider_request_id,
                ambiguity_evidence=(
                    {"kind": (
                        "response_start_observer_failed"
                        if error.error_code
                        == "GATEWAY_RESPONSE_START_PERSIST_FAILED"
                        else "provider_outcome_unproven"
                    )}
                    if error.unknown else None
                ),
            ),)
            self._log_error(request, provider, "provider_error", error)
            error_type = ModelCallUnknownError if error.unknown else ModelProviderError
            raise error_type(
                "provider stream did not form a complete result",
                model_step_id=request.model_step_id,
                provider=provider,
                request_hash=request.request_hash,
                attempts=attempts,
            ) from None
        except Exception as error:
            attempts = (ProviderAttemptReceipt(
                attempt_number=1,
                provider=provider,
                outcome=ProviderAttemptOutcome.FAILED,
            ),)
            self._log_error(request, provider, "provider_error", error)
            raise ModelProviderError(
                "provider stream did not form a complete result",
                model_step_id=request.model_step_id,
                provider=provider,
                request_hash=request.request_hash,
                attempts=attempts,
            ) from None
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
        if self._adapter_factory is None:
            raise ValueError("RUNTIME_MODEL_GATEWAY_REQUIRED")
        return self._adapter_factory(
            request.model_id,
            org_id=request.org_id or self._org_id,
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
