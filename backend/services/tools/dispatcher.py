"""Dispatch approved, immutable calls only. No policy, UI, retries or business IO."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from .policy import ToolCall, ToolDecision
from .spec import ToolSpec, thaw


class ToolHandler(Protocol):
    async def __call__(self, arguments: dict[str, Any]) -> Any: ...


@dataclass
class _DispatchState:
    consumed: bool = False
    handler_started: bool = False


@dataclass(frozen=True, eq=False)
class _ApprovedCall:
    """Request-local permit issued by the execution entrypoint, never from JSON."""

    call: ToolCall
    spec: ToolSpec
    decision: ToolDecision
    issuer: object
    state: _DispatchState = field(default_factory=_DispatchState)


class ToolDispatcher:
    def __init__(self, handlers: Mapping[tuple[str, str], ToolHandler]) -> None:
        self._handlers = dict(handlers)
        self._issuer = object()

    def _approve(self, call: ToolCall, spec: ToolSpec, decision: ToolDecision) -> _ApprovedCall:
        # Only ToolExecutionService calls this after consulting its canonical Policy.
        if decision.outcome != "allow" or call.name != spec.name:
            raise PermissionError("Tool call has not been approved")
        return _ApprovedCall(call, spec, decision, self._issuer)

    async def dispatch(self, approved: _ApprovedCall) -> Any:
        if (not isinstance(approved, _ApprovedCall) or approved.issuer is not self._issuer
                or approved.decision.outcome != "allow" or approved.state.consumed):
            raise PermissionError("An unused approved tool call is required")
        approved.state.consumed = True
        handler = self._handlers.get((approved.spec.executor_type, approved.spec.handler_key))
        if handler is None:
            raise ValueError(f"Unknown sync tool: {approved.call.name}")
        approved.state.handler_started = True
        return await handler(thaw(approved.call.arguments))
