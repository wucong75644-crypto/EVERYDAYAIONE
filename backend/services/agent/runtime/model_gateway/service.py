"""Model Gateway operation owner for the isolated BG3 process harness."""

from __future__ import annotations

import asyncio
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
    validate_claim_projection,
)
from services.agent.runtime.model_gateway.provider import (
    claim_error_code,
    completed_frame,
    db_usage_projection,
    FailedProviderStream,
    GatewayProviderError,
    GatewayProviderExecutor,
    read_error_code,
    validate_durable_operation,
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
        evidence: dict[str, object] = {
            "response_started": False, "provider_request_id": None,
        }
        fence: dict[str, object] | None = None
        try:
            read = await self._read(request)
            if read.get("outcome") != "found":
                yield _failed(read_error_code(read.get("outcome")))
                return
            durable_operation = _mapping(read.get("operation"))
            provider_revision = validate_durable_operation(
                request, durable_operation,
            )
            claim = await self._claim(request, provider_revision)
            outcome = claim.get("outcome")
            if outcome == "busy":
                operation = _mapping(claim.get("operation"))
                if validate_durable_operation(request, operation) != provider_revision:
                    raise GatewayConnectionAbort
                yield _accepted(operation, "readback")
                yield _unknown(
                    "GATEWAY_OPERATION_IN_FLIGHT",
                    bool(operation.get("response_started")),
                    _provider_request_id(operation),
                )
                return
            if outcome == "readback":
                operation = _mapping(claim.get("operation"))
                if validate_durable_operation(request, operation) != provider_revision:
                    raise GatewayConnectionAbort
                yield _accepted(operation, "readback")
                yield _readback_terminal(operation)
                return
            if outcome != "claimed":
                yield _failed(claim_error_code(outcome))
                return
            operation = _mapping(claim.get("operation"))
            if validate_durable_operation(request, operation) != provider_revision:
                raise GatewayConnectionAbort
            claim_token = claim.get("claim_token")
            if not isinstance(claim_token, str):
                raise GatewayConnectionAbort
            fence = _fence(operation, claim_token)
            yield _accepted(operation, "claimed")
            try:
                validate_claim_projection(request, claim)
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
                    async for frame in self._stream_frames(
                        stream, fence, evidence,
                    ):
                        yield frame
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
                    await self._finalize_cancelled(
                        fence,
                        response_started=bool(evidence["response_started"]) or dispatched,
                        provider_request_id=_evidence_provider_id(evidence),
                    )
                raise
        except GatewayProviderError:
            raise GatewayConnectionAbort from None
        finally:
            self._in_flight -= 1
            self._heartbeat = self._clock()

    async def _stream_frames(
        self, stream: AsyncIterator[Any], fence: dict[str, object],
        evidence: dict[str, object],
    ) -> AsyncIterator[Mapping[str, Any]]:
        while True:
            try:
                update = await self._next_with_renewal(stream, fence)
            except StopAsyncIteration:
                raise GatewayConnectionAbort from None
            if isinstance(update, NormalizedStreamDelta):
                evidence["response_started"] = True
                candidate_id = update.value.get("provider_request_id")
                if isinstance(candidate_id, str) and candidate_id:
                    evidence["provider_request_id"] = candidate_id
                yield {
                    "type": "delta", "delta_kind": update.kind,
                    "delta": dict(update.value),
                }
            elif isinstance(update, FailedProviderStream):
                yield await self._finalize_stream_failure(fence, update.error)
                return
            elif isinstance(update, CompletedProviderStream):
                operation = await self._finalize_completed(fence, update.result)
                yield completed_frame(
                    update.result,
                    _positive_version(operation.get("state_version")),
                )
                return

    async def _read(self, request: Mapping[str, Any]) -> Mapping[str, object]:
        return await self._db_call(self._repository.read(**{
            key: request[key] for key in (
                "request_id", "org_id", "user_id", "run_id",
                "model_attempt_id", "execution_token", "request_hash",
            )
        }))

    async def _claim(
        self, request: Mapping[str, Any], provider_revision: str,
    ) -> Mapping[str, object]:
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
            provider_revision=provider_revision,
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
            usage_summary=db_usage_projection(result.usage),
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
        code = "GATEWAY_PROVIDER_OUTCOME_UNKNOWN"
        response = await self._db_call(self._repository.finalize(
            **fence,
            terminal_status="unknown",
            provider_request_id=error.provider_request_id,
            response_started=error.response_started,
            response_hash=None,
            usage_summary={},
            terminal_error_code=None,
            ambiguity_code=code,
        ))
        operation = _mapping(response.get("operation"))
        if response.get("outcome") not in {"unknown", "readback"}:
            raise GatewayConnectionAbort
        if operation.get("status") != "unknown":
            raise GatewayConnectionAbort
        return _unknown(
            code, error.response_started, error.provider_request_id,
        )

    async def _finalize_cancelled(
        self, fence: Mapping[str, object], *, response_started: bool,
        provider_request_id: str | None,
    ) -> None:
        try:
            await asyncio.shield(self._db_call(self._repository.finalize(
                **fence,
                terminal_status="unknown",
                provider_request_id=provider_request_id,
                response_started=response_started,
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
    operation: Mapping[str, object], claim_token: str,
) -> dict[str, object]:
    return {
        "operation_id": operation.get("operation_id"),
        "claim_token": claim_token,
        "expected_state_version": _positive_version(operation.get("state_version")),
        "org_id": operation["org_id"],
        "execution_token": operation["execution_token"],
        "request_hash": operation["request_hash"],
        "provider_revision": operation["provider_revision"],
        "tenant_kill_epoch": operation["tenant_kill_epoch"],
        "provider_kill_epoch": operation["provider_kill_epoch"],
        "capability_kill_epoch": operation["capability_kill_epoch"],
    }


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


def _readback_terminal(operation: Mapping[str, object]) -> dict[str, object]:
    status = operation.get("status")
    if status == "failed":
        return _failed(str(operation.get("terminal_error_code") or "GATEWAY_FAILED"))
    if status == "unknown":
        return _unknown(
            str(operation.get("ambiguity_code") or "GATEWAY_OUTCOME_UNKNOWN"),
            bool(operation.get("response_started")),
            _provider_request_id(operation),
        )
    code = (
        "GATEWAY_COMPLETED_READBACK_ONLY"
        if status == "completed" else "GATEWAY_DISPATCH_READBACK"
    )
    return _unknown(
        code,
        bool(operation.get("response_started")),
        _provider_request_id(operation),
    )


def _provider_request_id(operation: Mapping[str, object]) -> str | None:
    value = operation.get("provider_request_id")
    return value if isinstance(value, str) else None


def _evidence_provider_id(evidence: Mapping[str, object]) -> str | None:
    value = evidence.get("provider_request_id")
    return value if isinstance(value, str) else None


__all__ = ["GatewayConnectionAbort", "ModelGatewayService"]
