"""Gateway-only Provider construction and Secret-free stream execution."""

from __future__ import annotations

import asyncio
import hmac
from dataclasses import dataclass
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from typing import Any

from services.agent.runtime.infrastructure.model.projection import resolve_model_revision
from services.agent.runtime.infrastructure.model.runtime_adapter_factory import (
    create_runtime_chat_adapter,
)
from services.agent.runtime.infrastructure.model.stream_execution import (
    ProviderStreamError,
    StreamUpdate,
    iterate_provider_stream,
)
from services.agent.runtime.ports.model import ModelRequestOptions


AdapterBuilder = Callable[..., Any]
BeforeStream = Callable[[], Awaitable[None]]
_OPTION_FIELDS = frozenset({
    "temperature", "reasoning_effort", "thinking_mode",
    "structured_output", "response_schema_revision", "timeout_seconds",
    "max_provider_attempts",
})


class GatewayProviderError(RuntimeError):
    """Stable pre-dispatch Provider registry or builder failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)

    def __repr__(self) -> str:
        return f"GatewayProviderError(code={self.code!r})"


@dataclass(frozen=True)
class FailedProviderStream:
    error: ProviderStreamError


class GatewayProviderExecutor:
    """Construct, dispatch and close exactly one credential-bound adapter."""

    def __init__(
        self,
        builder: AdapterBuilder = create_runtime_chat_adapter,
        *,
        close_timeout_seconds: float = 5.0,
    ) -> None:
        if close_timeout_seconds <= 0:
            raise ValueError("GATEWAY_CLOSE_TIMEOUT_INVALID")
        self._builder = builder
        self._close_timeout_seconds = close_timeout_seconds

    def validate_request(self, request: Mapping[str, Any]) -> ModelRequestOptions:
        model_id = str(request["model_id"])
        provider = str(request["provider"])
        revision = str(request["model_revision"])
        try:
            from services.adapters.factory import get_model_config

            config = get_model_config(model_id)
            actual_revision = resolve_model_revision(model_id)
        except Exception:
            raise GatewayProviderError("GATEWAY_PROVIDER_UNSUPPORTED") from None
        if (
            config is None
            or config.provider.value != provider
            or not hmac.compare_digest(revision, actual_revision)
        ):
            raise GatewayProviderError("GATEWAY_PROVIDER_UNSUPPORTED")
        return _parse_options(
            request["input"]["options"],
            deadline_ms=int(request["deadline_ms"]),
        )

    async def stream(
        self,
        material: str,
        *,
        request: Mapping[str, Any],
        options: ModelRequestOptions,
        before_stream: BeforeStream,
    ) -> AsyncIterator[StreamUpdate | FailedProviderStream]:
        adapter = None
        try:
            try:
                adapter = self._builder(
                    str(request["model_id"]),
                    api_key=material,
                    stream_timeout=options.timeout_seconds,
                )
            except Exception:
                raise GatewayProviderError("GATEWAY_PROVIDER_BUILD_FAILED") from None
            await before_stream()
            try:
                async for update in iterate_provider_stream(
                    adapter,
                    model_step_id=str(request["model_step_id"]),
                    provider=str(request["provider"]),
                    messages=list(request["input"]["messages"]),
                    tools=list(request["input"]["tools"]),
                    options=options,
                ):
                    yield update
            except ProviderStreamError as error:
                yield FailedProviderStream(error)
        finally:
            if adapter is not None:
                await self._close(adapter)

    async def _close(self, adapter: Any) -> None:
        try:
            async with asyncio.timeout(self._close_timeout_seconds):
                await adapter.close()
        except asyncio.CancelledError:
            task = asyncio.current_task()
            if task is not None and task.cancelling():
                close_task = asyncio.create_task(adapter.close())
                try:
                    await asyncio.shield(close_task)
                except Exception:
                    pass
                raise
        except Exception:
            pass


def _parse_options(value: object, *, deadline_ms: int) -> ModelRequestOptions:
    if not isinstance(value, dict) or not set(value) <= _OPTION_FIELDS:
        raise GatewayProviderError("GATEWAY_CONFIGURATION_INVALID")
    values = dict(value)
    timeout = values.pop("timeout_seconds", deadline_ms / 1000)
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool):
        raise GatewayProviderError("GATEWAY_CONFIGURATION_INVALID")
    values["timeout_seconds"] = min(float(timeout), deadline_ms / 1000, 120.0)
    values.setdefault("max_provider_attempts", 1)
    if values["max_provider_attempts"] != 1:
        raise GatewayProviderError("GATEWAY_CONFIGURATION_INVALID")
    try:
        return ModelRequestOptions(**values)
    except (TypeError, ValueError):
        raise GatewayProviderError("GATEWAY_CONFIGURATION_INVALID") from None


def provider_registry_available() -> bool:
    try:
        from services.adapters.factory import get_all_models

        models = get_all_models()
    except Exception:
        return False
    return bool(models) and all(
        getattr(config.provider, "value", "") in {
            "dashscope", "openrouter", "kie", "google",
        }
        for config in models.values()
    )


__all__ = [
    "FailedProviderStream",
    "GatewayProviderError",
    "GatewayProviderExecutor",
    "provider_registry_available",
]
