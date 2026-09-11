"""Actor invocation adapter; preserves the existing ledger states and fencing."""

import asyncio

from services.tools.result import ToolResult, UncertainToolInvocationError
from services.tools.spec import thaw


class ActorToolLifecycle:
    def __init__(self, handler, context):
        self.handler = handler
        self.store = getattr(handler, "_actor_invocation_store", None)
        self.context = context
        self.call = None

    def _enabled(self, decision):
        h = self.handler
        # Preserve existing invocation qualification, including restore_file.
        return (decision.risk_level != "safe" and self.store is not None
                and getattr(h, "_actor_enabled", False) is True
                and bool(getattr(h, "_actor_turn_id", None))
                and bool(getattr(h, "_actor_execution_token", None)))

    async def replay(self, call, context, decision):
        if not self._enabled(decision):
            return None
        from services.tool_invocation_store import hash_tool_arguments
        row = await asyncio.to_thread(
            self.store.lookup, task_id=context.task_id, conversation_id=context.conversation_id,
            turn_id=self.handler._actor_turn_id, tool_call_id=call.call_id,
        )
        if not row:
            return None
        if row["tool_name"] != call.name or row["args_hash"] != hash_tool_arguments(thaw(call.arguments)):
            raise PermissionError("ACTOR_TOOL_INVOCATION_ARGUMENT_MISMATCH")
        if row["status"] == "succeeded":
            return self._replayed(row.get("result"), call, context, decision)
        if row["status"] == "running":
            # Preserve stale-call sealing with the current fencing token. This
            # updates an existing invocation; no fresh business call is begun.
            await asyncio.to_thread(
                self.store.mark_stale, task_id=context.task_id,
                turn_id=self.handler._actor_turn_id, tool_call_id=call.call_id,
                execution_token=self.handler._actor_execution_token,
            )
        error_type = UncertainToolInvocationError if row["status"] in {"running", "in_progress", "uncertain"} else PermissionError
        raise error_type("ACTOR_TOOL_INVOCATION_" + row["status"].upper())

    @staticmethod
    def _replayed(payload, call, context, decision):
        from services.tools.result_payload import restore_result
        from services.tools.runtime_context import check_result_resources
        check_result_resources(context, payload)
        result = restore_result(payload, call=call, context=context, decision=decision)
        if result.execution.status == "uncertain":
            raise UncertainToolInvocationError("ACTOR_TOOL_INVOCATION_UNCERTAIN")
        if result.execution.cancelled:
            raise asyncio.CancelledError()
        if result.execution.status != "succeeded":
            raise PermissionError("ACTOR_TOOL_INVOCATION_RESULT_NOT_COMPLETED")
        return result.reused(call=call, context=context, decision=decision, replayed=True)

    async def begin(self, call, context, decision):
        if not self._enabled(decision):
            return None
        from services.handlers.chat_tool_mixin import ChatToolMixin
        invocation = await ChatToolMixin._begin_actor_tool_invocation(
            self.handler, store=self.store, task_id=context.task_id,
            conversation_id=context.conversation_id, tool_call_id=call.call_id,
            tool_name=call.name, args=thaw(call.arguments),
        )
        if not invocation:
            raise PermissionError("ACTOR_TOOL_INVOCATION_UNAVAILABLE")
        outcome = invocation.get("outcome")
        if outcome == "replay":
            return self._replayed(invocation.get("result"), call, context, decision)
        if outcome != "execute":
            error_type = UncertainToolInvocationError if outcome in {"running", "in_progress", "uncertain"} else PermissionError
            raise error_type("ACTOR_TOOL_INVOCATION_" + str(outcome).upper())
        self.call = call
        return None

    async def complete(self, result):
        if self.call is None or not result.execution.handler_started:
            return
        from services.handlers.chat_tool_mixin import ChatToolMixin
        from loguru import logger
        error = result.exception
        try:
            await ChatToolMixin._complete_actor_tool_invocation(
                self.handler, store=self.store, task_id=self.context.task_id,
                turn_id=self.handler._actor_turn_id, tool_call_id=self.call.call_id,
                status="uncertain" if error else "succeeded",
                result=result,
                error_message=str(error) if error else "",
            )
        except Exception as exc:
            # A failed completion write leaves the existing running/uncertain
            # recovery guard in charge. Never turn delivery failure into retry IO.
            logger.warning(f"actor_tool_completion_write_failed | error={type(exc).__name__}")
