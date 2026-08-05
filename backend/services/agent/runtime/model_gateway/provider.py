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
        close_task = asyncio.create_task(
            adapter.close(), name="gateway-provider-adapter-close",
        )
        try:
            await asyncio.wait_for(
                asyncio.shield(close_task), timeout=self._close_timeout_seconds,
            )
        except asyncio.CancelledError:
            await self._settle_cancelled_close(close_task)
            raise
        except Exception:
            close_task.cancel()
            await asyncio.gather(close_task, return_exceptions=True)

    async def _settle_cancelled_close(self, close_task: asyncio.Task[Any]) -> None:
        try:
            await asyncio.wait_for(
                asyncio.shield(close_task), timeout=self._close_timeout_seconds,
            )
        except (asyncio.CancelledError, Exception):
            close_task.cancel()
        await asyncio.gather(close_task, return_exceptions=True)


def validate_durable_operation(
    request: Mapping[str, Any], operation: Mapping[str, object],
) -> str:
    bindings = {
        key: key for key in (
            "request_id", "org_id", "user_id", "run_id", "model_step_id",
            "model_attempt_id", "execution_token", "request_hash", "model_id",
            "provider", "model_revision", "purpose", "tenant_kill_epoch",
            "provider_kill_epoch", "capability_kill_epoch",
        )
    }
    bindings["attempt_state_version"] = "state_version"
    if any(operation.get(field) != request[source] for field, source in bindings.items()):
        raise GatewayProviderError("GATEWAY_OPERATION_BINDING_INVALID")
    revision = operation.get("provider_revision")
    operation_id = operation.get("operation_id")
    if not isinstance(revision, str) or not revision or not isinstance(operation_id, str):
        raise GatewayProviderError("GATEWAY_OPERATION_BINDING_INVALID")
    return revision


def uds_usage_projection(value: Any) -> dict[str, int]:
    return {
        "input_tokens": max(0, int(value.input_tokens)),
        "output_tokens": max(0, int(value.output_tokens)),
        "cache_read_tokens": max(0, int(value.cache_read_tokens)),
        "cache_write_tokens": max(0, int(value.cache_write_tokens)),
    }


def db_usage_projection(value: Any) -> dict[str, int]:
    input_tokens = max(0, int(value.input_tokens))
    output_tokens = max(0, int(value.output_tokens))
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "reasoning_tokens": max(0, int(value.reasoning_tokens)),
        # Reasoning is an output breakdown, so it is not added a second time.
        "total_tokens": input_tokens + output_tokens,
    }


def completed_frame(result: Any, operation_state_version: int) -> dict[str, object]:
    return {
        "type": "completed",
        "text": result.output.content if result.output is not None else "",
        "tool_calls": [{
            "index": call.index,
            "id": call.call_id,
            "name": call.name,
            "arguments": call.arguments_json,
        } for call in result.tool_calls],
        "usage": uds_usage_projection(result.usage),
        "finish_reason": result.provider_stop_reason or "unknown",
        "provider_request_id": result.response_receipt.provider_request_id,
        "response_hash": result.response_hash,
        "operation_state_version": operation_state_version,
    }


def claim_error_code(outcome: object) -> str:
    return {
        "fenced": "GATEWAY_OPERATION_FENCED",
        "not_found": "GATEWAY_OPERATION_NOT_FOUND",
    }.get(str(outcome), "GATEWAY_CLAIM_FAILED")


def read_error_code(outcome: object) -> str:
    return {
        "fenced": "GATEWAY_OPERATION_FENCED",
        "not_found": "GATEWAY_OPERATION_NOT_FOUND",
    }.get(str(outcome), "GATEWAY_OPERATION_READ_FAILED")


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
    "claim_error_code",
    "completed_frame",
    "db_usage_projection",
    "FailedProviderStream",
    "GatewayProviderError",
    "GatewayProviderExecutor",
    "provider_registry_available",
    "read_error_code",
    "uds_usage_projection",
    "validate_durable_operation",
]
