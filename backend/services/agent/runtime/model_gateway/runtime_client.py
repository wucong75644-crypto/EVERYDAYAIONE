"""Production Runtime ModelPort backed only by the local Model Gateway UDS."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from services.agent.runtime.domain import StopReason
from services.agent.runtime.infrastructure.model.response import canonical_response_hash
from services.agent.runtime.ports.model import (
    ModelCallUnknownError,
    ModelOutput,
    ModelOutputKind,
    ModelProviderError,
    ModelResponseReceipt,
    ModelResponseStartObserver,
    ModelStepRequest,
    ModelStepResult,
    ModelToolCall,
    ModelUsage,
    ProviderAttemptOutcome,
    ProviderAttemptReceipt,
)
from services.agent.runtime.ports.model_gateway import ModelGatewayDispatchBinding

from .client import IsolatedModelGatewayClient
from .protocol import GatewayProtocolError, VERSION, validate_response


class ModelGatewayClient:
    """Map one durable Runtime dispatch to one strict UDS exchange."""

    requires_gateway_dispatch = True
    production_ready = False

    def __init__(
        self, socket_path: str, repository: object, *,
        connect_timeout: float = 2.0, first_frame_timeout: float = 10.0,
    ) -> None:
        if not socket_path or not Path(socket_path).is_absolute():
            raise ValueError("MODEL_GATEWAY_SOCKET_REQUIRED")
        if repository is None or not callable(getattr(repository, "read", None)):
            raise ValueError("MODEL_GATEWAY_REPOSITORY_REQUIRED")
        self._repository = repository
        self._transport = IsolatedModelGatewayClient(
            socket_path, connect_timeout=connect_timeout,
            first_frame_timeout=first_frame_timeout,
        )

    async def complete(
        self, request: ModelStepRequest, *,
        observer: ModelResponseStartObserver | None = None,
    ) -> ModelStepResult:
        binding = _require_binding(request)
        terminal: Mapping[str, Any] | None = None
        response_started = False
        notified_provider_id: str | None = None
        sequence = 0
        try:
            async for frame in self._transport.complete(
                _gateway_request(request, binding),
            ):
                frame = validate_response(
                    dict(frame), request_id=binding.request_id,
                    expected_sequence=sequence,
                )
                sequence += 1
                if frame["type"] == "accepted":
                    if frame["operation_id"] != binding.operation_id:
                        raise GatewayProtocolError("GATEWAY_OPERATION_ID_MISMATCH")
                    continue
                if frame["type"] == "delta":
                    candidate = _frame_provider_id(frame)
                    if not response_started or (
                        candidate is not None and candidate != notified_provider_id
                    ):
                        await _notify_started(
                            observer, binding.provider,
                            {"provider_request_id": candidate},
                        )
                        response_started = True
                        notified_provider_id = candidate or notified_provider_id
                if frame["type"] in {"completed", "failed", "unknown"}:
                    terminal = frame
        except asyncio.CancelledError:
            raise
        except Exception:
            return await self._readback_result(
                request, binding, None, observer, response_started,
                notified_provider_id,
            )
        return await self._readback_result(
            request, binding, terminal, observer, response_started,
            notified_provider_id,
        )

    async def _readback_result(
        self, request: ModelStepRequest, binding: ModelGatewayDispatchBinding,
        terminal: Mapping[str, Any] | None,
        observer: ModelResponseStartObserver | None, response_started: bool,
        notified_provider_id: str | None,
    ) -> ModelStepResult:
        try:
            readback = await self._repository.read(**_read_binding(binding))
            operation = _validated_operation(readback, binding)
        except asyncio.CancelledError:
            raise
        except Exception:
            raise _unknown(request, binding, "gateway_readback_unavailable") from None
        operation_provider_id = _optional_text(operation.get("provider_request_id"))
        should_notify = bool(operation.get("response_started")) and (
            not response_started or (
                operation_provider_id is not None
                and operation_provider_id != notified_provider_id
            )
        )
        if should_notify:
            try:
                await _notify_started(
                    observer, binding.provider,
                    {"provider_request_id": operation.get("provider_request_id")},
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                raise _unknown(
                    request, binding, "response_start_persist_failed", operation,
                ) from None
        status = operation.get("status")
        if status == "failed":
            raise _failed(request, binding, operation)
        if status != "completed" or terminal is None or terminal.get("type") != "completed":
            raise _unknown(request, binding, str(
                operation.get("ambiguity_code") or "gateway_outcome_unproven",
            ), operation)
        try:
            return _completed_result(request, binding, terminal, operation)
        except (KeyError, TypeError, ValueError, GatewayProtocolError):
            raise _unknown(
                request, binding, "gateway_completed_projection_invalid",
                operation,
            ) from None


def _gateway_request(
    request: ModelStepRequest, binding: ModelGatewayDispatchBinding,
) -> dict[str, object]:
    messages, tools = request.context_plan.project()
    context_hash = hashlib.sha256(json.dumps(
        {"messages": messages, "tools": tools}, ensure_ascii=False,
        sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    return {
        "version": VERSION, "type": "request", "operation": "model.complete",
        "request_id": binding.request_id, "org_id": binding.org_id,
        "user_id": binding.user_id, "run_id": binding.run_id,
        "model_step_id": binding.model_step_id,
        "model_attempt_id": binding.model_attempt_id,
        "worker_id": binding.worker_id,
        "execution_token": binding.execution_token,
        "request_hash": binding.request_hash,
        "state_version": binding.attempt_state_version,
        "model_id": binding.model_id, "provider": binding.provider,
        "model_revision": binding.model_revision, "purpose": binding.purpose,
        "tenant_kill_epoch": binding.tenant_kill_epoch,
        "provider_kill_epoch": binding.provider_kill_epoch,
        "capability_kill_epoch": binding.capability_kill_epoch,
        "deadline_ms": min(120_000, max(1, int(request.options.timeout_seconds * 1000))),
        "input": {
            "messages": messages, "tools": tools,
            "options": asdict(request.options),
            "context_receipt_hash": context_hash,
        },
    }


def _require_binding(request: ModelStepRequest) -> ModelGatewayDispatchBinding:
    binding = request.gateway_binding
    if binding is None:
        attempt = ProviderAttemptReceipt(
            attempt_number=1, provider="model-gateway",
            outcome=ProviderAttemptOutcome.FAILED,
        )
        raise ModelProviderError(
            "MODEL_GATEWAY_DISPATCH_BINDING_REQUIRED",
            model_step_id=request.model_step_id, provider="model-gateway",
            request_hash=request.request_hash, attempts=(attempt,),
        )
    expected = {
        "model_step_id": str(request.model_step_id),
        "model_id": request.model_id,
        "model_revision": request.model_revision,
        "request_hash": request.request_hash,
        "org_id": request.org_id,
    }
    if any(getattr(binding, key) != value for key, value in expected.items()):
        raise _unknown(request, binding, "gateway_dispatch_binding_mismatch")
    return binding


def _read_binding(binding: ModelGatewayDispatchBinding) -> dict[str, object]:
    return {
        "request_id": binding.request_id, "org_id": binding.org_id,
        "user_id": binding.user_id, "run_id": binding.run_id,
        "model_attempt_id": binding.model_attempt_id,
        "execution_token": binding.execution_token,
        "request_hash": binding.request_hash,
    }


def _validated_operation(
    readback: object, binding: ModelGatewayDispatchBinding,
) -> Mapping[str, object]:
    if not isinstance(readback, Mapping) or readback.get("outcome") != "found":
        raise GatewayProtocolError("GATEWAY_OPERATION_READ_FAILED")
    operation = readback.get("operation")
    if not isinstance(operation, Mapping):
        raise GatewayProtocolError("GATEWAY_OPERATION_READ_INVALID")
    fields = {
        "operation_id": binding.operation_id, "request_id": binding.request_id,
        "org_id": binding.org_id, "user_id": binding.user_id,
        "session_id": binding.session_id, "run_id": binding.run_id,
        "model_step_id": binding.model_step_id,
        "model_attempt_id": binding.model_attempt_id,
        "execution_token": binding.execution_token,
        "request_hash": binding.request_hash, "model_id": binding.model_id,
        "provider": binding.provider, "provider_revision": binding.provider_revision,
        "model_revision": binding.model_revision, "purpose": binding.purpose,
        "tenant_kill_epoch": binding.tenant_kill_epoch,
        "provider_kill_epoch": binding.provider_kill_epoch,
        "capability_kill_epoch": binding.capability_kill_epoch,
        "attempt_state_version": binding.attempt_state_version,
    }
    if any(operation.get(key) != value for key, value in fields.items()):
        raise GatewayProtocolError("GATEWAY_OPERATION_BINDING_MISMATCH")
    return operation


def _completed_result(
    request: ModelStepRequest, binding: ModelGatewayDispatchBinding,
    frame: Mapping[str, Any], operation: Mapping[str, object],
) -> ModelStepResult:
    if frame["operation_state_version"] != operation.get("state_version"):
        raise GatewayProtocolError("GATEWAY_COMPLETED_FACT_MISMATCH")
    db_usage = operation.get("usage_summary")
    if not isinstance(db_usage, Mapping):
        raise GatewayProtocolError("GATEWAY_COMPLETED_USAGE_MISMATCH")
    wire_usage = frame["usage"]
    db_input = _usage_amount(db_usage, "input_tokens")
    db_output = _usage_amount(db_usage, "output_tokens")
    db_reasoning = _usage_amount(db_usage, "reasoning_tokens")
    if (wire_usage.get("input_tokens", 0) != db_input
            or wire_usage.get("output_tokens", 0) != db_output
            or _usage_amount(db_usage, "total_tokens") != db_input + db_output):
        raise GatewayProtocolError("GATEWAY_COMPLETED_USAGE_MISMATCH")
    if frame["provider_request_id"] != operation.get("provider_request_id"):
        raise GatewayProtocolError("GATEWAY_COMPLETED_PROVIDER_ID_MISMATCH")
    usage = ModelUsage(
        input_tokens=wire_usage.get("input_tokens", 0),
        output_tokens=wire_usage.get("output_tokens", 0),
        reasoning_tokens=db_reasoning,
        cache_read_tokens=wire_usage.get("cache_read_tokens", 0),
        cache_write_tokens=wire_usage.get("cache_write_tokens", 0),
    )
    calls = tuple(ModelToolCall(
        index=call["index"], call_id=call["call_id"], name=call["name"],
        arguments_json=call["arguments"], provider_call_id=call["provider_call_id"],
    ) for call in frame["tool_calls"])
    output = _output(request, frame["text"])
    stop = StopReason(frame["stop_reason"])
    response_hash = canonical_response_hash(
        stop_reason=stop, provider_stop_reason=frame["provider_stop_reason"],
        output=output, tool_calls=calls, usage=usage,
    )
    if (response_hash != frame["response_hash"]
            or response_hash != operation.get("response_hash")):
        raise GatewayProtocolError("GATEWAY_COMPLETED_HASH_MISMATCH")
    receipt = ModelResponseReceipt(
        output_kind=output.kind if output else None,
        output_characters=len(output.content) if output else 0,
        tool_call_count=len(calls), invalid_tool_call_count=0, usage=usage,
        provider=binding.provider,
        provider_request_id=frame["provider_request_id"],
    )
    return ModelStepResult(
        stop_reason=stop, provider_stop_reason=frame["provider_stop_reason"],
        response_hash=response_hash, response_receipt=receipt,
        output=output, tool_calls=calls, usage=usage,
        attempts=(ProviderAttemptReceipt(
            attempt_number=1, provider=binding.provider,
            outcome=ProviderAttemptOutcome.COMPLETED, response_started=True,
            provider_request_id=frame["provider_request_id"],
        ),),
    )


def _output(request: ModelStepRequest, text: str) -> ModelOutput | None:
    if not text.strip():
        return None
    if not request.options.structured_output:
        return ModelOutput(kind=ModelOutputKind.TEXT, content=text)
    canonical = json.dumps(json.loads(text), ensure_ascii=False,
                           sort_keys=True, separators=(",", ":"))
    return ModelOutput(
        kind=ModelOutputKind.STRUCTURED, content=canonical,
        schema_revision=request.options.response_schema_revision,
    )


def _failed(
    request: ModelStepRequest, binding: ModelGatewayDispatchBinding,
    operation: Mapping[str, object],
) -> ModelProviderError:
    code = operation.get("terminal_error_code")
    if not isinstance(code, str) or not code:
        return _unknown(
            request, binding, "gateway_failed_fact_invalid", operation,
        )
    attempt = ProviderAttemptReceipt(
        attempt_number=1, provider=binding.provider,
        outcome=ProviderAttemptOutcome.FAILED,
        response_started=bool(operation.get("response_started")),
        provider_request_id=_optional_text(operation.get("provider_request_id")),
        retry_reason=code,
    )
    return ModelProviderError(
        code, model_step_id=request.model_step_id, provider=binding.provider,
        request_hash=request.request_hash, attempts=(attempt,),
    )


def _unknown(
    request: ModelStepRequest, binding: ModelGatewayDispatchBinding, kind: str,
    operation: Mapping[str, object] | None = None,
) -> ModelCallUnknownError:
    operation = operation or {}
    return ModelCallUnknownError(
        "MODEL_GATEWAY_OUTCOME_UNKNOWN",
        model_step_id=request.model_step_id, provider=binding.provider,
        request_hash=request.request_hash,
        attempts=(ProviderAttemptReceipt(
            attempt_number=1, provider=binding.provider,
            outcome=ProviderAttemptOutcome.UNKNOWN,
            response_started=bool(operation.get("response_started")),
            provider_request_id=_optional_text(operation.get("provider_request_id")),
            ambiguity_evidence={"kind": kind[:128]},
        ),),
    )


async def _notify_started(
    observer: ModelResponseStartObserver | None, provider: str,
    evidence: Mapping[str, object],
) -> None:
    if observer is not None:
        await observer.response_started(
            provider=provider,
            provider_request_id=_optional_text(evidence.get("provider_request_id")),
        )


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _usage_amount(usage: Mapping[str, object], field: str) -> int:
    value = usage.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise GatewayProtocolError("GATEWAY_COMPLETED_USAGE_MISMATCH")
    return value


def _frame_provider_id(frame: Mapping[str, object]) -> str | None:
    if frame.get("delta_kind") != "provider_metadata":
        return None
    delta = frame.get("delta")
    return _optional_text(
        delta.get("provider_request_id") if isinstance(delta, Mapping) else None,
    )


__all__ = ["ModelGatewayClient"]
