"""Policy-enforced entrypoint shared by production and compatibility callers.

One service per request/execution scope. Trusted callers supply refreshed Context,
normalized/resolved arguments and authenticated confirmation receipts. They also
own executor/context identity pairing, deadlines, invocation storage and delivery.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace

from .context import ToolContext
from .dispatcher import ToolDispatcher
from .policy import ToolCall, ToolConfirmation, ToolPolicy
from .registry import ToolRegistry
from .result import ToolResult


class ToolExecutionService:
    def __init__(self, registry: ToolRegistry, dispatcher: ToolDispatcher) -> None:
        self.registry = registry
        self.policy = ToolPolicy(registry)
        self.dispatcher = dispatcher
        self._consumed: set[tuple] = set()

    @staticmethod
    def _check_cancelled(context: ToolContext) -> None:
        if context.cancellation is not None and context.cancellation.is_set():
            raise asyncio.CancelledError()

    async def execute(
        self, call: ToolCall, context: ToolContext, *, confirmation: ToolConfirmation | None = None,
        before_dispatch=None, on_result=None,
    ) -> ToolResult:
        """Policy denial/pending/ordinary exceptions become envelopes; cancellation propagates.

        No confirmation UI is driven here. Pending calls can be resubmitted after
        an authenticated response with current context; dispatched IDs cannot be
        resubmitted in this service, even after an exception/cancellation.
        """
        context = replace(context, call_id=call.call_id)
        self._check_cancelled(context)
        decision = self.policy.decide(call.name, context, call.arguments, confirmation=confirmation)
        if decision.outcome != "allow":
            return ToolResult.not_executed(call=call, context=context, decision=decision)
        self._check_cancelled(context)
        key = (context.actor_user_id, context.workspace_owner_id, context.org_id,
               context.conversation_id, context.task_id, call.call_id)
        if key in self._consumed:
            return ToolResult.from_exception(
                PermissionError(f"Tool call already dispatched: {call.call_id}"),
                call=call, context=context, decision=decision, handler_started=False,
            )
        approved = self.dispatcher._approve(call, self.registry.require(call.name), decision)
        self._consumed.add(key)  # reserve before the first await, including concurrent submissions
        started = time.monotonic()
        # Trusted runtime hooks own the existing cache/invocation lifecycle.
        # They run only after the canonical Policy, never on rejection.
        if before_dispatch is not None:
            reused = await before_dispatch(call, context, decision)
            if reused is not None:
                return reused
        self._check_cancelled(context)
        try:
            raw = await self.dispatcher.dispatch(approved)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            result = ToolResult.from_exception(
                error, call=call, context=context, decision=decision,
                handler_started=approved.state.handler_started,
                elapsed_ms=int((time.monotonic() - started) * 1000),
            )
        else:
            result = ToolResult.wrap(raw, call=call, context=context, decision=decision,
                                     elapsed_ms=int((time.monotonic() - started) * 1000))
        if on_result is not None:
            await on_result(result)
        return result

    async def execute_legacy(self, call: ToolCall, context: ToolContext, **kwargs):
        """Compatibility exit for block 04: original return types / raised exceptions."""
        return (await self.execute(call, context, **kwargs)).to_legacy()
