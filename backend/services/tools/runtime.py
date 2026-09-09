"""Production orchestration. Business handlers and old result/ledger payloads stay intact."""

import asyncio
from dataclasses import replace
from uuid import uuid4

from .dispatcher import ToolDispatcher
from .execution import ToolExecutionService
from .legacy import LegacyAdvertisement, build_legacy_catalog
from .legacy_handler import build_legacy_handlers
from .policy import ToolCall, ToolConfirmation
from .result import ToolResult
from .runtime_context import (
    executor_context, refresh_context, resolve_resources, check_deferred_resources,
    check_result_resources,
)
from .spec import thaw


async def run_parallel(operations):
    """Keep cancellation inside the current batch; do not leave sibling IO running."""
    tasks = [asyncio.create_task(operation) for operation in operations]
    try:
        return await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


class ToolRuntime:
    def __init__(self, executor):
        self.executor = executor
        self.registry = build_legacy_catalog()
        self.service = ToolExecutionService(self.registry, ToolDispatcher(build_legacy_handlers(executor)))
        self.policy = self.service.policy
        self._confirmations = {}
        self._pending = {}

    def context(self, call_id=None):
        return executor_context(self.executor, call_id=call_id)

    def advertised(self, initial_names=None, discovered_names=()):
        return self.registry.resolve(
            self.context(), policy=self.policy,
            advertisement=LegacyAdvertisement(initial_names), discovered_names=discovered_names,
        ).advertised_schemas()

    def batches(self, calls):
        return self.policy.plan_batches(calls, self.context())

    def check_argument_scope(self, arguments):
        context = self.context()
        expected = {"user_id": context.actor_user_id, "actor_user_id": context.actor_user_id,
                    "workspace_owner_id": context.workspace_owner_id,
                    "workspace_user_id": context.workspace_owner_id, "org_id": context.org_id,
                    "conversation_id": context.conversation_id}
        if any(key in arguments and arguments[key] != value for key, value in expected.items()):
            raise PermissionError("tool_scope_mismatch: 工具未执行")

    @staticmethod
    def check_lifetime(context):
        ToolExecutionService._check_cancelled(context)
        if context.budget is not None and context.budget.remaining <= 0:
            raise TimeoutError("tool_budget_exhausted")

    async def execute(self, name, arguments, *, call_id=None, cache=None, lifecycle=None):
        call = ToolCall(call_id or str(uuid4()), name, arguments)
        context = self.context(call.call_id)
        self.check_lifetime(context)
        decision = self.policy.decide(name, context, call.arguments)
        if decision.outcome == "deny" and not decision.reason.startswith("business_permission_required:"):
            return ToolResult.not_executed(call=call, context=context, decision=decision)
        try:
            self.check_argument_scope(call.arguments)
            context = await refresh_context(self.executor, context, self.registry)
            self.check_lifetime(context)
            decision = self.policy.decide(name, context, call.arguments)
            if decision.outcome == "deny":
                return ToolResult.not_executed(call=call, context=context, decision=decision)
            call = ToolCall(call.call_id, name, resolve_resources(self.executor, name, call.arguments))
            await check_deferred_resources(self.executor, name, call.arguments)
            self.check_lifetime(context)
            decision = self.policy.decide(name, context, call.arguments)
            if decision.outcome == "deny":
                return ToolResult.not_executed(call=call, context=context, decision=decision)
            # Read-only peek: completed business calls do not require a second
            # approval. Current policy/action/resources are checked above.
            if lifecycle is not None:
                replay = await lifecycle.replay(call, context, decision)
                if replay is not None:
                    self.check_lifetime(context)
                    return replay
            receipt = None
            if decision.outcome == "require_confirmation":
                binding = decision.confirmation_binding
                receipt = self._confirmations.get(binding)
                if receipt is None:
                    if binding not in self._pending:
                        self._pending[binding] = asyncio.create_task(self._confirm(call, context, decision))
                    receipt = await self._pending[binding]
                    self._confirmations[binding] = receipt
                # Fresh identity, flags, mode, authorization and resource facts.
                context = await refresh_context(self.executor, self.context(call.call_id), self.registry)
                self.check_lifetime(context)
                current = resolve_resources(self.executor, name, call.arguments)
                call = ToolCall(call.call_id, name, current)
            self.check_lifetime(context)
            if decision.reason == "resource_notice":
                from loguru import logger
                logger.info(f"Tool confirm notify | tool={name} | task={context.task_id}")

            async def before_dispatch(call, context, decision):
                self.check_lifetime(context)
                if cache is not None and decision.cacheable:
                    raw = cache.get(name, thaw(call.arguments))
                    if raw is not None:
                        check_result_resources(context, raw)
                        result = ToolResult.wrap(raw, call=call, context=context, decision=decision)
                        return replace(result, execution=replace(
                            result.execution, handler_started=False, attempts=0, cached=True,
                        ))
                if lifecycle is not None:
                    return await lifecycle.begin(call, context, decision)
                return None

            async def on_result(result):
                # Completion is independent of display/audit delivery. A failure
                # writing/consuming a completed result never redoes business IO.
                if lifecycle is not None:
                    await lifecycle.complete(result)
                if cache is not None and result.exception is None and result.decision.cacheable:
                    try:
                        cache.put(name, thaw(call.arguments), result.to_legacy())
                    except Exception as error:
                        from loguru import logger
                        logger.warning(f"tool_cache_write_failed | error={type(error).__name__}")

            return await self.service.execute(call, context, confirmation=receipt,
                                              before_dispatch=before_dispatch, on_result=on_result)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            return ToolResult.from_exception(error, call=call, context=context,
                                             decision=decision, handler_started=False)

    async def _confirm(self, call, context, decision):
        waits = []
        try:
            confirmation = asyncio.create_task(self.executor.tool_confirmer(call, context, decision))
            waits.append(confirmation)
            if context.cancellation is not None:
                cancelled = asyncio.create_task(context.cancellation.wait())
                waits.append(cancelled)
            done, _ = await asyncio.wait(waits, timeout=min(60.0, context.budget.remaining)
                                        if context.budget else 60.0, return_when=asyncio.FIRST_COMPLETED)
            self.check_lifetime(context)
            approved = confirmation.result() if confirmation in done else False
        except asyncio.CancelledError:
            raise
        except Exception:
            approved = False
        finally:
            for task in waits:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*waits, return_exceptions=True)
        return ToolConfirmation(decision.confirmation_binding,
                                "approved" if approved is True else "rejected")
