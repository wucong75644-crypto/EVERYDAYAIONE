"""Single-item scheduled finalization driver owned by the Runtime Worker."""

from __future__ import annotations

from datetime import timedelta
from uuid import NAMESPACE_URL, uuid5

from services.agent.runtime.ports.scheduled_finalization import (
    ScheduledFinalizationContext,
    ScheduledFinalizationProjection,
    ScheduledFinalizationRepositoryPort,
    ScheduledTerminalStatus,
)
from services.scheduler.cron_utils import calc_next_run


class ScheduledRuntimeFinalizer:
    def __init__(
        self, repository: ScheduledFinalizationRepositoryPort,
        worker_id: str, *, lease_seconds: int = 90,
    ) -> None:
        if not worker_id.strip():
            raise ValueError("SCHEDULED_FINALIZER_WORKER_ID_REQUIRED")
        self._repository = repository
        self._worker_id = worker_id
        self._lease_seconds = lease_seconds

    async def run_once(self) -> bool:
        claim = await self._repository.claim_next(
            self._worker_id, lease_seconds=self._lease_seconds,
        )
        if claim is None:
            return False
        context = await self._repository.read_context(claim)
        projection = ScheduledFinalizationProjection(
            request_id=str(uuid5(
                NAMESPACE_URL,
                "everydayai:scheduled-finalization:"
                f"{claim.scheduled_run_id}:{claim.claim_token}:"
                f"{context.intent_state_version}",
            )),
            next_run_at=_next_run_at(context),
        )
        await self._repository.apply(claim, context, projection)
        return True


def _next_run_at(context: ScheduledFinalizationContext):
    if context.terminal_status is ScheduledTerminalStatus.FAILED:
        failures = context.consecutive_failures + 1
        threshold = max(3, context.retry_count + 1)
        if failures >= threshold:
            return None
        if failures - 1 < context.retry_count:
            return context.terminal_baseline + timedelta(minutes=5)
    if context.schedule_type == "once":
        return None
    if context.cron_expr is None:
        raise RuntimeError("SCHEDULED_FINALIZER_CRON_REQUIRED")
    return calc_next_run(
        context.cron_expr, context.timezone, base=context.terminal_baseline,
    )
