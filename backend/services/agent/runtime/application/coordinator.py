"""PostgreSQL-first Command Coordinator skeleton."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress

from services.agent.runtime.ports.command_claim import (
    CommandClaim,
    CommandClaimOutcome,
    CommandClaimRepositoryPort,
)
from services.agent.runtime.application.action_loop import ActionLoopDriver
from services.agent.runtime.application.authorization_recovery import (
    AuthorizationRecoveryDriver,
)
from services.agent.runtime.application.model_loop import ModelLoopDriver
from services.agent.runtime.domain.errors import FencingTokenMismatchError
from services.agent.runtime.ports.coordinator_recovery import (
    CoordinatorRecoveryPort,
    RecoveryOutcome,
)
from services.agent.runtime.ports.repository import (
    MutationOutcome,
    RuntimeRepositoryPort,
)


CommandHandler = Callable[[CommandClaim], Awaitable[CommandClaimOutcome]]
WakeupWaiter = Callable[[float], Awaitable[None]]


class CommandClaimLeaseLost(RuntimeError):
    """The Coordinator must stop work without writing a terminal fact."""


class RuntimeCoordinator:
    """Polls durable Commands; optional wakeups only shorten the next poll."""

    def __init__(
        self, repository: CommandClaimRepositoryPort, worker_id: str,
        handler: CommandHandler, *, poll_interval: float = 1.0,
        lease_seconds: int = 90, renew_interval: float = 30.0,
        wakeup_waiter: WakeupWaiter | None = None,
    ) -> None:
        if poll_interval <= 0 or renew_interval <= 0:
            raise ValueError("COORDINATOR_INTERVAL_MUST_BE_POSITIVE")
        self._repository = repository
        self._worker_id = worker_id
        self._handler = handler
        self._poll_interval = poll_interval
        self._lease_seconds = lease_seconds
        self._renew_interval = renew_interval
        self._wakeup_waiter = wakeup_waiter
        self._stopping = asyncio.Event()

    async def run(self) -> None:
        """Recover expired claims through the same PostgreSQL scan loop."""
        while not self._stopping.is_set():
            handled = await self.run_once()
            if not handled:
                await self._wait_for_work()

    async def run_once(self) -> bool:
        receipt = await self._repository.claim_next(
            self._worker_id, lease_seconds=self._lease_seconds,
        )
        if receipt.outcome is CommandClaimOutcome.NOT_FOUND:
            return False
        if receipt.claim is None:
            return True
        await self._handle_claim(receipt.claim)
        return True

    def stop(self) -> None:
        self._stopping.set()

    async def _handle_claim(self, claim: CommandClaim) -> None:
        handler = asyncio.create_task(self._handler(claim))
        renewal = asyncio.create_task(self._renew_until_done(claim))
        try:
            done, _ = await asyncio.wait(
                {handler, renewal}, return_when=asyncio.FIRST_COMPLETED,
            )
            if renewal in done:
                handler.cancel()
                with suppress(asyncio.CancelledError):
                    await handler
                renewal_error = renewal.exception()
                if renewal_error is not None:
                    raise CommandClaimLeaseLost(
                        claim.command_id,
                    ) from renewal_error
                raise CommandClaimLeaseLost(claim.command_id)
            terminal = await handler
            await self._repository.finish(claim, terminal)
        except asyncio.CancelledError:
            raise
        except CommandClaimLeaseLost:
            raise
        except Exception as exc:
            await self._repository.finish(
                claim, CommandClaimOutcome.FAILED,
                error_class=type(exc).__name__,
            )
            raise
        finally:
            handler.cancel()
            renewal.cancel()
            for task in (handler, renewal):
                with suppress(asyncio.CancelledError, Exception):
                    await task

    async def _renew_until_done(self, claim: CommandClaim) -> None:
        while not self._stopping.is_set():
            await asyncio.sleep(self._renew_interval)
            receipt = await self._repository.renew(
                claim, lease_seconds=self._lease_seconds,
            )
            if receipt.outcome not in {
                CommandClaimOutcome.RENEWED,
                CommandClaimOutcome.FOUND,
            }:
                return

    async def _wait_for_work(self) -> None:
        if self._wakeup_waiter is None:
            with suppress(TimeoutError):
                await asyncio.wait_for(
                    self._stopping.wait(), self._poll_interval,
                )
            return
        try:
            await self._wakeup_waiter(self._poll_interval)
        except Exception:
            with suppress(TimeoutError):
                await asyncio.wait_for(
                    self._stopping.wait(), self._poll_interval,
                )


class RuntimeLoopCoordinator:
    """Code-level owner for Run, ModelAttempt and Action advancement."""

    def __init__(
        self, *, recovery_repository: CoordinatorRecoveryPort,
        runtime_repository: RuntimeRepositoryPort,
        model_loop: ModelLoopDriver, action_loop: ActionLoopDriver,
        authorization_loop: AuthorizationRecoveryDriver | None = None,
        worker_id: str, poll_interval: float = 1.0,
        run_lease_seconds: int = 90, run_renew_interval: float = 30.0,
    ) -> None:
        if poll_interval <= 0 or run_renew_interval <= 0:
            raise ValueError("RUNTIME_LOOP_INTERVAL_MUST_BE_POSITIVE")
        self._recovery = recovery_repository
        self._runtime = runtime_repository
        self._model_loop = model_loop
        self._action_loop = action_loop
        self._authorization_loop = authorization_loop
        self._worker_id = worker_id
        self._poll_interval = poll_interval
        self._run_lease_seconds = run_lease_seconds
        self._run_renew_interval = run_renew_interval
        self._stopping = asyncio.Event()

    async def run(self) -> None:
        """Supervise independent durable Run and Action scanners."""
        tasks = {
            asyncio.create_task(self._run_scanner()),
            asyncio.create_task(self._action_scanner()),
            asyncio.create_task(self._reconciliation_scanner()),
        }
        if self._authorization_loop is not None:
            tasks.add(asyncio.create_task(self._authorization_scanner()))
        try:
            done, _ = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_EXCEPTION,
            )
            for task in done:
                error = task.exception()
                if error is not None:
                    raise error
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                with suppress(asyncio.CancelledError):
                    await task

    def stop(self) -> None:
        self._stopping.set()

    async def handle_command(
        self, claim: CommandClaim,
    ) -> CommandClaimOutcome:
        """A claimed Command already owns a durable Run created by migration 219."""
        if not claim.run_id:
            raise RuntimeError("COMMAND_RUN_ASSOCIATION_REQUIRED")
        return CommandClaimOutcome.COMPLETED

    async def run_once(self) -> bool:
        claim = await self._recovery.claim_next_run(
            worker_id=self._worker_id,
            lease_seconds=self._run_lease_seconds,
        )
        if claim.outcome is RecoveryOutcome.NOT_FOUND:
            return False
        if claim.outcome is RecoveryOutcome.ATTEMPTS_EXHAUSTED:
            return True
        if (
            claim.run_id is None
            or claim.execution_token is None
            or claim.state_version is None
        ):
            raise RuntimeError("RUN_CLAIM_RECEIPT_INCOMPLETE")
        await self._with_run_lease(
            run_id=claim.run_id,
            token=claim.execution_token,
            initial_state_version=claim.state_version,
        )
        return True

    async def action_once(self) -> bool:
        return await self._action_loop.dispatch_once()

    async def reconciliation_once(self) -> bool:
        return await self._action_loop.reconcile_once()

    async def authorization_once(self) -> bool:
        if self._authorization_loop is None:
            return False
        return await self._authorization_loop.run_once()

    async def _with_run_lease(
        self, *, run_id: str, token: str, initial_state_version: int,
    ) -> None:
        work = asyncio.create_task(self._advance_run(
            run_id=run_id, token=token,
            initial_state_version=initial_state_version,
        ))
        renewal = asyncio.create_task(self._renew_run(run_id, token))
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
                    raise CommandClaimLeaseLost(run_id) from error
                raise CommandClaimLeaseLost(run_id)
            await work
        finally:
            work.cancel()
            renewal.cancel()
            for task in (work, renewal):
                with suppress(asyncio.CancelledError, Exception):
                    await task

    async def _advance_run(
        self, *, run_id: str, token: str, initial_state_version: int,
    ) -> None:
        del initial_state_version
        snapshot = await self._recovery.get_run_aggregate(
            run_id=run_id, worker_id=self._worker_id,
            execution_token=token,
        )
        await self._model_loop.advance(
            snapshot=snapshot, worker_id=self._worker_id,
            run_id=run_id, run_execution_token=token,
        )
        try:
            refreshed = await self._recovery.get_run_aggregate(
                run_id=run_id, worker_id=self._worker_id,
                execution_token=token,
            )
        except FencingTokenMismatchError:
            # Tool terminal, cancel, or a competing fence may release ownership.
            return
        step = refreshed.latest_model_step
        if step is None:
            return
        run_version = _state_version(refreshed.run)
        if (
            step.get("status") == "completed"
            and step.get("stop_reason") in {"final", "structured_final"}
            and refreshed.latest_model_result is not None
        ):
            result_hash = str(
                refreshed.latest_model_result["content_hash"],
            )
            receipt = await self._runtime.complete_run(
                run_id, token, run_version, result_hash,
            )
            if receipt.outcome not in {
                MutationOutcome.COMPLETED,
                MutationOutcome.ALREADY_COMPLETED,
            }:
                raise RuntimeError(
                    f"RUN_COMPLETE_REJECTED:{receipt.outcome.value}",
                )
        elif step.get("status") == "failed":
            await self._runtime.fail_run(
                run_id, token, run_version,
                str(step.get("terminal_reason", "model_step_failed")),
            )
        elif (
            step.get("status") == "completed"
            and step.get("stop_reason") not in {"tool_calls"}
        ):
            await self._runtime.fail_run(
                run_id, token, run_version,
                str(step.get("stop_reason", "model_step_nonfinal")),
            )

    async def _renew_run(self, run_id: str, token: str) -> None:
        while not self._stopping.is_set():
            await asyncio.sleep(self._run_renew_interval)
            receipt = await self._runtime.renew_run(
                run_id, token, self._run_lease_seconds,
            )
            if receipt.outcome is not MutationOutcome.RENEWED:
                return

    async def _run_scanner(self) -> None:
        while not self._stopping.is_set():
            if not await self.run_once():
                await self._wait()

    async def _action_scanner(self) -> None:
        while not self._stopping.is_set():
            if not await self.action_once():
                await self._wait()

    async def _reconciliation_scanner(self) -> None:
        while not self._stopping.is_set():
            if not await self.reconciliation_once():
                await self._wait()

    async def _authorization_scanner(self) -> None:
        while not self._stopping.is_set():
            if not await self.authorization_once():
                await self._wait()

    async def _wait(self) -> None:
        with suppress(TimeoutError):
            await asyncio.wait_for(
                self._stopping.wait(), self._poll_interval,
            )


def _state_version(value: object) -> int:
    if not isinstance(value, dict):
        from collections.abc import Mapping

        if not isinstance(value, Mapping):
            raise RuntimeError("RUN_SNAPSHOT_REQUIRED")
    version = value.get("state_version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise RuntimeError("RUN_STATE_VERSION_REQUIRED")
    return version
