"""Lossless in-process envelope. No serialization, delivery, cache or audit writes.

Original objects are retained, including runtime-only metadata. Projections are
lazy so wrapping cannot introduce serialization failures into legacy execution.
This is deliberately NOT a persistence payload (block 06 owns that contract).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

from .context import ToolContext
from .policy import ToolCall, ToolDecision


@dataclass(frozen=True)
class ToolExecutionMetadata:
    status: str  # not_started / succeeded (returned, including business errors) / failed / uncertain
    handler_started: bool
    effects: tuple[str, ...]
    elapsed_ms: int
    attempts: int
    cancelled: bool = False
    cached: bool = False
    replayed: bool = False


@dataclass(frozen=True)
class ToolError:
    message: str
    kind: str
    retryable: bool | None = None  # original handler hint, not authorization to retry
    safe_to_retry: bool = False
    retry_context: Any = None


@dataclass(frozen=True)
class ToolArtifacts:
    file_ref: Any = None
    emit_payloads: Any = field(default_factory=list)
    image_url: str = ""
    form: Any = None
    data: Any = None
    columns: Any = None


@dataclass(frozen=True)
class ToolResult:
    raw: Any
    kind: str
    status: str
    decision: ToolDecision
    execution: ToolExecutionMetadata
    audit: dict[str, Any]
    error: ToolError | None = None
    exception: BaseException | None = field(default=None, repr=False)

    @classmethod
    def wrap(
        cls, raw: Any, *, call: ToolCall, context: ToolContext, decision: ToolDecision,
        elapsed_ms: int = 0,
    ) -> ToolResult:
        from schemas.multimodal import FileReadResult
        from services.agent.agent_result import AgentResult
        from services.scheduler.chat_task_manager import FormBlockResult

        if isinstance(raw, AgentResult):
            kind, status, length = "agent", raw.status, len(raw.summary)
        elif isinstance(raw, FileReadResult):
            kind, status, length = "file_read", "success", len(raw.text)
        elif isinstance(raw, FormBlockResult):
            kind, status, length = "form", "success", None
        elif isinstance(raw, str):
            kind, status, length = "string", "success", len(raw)
        else:
            raise TypeError(f"Unsupported tool result: {type(raw).__name__}")
        error = None
        if kind == "agent" and raw.is_failure:
            hint = raw.metadata.get("retryable")
            retryable = hint if type(hint) is bool else None
            error = ToolError(
                raw.error_message, str(status), retryable,
                retryable is True and decision.effects == ("none",),
                raw.metadata.get("retry_context"),
            )
        return cls(
            raw, kind, status, decision,
            ToolExecutionMetadata("succeeded", True, decision.effects, elapsed_ms, 1),
            cls._audit(call, context, status, elapsed_ms, length, raw if kind == "agent" else None),
            error,
        )

    @classmethod
    def from_exception(
        cls, error: BaseException, *, call: ToolCall, context: ToolContext,
        decision: ToolDecision, handler_started: bool, elapsed_ms: int = 0,
    ) -> ToolResult:
        if not isinstance(error, (Exception, asyncio.CancelledError)):
            raise error
        cancelled = isinstance(error, asyncio.CancelledError)
        status = "cancelled" if cancelled else "timeout" if isinstance(error, TimeoutError) else "error"
        execution_status = "not_started" if not handler_started else (
            "failed" if decision.effects == ("none",) else "uncertain"
        )
        return cls(
            None, "exception", status, decision,
            ToolExecutionMetadata(execution_status, handler_started, decision.effects,
                                  elapsed_ms, int(handler_started), cancelled),
            cls._audit(call, context, status, elapsed_ms, None),
            ToolError(str(error), type(error).__name__), error,
        )

    @classmethod
    def not_executed(
        cls, *, call: ToolCall, context: ToolContext, decision: ToolDecision,
    ) -> ToolResult:
        status = "confirmation_required" if decision.outcome == "require_confirmation" else "denied"
        error = (ValueError(f"Unknown sync tool: {call.name}") if decision.reason == "unknown_tool"
                 else PermissionError(f"Tool execution not allowed: {decision.reason}"))
        result = cls.from_exception(error, call=call, context=context, decision=decision, handler_started=False)
        return cls(result.raw, result.kind, status, decision, result.execution,
                   {**result.audit, "status": status}, result.error, error)

    @staticmethod
    def _audit(call, context, status, elapsed_ms, length, agent=None) -> dict[str, Any]:
        return {
            "tool_name": call.name, "tool_call_id": call.call_id,
            "actor_user_id": context.actor_user_id, "workspace_owner_id": context.workspace_owner_id,
            "org_id": context.org_id, "conversation_id": context.conversation_id, "task_id": context.task_id,
            "args": call.arguments, "status": status, "elapsed_ms": elapsed_ms,
            "result_length": length, "truncated": False, "cached": False,
            "tokens_used": agent.tokens_used if agent is not None else 0,
            "source": agent.source if agent is not None else "",
            "metadata": agent.metadata if agent is not None else {},
        }

    def to_legacy(self) -> Any:
        """Exact original object or original exception, including cancellation."""
        if self.exception is not None:
            raise self.exception
        return self.raw

    @property
    def is_failure(self) -> bool:
        return self.exception is not None or (self.kind == "agent" and self.raw.is_failure)

    def model_content(self, consumer: str) -> Any:
        """Original pre-truncation projection; Chat image injection remains separate."""
        if consumer not in {"chat", "tool_loop"}:
            raise ValueError(f"Unknown model consumer: {consumer}")
        raw = self.to_legacy()
        if self.kind == "agent":
            return raw.to_message_content() if consumer == "chat" else raw.to_tool_content()
        if consumer == "tool_loop":
            # The legacy loop only normalizes AgentResult. Other objects pass
            # through here; expanding that loop's type support belongs to block 05.
            return raw or ""
        if consumer == "chat":
            if self.kind == "file_read":
                return raw.text
            if self.kind == "form":
                return raw.llm_hint
        return str(raw)

    @property
    def model_image_blocks(self) -> list[dict[str, Any]]:
        if self.kind == "file_read" and self.raw.type == "image" and self.raw.image_url:
            return [{"type": "image_url", "image_url": {"url": self.raw.image_url}}]
        return []

    @property
    def display(self) -> dict[str, Any]:
        if self.kind == "agent":
            return {"text": self.raw.summary or "", "thinking_text": self.raw.thinking_text,
                    "format": self.raw.format, "terminal_form": False}
        if self.kind == "file_read":
            return {"text": self.raw.text or "", "image_url": self.raw.image_url, "terminal_form": False}
        if self.kind == "form":
            return {"text": "表单已展示", "form": self.raw.form, "terminal_form": True}
        return {"text": str(self.exception) if self.exception is not None else self.raw, "terminal_form": False}

    @property
    def artifacts(self) -> ToolArtifacts:
        if self.kind == "agent":
            return ToolArtifacts(file_ref=self.raw.file_ref, emit_payloads=self.raw.emit_payloads,
                                 data=self.raw.data, columns=self.raw.columns)
        if self.kind == "file_read":
            return ToolArtifacts(image_url=self.raw.image_url if self.raw.type == "image" else "")
        if self.kind == "form":
            return ToolArtifacts(form=self.raw.form)
        return ToolArtifacts()

    @property
    def metadata(self) -> dict[str, Any]:
        return self.raw.metadata if self.kind == "agent" else {}

    @property
    def agent_context(self) -> dict[str, Any]:
        if self.kind != "agent":
            return {}
        return {name: getattr(self.raw, name) for name in (
            "source", "tokens_used", "confidence", "insights", "follow_up", "thinking_text",
        )}

    def audit_fields(self) -> dict[str, Any]:
        """Pre-delivery facts for the existing writer; no write or dedup occurs here."""
        fields = dict(self.audit)
        if self.kind == "form":
            fields["result_length"] = len(json.dumps(self.raw.form))
        return fields
