"""One fenced ModelStep advancement for the Runtime Coordinator."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from contextlib import suppress
from typing import Callable, Mapping, Protocol

from services.agent.runtime.ports.action_repository import ActionRepositoryPort
from services.agent.runtime.ports.coordinator_recovery import (
    CoordinatorRecoveryPort,
    ModelResultDraft,
    RecoveryOutcome,
    RunAggregateSnapshot,
)
from services.agent.runtime.ports.model import (
    ModelCallUnknownError,
    ModelOutputKind,
    ModelPort,
    ModelProviderError,
    ModelStepRequest,
    ModelStepResult,
)
from services.agent.runtime.ports.model_attempt import (
    ModelAttemptOutcome,
    ModelAttemptRepositoryPort,
)
from services.agent.runtime.ports.repository import RuntimeRepositoryPort


@dataclass(frozen=True, kw_only=True)
class PreparedModelCall:
    model_id: str
    provider: str
    model_revision: str
    prompt_revision: str
    tool_catalog_revision: str
    request_receipt: Mapping[str, object]
    reserved_credits: int
    build_request: Callable[[str], ModelStepRequest]
    actual_credits: Callable[[ModelStepResult], int]
    build_actions: Callable[
        [ModelStepResult], tuple[str, tuple[Mapping[str, object], ...]]
    ]


@dataclass(frozen=True, kw_only=True)
class _ActiveModelCall:
    plan: PreparedModelCall
    request: ModelStepRequest
    step_version: int
    attempt_id: str
    attempt_version: int
    attempt_token: str


class ModelCallFactory(Protocol):
    async def __call__(
        self, snapshot: RunAggregateSnapshot,
    ) -> PreparedModelCall: ...


class ModelAttemptReconciler(Protocol):
    async def __call__(
        self, snapshot: RunAggregateSnapshot,
    ) -> None:
        """Resolve or retain the discovered unknown Attempt without dispatch."""


class ModelLoopDriver:
    def __init__(
        self, *, runtime_repository: RuntimeRepositoryPort,
        attempt_repository: ModelAttemptRepositoryPort,
        action_repository: ActionRepositoryPort,
        recovery_repository: CoordinatorRecoveryPort,
        model: ModelPort, call_factory: ModelCallFactory,
        reconciler: ModelAttemptReconciler,
        attempt_lease_seconds: int = 120,
        attempt_renew_interval: float = 40.0,
    ) -> None:
        if attempt_renew_interval <= 0:
            raise ValueError("MODEL_ATTEMPT_RENEW_INTERVAL_MUST_BE_POSITIVE")
        self._runtime = runtime_repository
        self._attempts = attempt_repository
        self._actions = action_repository
        self._recovery = recovery_repository
        self._model = model
        self._call_factory = call_factory
        self._reconciler = reconciler
        self._attempt_lease_seconds = attempt_lease_seconds
        self._attempt_renew_interval = attempt_renew_interval

    async def advance(
        self, *, snapshot: RunAggregateSnapshot, worker_id: str,
        run_id: str, run_execution_token: str,
    ) -> None:
        attempt = snapshot.unresolved_model_attempt
        if attempt is not None and str(attempt["status"]) in {
            "dispatching", "unknown",
        }:
            await self._reconciler(snapshot)
            return
        active = await self._prepare(
            snapshot=snapshot, worker_id=worker_id,
            run_id=run_id, run_execution_token=run_execution_token,
        )
        if active is None:
            return
        result, terminal_version = await self._dispatch(
            active=active, run_execution_token=run_execution_token,
        )
        if result is None:
            return
        await self._commit(
            active=active, result=result,
            terminal_version=terminal_version,
            run_execution_token=run_execution_token,
        )

    async def _prepare(
        self, *, snapshot: RunAggregateSnapshot, worker_id: str,
        run_id: str, run_execution_token: str,
    ) -> _ActiveModelCall | None:
        attempt = snapshot.unresolved_model_attempt
        plan = await self._call_factory(snapshot)
        step = snapshot.latest_model_step
        if step is None or (
            step.get("status") == "completed"
            and step.get("stop_reason") == "tool_calls"
        ):
            created = await self._runtime.create_model_step(
                run_id, run_execution_token,
                model_id=plan.model_id, provider=plan.provider,
                model_revision=plan.model_revision,
                prompt_revision=plan.prompt_revision,
                tool_catalog_revision=plan.tool_catalog_revision,
                request_receipt=plan.request_receipt,
            )
            if created.entity_id is None or created.state_version is None:
                raise RuntimeError("MODEL_STEP_CREATE_RECEIPT_INCOMPLETE")
            step_id = created.entity_id
            step_version = created.state_version
        else:
            step_id = str(step["id"])
            step_version = _version(step)

        request = plan.build_request(step_id)
        if attempt is None:
            prepared = await self._attempts.prepare(
                model_step_id=step_id,
                run_execution_token=run_execution_token,
                expected_step_version=step_version,
                worker_id=worker_id,
                request_hash=request.request_hash,
                idempotency_key=f"model-step:{step_id}",
                provider=plan.provider,
                request_receipt=plan.request_receipt,
                reserved_credits=plan.reserved_credits,
            )
            if prepared.outcome not in {
                ModelAttemptOutcome.PREPARED,
                ModelAttemptOutcome.ALREADY_PREPARED,
            }:
                return
            attempt_id = _required(prepared.attempt_id, "attempt_id")
            attempt_version = _required_int(
                prepared.state_version, "attempt state_version",
            )
            attempt_token = _required(
                prepared.execution_token, "attempt execution_token",
            )
        else:
            attempt_id = str(attempt["id"])
            attempt_version = _version(attempt)
            attempt_token = str(attempt["execution_token"])

        dispatch = await self._attempts.start_dispatch(
            attempt_id=attempt_id,
            run_execution_token=run_execution_token,
            expected_attempt_version=attempt_version,
            request_hash=request.request_hash,
        )
        if dispatch.outcome not in {
            ModelAttemptOutcome.DISPATCHING,
            ModelAttemptOutcome.ALREADY_DISPATCHING,
        }:
            return
        dispatch_version = _required_int(
            dispatch.state_version, "dispatch state_version",
        )
        return _ActiveModelCall(
            plan=plan, request=request, step_version=step_version,
            attempt_id=attempt_id, attempt_version=dispatch_version,
            attempt_token=attempt_token,
        )

    async def _dispatch(
        self, *, active: _ActiveModelCall, run_execution_token: str,
    ) -> tuple[ModelStepResult | None, int]:
        lease = _ModelAttemptLease(
            attempts=self._attempts, recovery=self._recovery,
            attempt_id=active.attempt_id,
            run_execution_token=run_execution_token,
            attempt_execution_token=active.attempt_token,
            state_version=active.attempt_version,
            request_hash=active.request.request_hash,
            lease_seconds=self._attempt_lease_seconds,
            renew_interval=self._attempt_renew_interval,
        )
        try:
            result = await lease.complete(self._model, active.request)
        except ModelCallUnknownError as error:
            await self._attempts.record_unknown(
                attempt_id=active.attempt_id,
                run_execution_token=run_execution_token,
                expected_attempt_version=lease.state_version,
                request_hash=active.request.request_hash,
                dispatch_phase=_dispatch_phase(error),
                retry_disposition="reconcile_only",
                ambiguity_evidence={"kind": "provider_outcome_unproven"},
            )
            return None, lease.state_version
        except ModelProviderError:
            await self._attempts.fail(
                attempt_id=active.attempt_id,
                run_execution_token=run_execution_token,
                expected_attempt_version=lease.state_version,
                expected_step_version=active.step_version,
                request_hash=active.request.request_hash,
                error_code="provider_error",
            )
            return None, lease.state_version
        return result, lease.state_version

    async def _commit(
        self, *, active: _ActiveModelCall, result: ModelStepResult,
        terminal_version: int, run_execution_token: str,
    ) -> None:
        usage = _usage(result)
        credits = active.plan.actual_credits(result)
        if result.stop_reason.value == "tool_calls":
            batch_hash, actions = active.plan.build_actions(result)
            await self._actions.complete_tool_calls(
                attempt_id=active.attempt_id,
                run_execution_token=run_execution_token,
                expected_attempt_version=terminal_version,
                expected_step_version=active.step_version,
                request_hash=active.request.request_hash,
                response_receipt=_response_receipt(result),
                response_hash=result.response_hash,
                provider_stop_reason=result.provider_stop_reason,
                usage=usage, actual_credits=credits,
                batch_hash=batch_hash, actions=actions,
            )
            return
        if result.stop_reason.value not in {"final", "structured_final"}:
            await self._attempts.fail(
                attempt_id=active.attempt_id,
                run_execution_token=run_execution_token,
                expected_attempt_version=terminal_version,
                expected_step_version=active.step_version,
                request_hash=active.request.request_hash,
                error_code=result.stop_reason.value,
            )
            return
        if result.output is None:
            await self._attempts.fail(
                attempt_id=active.attempt_id,
                run_execution_token=run_execution_token,
                expected_attempt_version=terminal_version,
                expected_step_version=active.step_version,
                request_hash=active.request.request_hash,
                error_code=result.stop_reason.value,
            )
            return
        outcome = await self._recovery.complete_model_with_result(
            attempt_id=active.attempt_id,
            run_execution_token=run_execution_token,
            expected_attempt_version=terminal_version,
            expected_step_version=active.step_version,
            request_hash=active.request.request_hash,
            response_receipt=_response_receipt(result),
            response_hash=result.response_hash,
            stop_reason=result.stop_reason.value,
            provider_stop_reason=result.provider_stop_reason,
            usage=usage, actual_credits=credits,
            result=_result_draft(result),
        )
        if outcome is RecoveryOutcome.RUN_CANCELLED_USE_LATE_RECEIPT:
            await self._attempts.record_late_receipt(
                attempt_id=active.attempt_id,
                provider_request_id=(
                    result.response_receipt.provider_request_id
                ),
                response_receipt=_response_receipt(result),
                response_hash=result.response_hash,
                usage=usage, late_outcome="completed",
                ambiguity_evidence={"kind": "completed_after_run_cancel"},
                actual_credits=credits,
            )


def _result_draft(result: ModelStepResult) -> ModelResultDraft:
    output = result.output
    if output is None:
        raise ValueError("MODEL_OUTPUT_REQUIRED")
    if output.kind is ModelOutputKind.TEXT:
        content_hash = hashlib.sha256(output.content.encode()).hexdigest()
        return ModelResultDraft(
            output_kind="text", text_content=output.content,
            content_hash=content_hash,
        )
    structured = json.loads(output.content)
    canonical = json.dumps(
        structured, ensure_ascii=False, sort_keys=True, separators=(", ", ": "),
    )
    return ModelResultDraft(
        output_kind="structured", structured_content=structured,
        schema_revision=output.schema_revision,
        content_hash=hashlib.sha256(canonical.encode()).hexdigest(),
    )


def _response_receipt(result: ModelStepResult) -> Mapping[str, object]:
    receipt = result.response_receipt
    return {
        "output_kind": (
            receipt.output_kind.value if receipt.output_kind is not None else None
        ),
        "output_characters": receipt.output_characters,
        "tool_call_count": receipt.tool_call_count,
        "invalid_tool_call_count": receipt.invalid_tool_call_count,
        "provider": receipt.provider,
        "provider_request_id": receipt.provider_request_id,
    }


def _usage(result: ModelStepResult) -> Mapping[str, object]:
    usage = result.usage
    return {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "reasoning_tokens": usage.reasoning_tokens,
        "cache_read_tokens": usage.cache_read_tokens,
        "cache_write_tokens": usage.cache_write_tokens,
    }


def _dispatch_phase(error: ModelCallUnknownError) -> str:
    return (
        "response_started"
        if any(item.response_started for item in error.attempts)
        else "request_started"
    )


def _version(value: Mapping[str, object]) -> int:
    version = value.get("state_version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise RuntimeError("STATE_VERSION_REQUIRED")
    return version


def _required(value: str | None, name: str) -> str:
    if value is None:
        raise RuntimeError(f"{name.upper()}_REQUIRED")
    return value


def _required_int(value: int | None, name: str) -> int:
    if value is None:
        raise RuntimeError(f"{name.upper()}_REQUIRED")
    return value


class _ModelAttemptLease:
    def __init__(
        self, *, attempts: ModelAttemptRepositoryPort,
        recovery: CoordinatorRecoveryPort, attempt_id: str,
        run_execution_token: str, attempt_execution_token: str,
        state_version: int, request_hash: str, lease_seconds: int,
        renew_interval: float,
    ) -> None:
        self._attempts = attempts
        self._recovery = recovery
        self._attempt_id = attempt_id
        self._run_token = run_execution_token
        self._attempt_token = attempt_execution_token
        self._request_hash = request_hash
        self._lease_seconds = lease_seconds
        self._renew_interval = renew_interval
        self._lock = asyncio.Lock()
        self.state_version = state_version

    async def complete(
        self, model: ModelPort, request: ModelStepRequest,
    ) -> ModelStepResult:
        work = asyncio.create_task(model.complete(request, observer=self))
        renewal = asyncio.create_task(self._renew())
        try:
            done, _ = await asyncio.wait(
                {work, renewal}, return_when=asyncio.FIRST_COMPLETED,
            )
            if renewal in done:
                work.cancel()
                with suppress(asyncio.CancelledError):
                    await work
                error = renewal.exception()
                if error is not None:
                    raise error
                raise RuntimeError("MODEL_ATTEMPT_LEASE_LOST")
            result = await work
            async with self._lock:
                pass
            return result
        finally:
            renewal.cancel()
            with suppress(asyncio.CancelledError):
                await renewal

    async def response_started(
        self, *, provider: str, provider_request_id: str | None,
    ) -> None:
        del provider
        async with self._lock:
            receipt = await self._attempts.mark_response_started(
                attempt_id=self._attempt_id,
                run_execution_token=self._run_token,
                expected_attempt_version=self.state_version,
                request_hash=self._request_hash,
                provider_request_id=provider_request_id,
            )
            self.state_version = _required_int(
                receipt.state_version, "response start state_version",
            )

    async def _renew(self) -> None:
        while True:
            await asyncio.sleep(self._renew_interval)
            async with self._lock:
                self.state_version = await self._recovery.renew_model_attempt(
                    attempt_id=self._attempt_id,
                    run_execution_token=self._run_token,
                    attempt_execution_token=self._attempt_token,
                    expected_state_version=self.state_version,
                    lease_seconds=self._lease_seconds,
                )
