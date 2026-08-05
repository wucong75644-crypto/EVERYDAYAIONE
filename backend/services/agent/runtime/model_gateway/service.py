"""Model Gateway operation owner for the isolated BG3 process harness."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from collections.abc import AsyncIterator, Mapping
from contextlib import suppress
from typing import Any

from services.agent.runtime.infrastructure.model.stream_execution import (
    CompletedProviderStream,
    NormalizedStreamDelta,
    ProviderStreamError,
)
from services.agent.runtime.model_gateway.configuration import (
    GatewayConfigurationError,
    GatewaySecretBundleConsumer,
)
from services.agent.runtime.model_gateway.provider import (
    FailedProviderStream,
    GatewayProviderError,
    GatewayProviderExecutor,
)


class GatewayConnectionAbort(asyncio.CancelledError):
    """Close UDS without claiming a terminal DB fact exists."""


class _DispatchReadback(RuntimeError):
    pass


class ModelGatewayService:
    """Enforce claim, Secret, dispatch and finalize ordering for one process."""

    production_ready = False

    def __init__(
        self,
        repository: Any,
        secret_consumer: GatewaySecretBundleConsumer,
        provider: GatewayProviderExecutor,
        *,
        worker_id: str,
        release: str,
        lease_seconds: int = 120,
        renew_interval_seconds: float | None = None,
        clock: Any = time.monotonic,
    ) -> None:
        if not worker_id or not release or not 15 <= lease_seconds <= 600:
            raise ValueError("GATEWAY_SERVICE_CONFIGURATION_INVALID")
        self._repository = repository
        self._secrets = secret_consumer
        self._provider = provider
        self._worker_id = worker_id
        self._release = release
        self._lease_seconds = lease_seconds
        self._renew_interval_seconds = (
            min(30.0, lease_seconds / 3)
            if renew_interval_seconds is None else renew_interval_seconds
        )
        if not 0 < self._renew_interval_seconds < lease_seconds:
            raise ValueError("GATEWAY_RENEW_INTERVAL_INVALID")
        self._clock = clock
        self._draining = False
        self._in_flight = 0
        self._heartbeat = clock()

    def drain(self) -> None:
        self._draining = True

    @property
    def in_flight(self) -> int:
        return self._in_flight

    def health(self, dependencies: Mapping[str, str]) -> dict[str, object]:
        allowed = {"db", "kek", "provider_registry", "socket"}
        summary = {
            key: value for key, value in dependencies.items() if key in allowed
        }
        ready = (
            not self._draining
            and set(summary) == allowed
            and all(value == "available" for value in summary.values())
        )
        return {
            "version": "agent-model-gateway.v1",
            "release": self._release,
            "ready": ready,
            "draining": self._draining,
            "dependencies": summary,
            "in_flight": self._in_flight,
            "heartbeat": self._heartbeat,
        }

    async def complete(
        self, request: Mapping[str, Any],
    ) -> AsyncIterator[Mapping[str, Any]]:
        if self._draining:
            yield _failed("GATEWAY_DRAINING")
            return
        self._in_flight += 1
        self._heartbeat = self._clock()
        dispatched = False
        fence: dict[str, object] | None = None
        try:
            claim = await self._claim(request)
            outcome = claim.get("outcome")
            if outcome == "readback":
                operation = _mapping(claim.get("operation"))
                yield _accepted(operation, "readback")
                yield _readback_terminal(operation)
                return
            if outcome != "claimed":
                yield _failed(_claim_error_code(outcome))
                return
            operation = _mapping(claim.get("operation"))
            claim_token = claim.get("claim_token")
            if not isinstance(claim_token, str):
                raise GatewayConnectionAbort
            fence = _fence(request, operation, claim_token)
            yield _accepted(operation, "claimed")
            try:
                _validate_claim_projection(request, claim)
                options = self._provider.validate_request(request)

                async def mark_dispatched() -> None:
                    nonlocal dispatched, fence
                    result = await self._db_call(
                        self._repository.mark_dispatched(**fence)
                    )
                    result_outcome = result.get("outcome")
                    if result_outcome != "dispatching":
                        if (
                            result_outcome == "readback"
                            and _mapping(result.get("operation")).get("status")
                            == "dispatching"
                        ):
                            raise _DispatchReadback
                        raise GatewayConnectionAbort
                    operation_fact = _mapping(result.get("operation"))
                    fence["expected_state_version"] = _positive_version(
                        operation_fact.get("state_version")
                    )
                    dispatched = True

                def consume(material: str):
                    return self._provider.stream(
                        material,
                        request=request,
                        options=options,
                        before_stream=mark_dispatched,
                    )

                stream = self._secrets.consume(
                    claim.get("encrypted_configuration_bundle"),
                    provider=str(request["provider"]),
                    consumer=consume,
                )
                try:
                    while True:
                        try:
                            update = await self._next_with_renewal(stream, fence)
                        except StopAsyncIteration:
                            raise GatewayConnectionAbort from None
                        if isinstance(update, NormalizedStreamDelta):
                            yield {
                                "type": "delta",
                                "delta_kind": update.kind,
                                "delta": dict(update.value),
                            }
                            continue
                        if isinstance(update, FailedProviderStream):
                            if fence is None or not dispatched:
                                raise GatewayConnectionAbort
                            yield await self._finalize_stream_failure(
                                fence, update.error,
                            )
                            return
                        if isinstance(update, CompletedProviderStream):
                            if fence is None or not dispatched:
                                raise GatewayConnectionAbort
                            finalized = await self._finalize_completed(
                                fence, update.result,
                            )
                            yield _completed(update.result, finalized)
                            return
                finally:
                    await stream.aclose()
            except (GatewayConfigurationError, GatewayProviderError) as error:
                if dispatched or fence is None:
                    raise GatewayConnectionAbort
                await self._fail_before_dispatch(fence, error.code)
                yield _failed(error.code)
            except _DispatchReadback:
                yield _unknown("GATEWAY_DISPATCH_READBACK", False, None)
            except asyncio.CancelledError:
                if dispatched and fence is not None:
                    await self._finalize_cancelled(fence)
                raise
        finally:
            self._in_flight -= 1
            self._heartbeat = self._clock()

    async def _claim(self, request: Mapping[str, Any]) -> Mapping[str, object]:
        return await self._db_call(self._repository.claim(
            gateway_worker_id=self._worker_id,
            lease_seconds=self._lease_seconds,
            request_id=request["request_id"],
            runtime_worker_id=request["worker_id"],
            org_id=request["org_id"],
            user_id=request["user_id"],
            run_id=request["run_id"],
            model_attempt_id=request["model_attempt_id"],
            execution_token=request["execution_token"],
            request_hash=request["request_hash"],
            attempt_state_version=request["state_version"],
            model_id=request["model_id"],
            provider=request["provider"],
            provider_revision=request["model_revision"],
            model_revision=request["model_revision"],
            purpose=request["purpose"],
            tenant_kill_epoch=request["tenant_kill_epoch"],
            provider_kill_epoch=request["provider_kill_epoch"],
            capability_kill_epoch=request["capability_kill_epoch"],
        ))

    async def _fail_before_dispatch(
        self, fence: Mapping[str, object], code: str,
    ) -> None:
        result = await self._db_call(
            self._repository.fail_before_dispatch(**fence, error_code=code)
        )
        operation = _mapping(result.get("operation"))
        if (
            result.get("outcome") not in {"failed", "already_failed"}
            or operation.get("status") != "failed"
            or operation.get("terminal_error_code") != code
        ):
            raise GatewayConnectionAbort

    async def _next_with_renewal(
        self, stream: AsyncIterator[Any], fence: dict[str, object],
    ) -> Any:
        next_task = asyncio.create_task(anext(stream))
        try:
            while True:
                try:
                    return await asyncio.wait_for(
                        asyncio.shield(next_task),
                        timeout=self._renew_interval_seconds,
                    )
                except TimeoutError:
                    renewed = await self._db_call(self._repository.renew(
                        **fence, lease_seconds=self._lease_seconds,
                    ))
                    if renewed.get("outcome") != "renewed":
                        raise GatewayConnectionAbort
                    operation = _mapping(renewed.get("operation"))
                    fence["expected_state_version"] = _positive_version(
                        operation.get("state_version")
                    )
        finally:
            if not next_task.done():
                next_task.cancel()
                with suppress(asyncio.CancelledError):
                    await next_task

    async def _finalize_completed(
        self, fence: Mapping[str, object], result: Any,
    ) -> Mapping[str, object]:
        response = await self._db_call(self._repository.finalize(
            **fence,
            terminal_status="completed",
            provider_request_id=result.response_receipt.provider_request_id,
            response_started=result.attempts[0].response_started,
            response_hash=result.response_hash,
            usage_summary=_usage(result.usage, include_reasoning=True),
            terminal_error_code=None,
            ambiguity_code=None,
        ))
        operation = _mapping(response.get("operation"))
        if response.get("outcome") not in {"completed", "readback"}:
            raise GatewayConnectionAbort
        if operation.get("status") != "completed":
            raise GatewayConnectionAbort
        return operation

    async def _finalize_stream_failure(
        self, fence: Mapping[str, object], error: ProviderStreamError,
    ) -> Mapping[str, object]:
        status = "unknown" if error.unknown else "failed"
        code = (
            "GATEWAY_PROVIDER_OUTCOME_UNKNOWN"
            if error.unknown else "GATEWAY_PROVIDER_FAILED"
        )
        response = await self._db_call(self._repository.finalize(
            **fence,
            terminal_status=status,
            provider_request_id=error.provider_request_id,
            response_started=error.response_started,
            response_hash=None,
            usage_summary={},
            terminal_error_code=code if status == "failed" else None,
            ambiguity_code=code if status == "unknown" else None,
        ))
        operation = _mapping(response.get("operation"))
        if response.get("outcome") not in {status, "readback"}:
            raise GatewayConnectionAbort
        if operation.get("status") != status:
            raise GatewayConnectionAbort
        if status == "unknown":
            return _unknown(
                code, error.response_started, error.provider_request_id,
            )
        return _failed(code)

    async def _finalize_cancelled(self, fence: Mapping[str, object]) -> None:
        try:
            await asyncio.shield(self._db_call(self._repository.finalize(
                **fence,
                terminal_status="unknown",
                provider_request_id=None,
                response_started=False,
                response_hash=None,
                usage_summary={},
                terminal_error_code=None,
                ambiguity_code="GATEWAY_CANCELLED_AFTER_DISPATCH",
            )))
        except Exception:
            pass

    @staticmethod
    async def _db_call(awaitable: Any) -> Mapping[str, object]:
        try:
            result = await awaitable
        except asyncio.CancelledError:
            raise
        except Exception:
            raise GatewayConnectionAbort from None
        if not isinstance(result, Mapping):
            raise GatewayConnectionAbort
        return result


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise GatewayConnectionAbort
    return value


def _positive_version(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise GatewayConnectionAbort
    return value


def _fence(
    request: Mapping[str, Any], operation: Mapping[str, object], claim_token: str,
) -> dict[str, object]:
    return {
        "operation_id": operation.get("operation_id"),
        "claim_token": claim_token,
        "expected_state_version": _positive_version(operation.get("state_version")),
        "org_id": request["org_id"],
        "execution_token": request["execution_token"],
        "request_hash": request["request_hash"],
        "provider_revision": request["model_revision"],
        "tenant_kill_epoch": request["tenant_kill_epoch"],
        "provider_kill_epoch": request["provider_kill_epoch"],
        "capability_kill_epoch": request["capability_kill_epoch"],
    }


def _validate_claim_projection(
    request: Mapping[str, Any], claim: Mapping[str, object],
) -> None:
    receipt = _mapping(claim.get("input_receipt"))
    input_value = request["input"]
    digest = hashlib.sha256(json.dumps(
        {"messages": input_value["messages"], "tools": input_value["tools"]},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    valid = (
        hmac.compare_digest(str(receipt.get("request_hash") or ""), request["request_hash"])
        and hmac.compare_digest(str(receipt.get("prefix_hash") or ""), digest)
        and hmac.compare_digest(input_value["context_receipt_hash"], digest)
        and receipt.get("message_count") == len(input_value["messages"])
        and receipt.get("tool_count") == len(input_value["tools"])
    )
    if not valid:
        raise GatewayConfigurationError("GATEWAY_CONFIGURATION_INVALID")


def _accepted(operation: Mapping[str, object], status: str) -> dict[str, object]:
    operation_id = operation.get("operation_id")
    if not isinstance(operation_id, str):
        raise GatewayConnectionAbort
    return {"type": "accepted", "operation_id": operation_id, "status": status}


def _failed(code: str) -> dict[str, object]:
    return {
        "type": "failed",
        "error_code": code,
        "retry_class": "terminal",
        "summary": "gateway request failed",
    }


def _unknown(
    code: str, response_started: bool, provider_request_id: str | None,
) -> dict[str, object]:
    return {
        "type": "unknown",
        "ambiguity_kind": code,
        "response_started": response_started,
        "provider_request_id": provider_request_id,
        "reconcile_only": True,
    }


def _completed(result: Any, operation: Mapping[str, object]) -> dict[str, object]:
    return {
        "type": "completed",
        "text": result.output.content if result.output is not None else "",
        "tool_calls": [{
            "index": call.index,
            "id": call.call_id,
            "name": call.name,
            "arguments": call.arguments_json,
        } for call in result.tool_calls],
        "usage": _usage(result.usage),
        "finish_reason": result.provider_stop_reason or "unknown",
        "provider_request_id": result.response_receipt.provider_request_id,
        "response_hash": result.response_hash,
        "operation_state_version": _positive_version(operation.get("state_version")),
    }


def _readback_terminal(operation: Mapping[str, object]) -> dict[str, object]:
    status = operation.get("status")
    if status == "failed":
        return _failed(str(operation.get("terminal_error_code") or "GATEWAY_FAILED"))
    if status == "unknown":
        return _unknown(
            str(operation.get("ambiguity_code") or "GATEWAY_OUTCOME_UNKNOWN"),
            bool(operation.get("response_started")),
            operation.get("provider_request_id")
            if isinstance(operation.get("provider_request_id"), str) else None,
        )
    code = (
        "GATEWAY_COMPLETED_READBACK_ONLY"
        if status == "completed" else "GATEWAY_DISPATCH_READBACK"
    )
    return _unknown(
        code,
        bool(operation.get("response_started")),
        operation.get("provider_request_id")
        if isinstance(operation.get("provider_request_id"), str) else None,
    )


def _claim_error_code(outcome: object) -> str:
    return {
        "busy": "GATEWAY_OPERATION_BUSY",
        "fenced": "GATEWAY_OPERATION_FENCED",
        "not_found": "GATEWAY_OPERATION_NOT_FOUND",
    }.get(str(outcome), "GATEWAY_CLAIM_FAILED")


def _usage(value: Any, *, include_reasoning: bool = False) -> dict[str, int]:
    result = {
        "input_tokens": value.input_tokens,
        "output_tokens": value.output_tokens,
        "cache_read_tokens": value.cache_read_tokens,
        "cache_write_tokens": value.cache_write_tokens,
    }
    if include_reasoning:
        result["reasoning_tokens"] = value.reasoning_tokens
    return result


__all__ = ["GatewayConnectionAbort", "ModelGatewayService"]
