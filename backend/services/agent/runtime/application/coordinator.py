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
