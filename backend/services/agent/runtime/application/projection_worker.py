"""One-shot compatibility Projection worker without production wiring."""

from __future__ import annotations

from loguru import logger

from services.agent.runtime.domain.errors import PersistenceContractError
from services.agent.runtime.infrastructure.postgres.compat_projection import (
    PostgresCompatibilityProjection,
)
from services.agent.runtime.ports.projection import ProjectionClaim
from services.agent.runtime.projection import classify_event


class CompatibilityProjectionWorker:
    """Consume claims; projection failure never changes Runtime aggregates."""

    def __init__(self, projection: PostgresCompatibilityProjection) -> None:
        self._projection = projection

    async def run_once(self, batch_size: int = 50) -> int:
        claims = await self._projection.claim(batch_size=batch_size)
        for claim in claims:
            await self._process(claim)
        return len(claims)

    async def _process(self, claim: ProjectionClaim) -> None:
        event = claim.event
        try:
            projection = classify_event(event)
            await self._projection.apply(claim, projection.action.value)
        except PersistenceContractError as error:
            await self._projection.fail(
                claim, _error_code("contract", error),
            )
        except Exception as error:
            if await self._projection.readback(claim) is not None:
                return
            logger.warning(
                "compat_projection_retry | "
                f"outbox_id={claim.outbox_id} | "
                f"event_id={event.event_id} | error={type(error).__name__}",
            )
            await self._projection.fail(
                claim, _error_code("apply", error),
            )


def _error_code(prefix: str, error: Exception) -> str:
    return f"{prefix}_{type(error).__name__}".lower()[:200]
