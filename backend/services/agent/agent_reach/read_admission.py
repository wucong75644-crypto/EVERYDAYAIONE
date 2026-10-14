"""Bounded interactive read admission; background jobs keep their durable retry time."""
import asyncio
from datetime import datetime, timezone

from .contracts import ReachError


async def reserve_read(budget, connection, request, executor, *, deadline):
    try:
        return await asyncio.to_thread(budget.reserve, connection, request)
    except ReachError as error:
        if (error.code != 'ACCESS_BUDGET_WAIT'
                or getattr(error, 'wait_reason', '') != 'interval'
                or getattr(executor, 'execution_mode', None) != 'interactive'):
            raise
        try:
            eligible = datetime.fromisoformat(error.next_eligible_at)
            delay = max(0, (eligible - datetime.now(timezone.utc)).total_seconds()) + .05
        except (AttributeError, TypeError, ValueError):
            raise error
        remaining = deadline - asyncio.get_running_loop().time()
        # Keep time for the actual platform read. Long cooldowns/daily limits
        # belong to a later turn, not a sleeping job or a relaxed account policy.
        if delay > 30.1 or delay + 15 >= remaining:
            raise
    # reserve() has closed its transaction before sleeping; the caller retains
    # the account lock so another same-host worker cannot consume this slot.
    await asyncio.sleep(delay)
    return await asyncio.to_thread(budget.reserve, connection, request)
