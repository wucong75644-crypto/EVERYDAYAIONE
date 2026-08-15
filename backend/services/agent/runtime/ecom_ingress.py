"""Durable Runtime ingress for e-commerce model work.

This adapter deliberately owns submission only.  The Runtime coordinator owns
ModelLoop execution and any resulting Actions/Child Runs; synchronous legacy
callers must not construct a provider as a fallback while projection wiring is
being completed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from services.agent.runtime.ingress import RuntimeIngress


@dataclass(frozen=True, kw_only=True)
class RuntimeEcomIngressReceipt:
    outcome: str
    command_id: str | None = None
    run_id: str | None = None
    session_id: str | None = None
    runtime_owned: bool = False

    @property
    def accepted(self) -> bool:
        return self.outcome in {"created", "already_exists"}


@dataclass(frozen=True, kw_only=True)
class RuntimeEcomReadback:
    """Authoritative Runtime state; only terminal results contain content."""

    status: str
    run_id: str | None = None
    model_step_id: str | None = None
    content: str | None = None
    structured_content: Mapping[str, Any] | None = None
    reason_code: str | None = None

    @property
    def terminal(self) -> bool:
        return self.status in {"completed", "failed", "cancelled"}


class RuntimeEcomModelIngress:
    """Convert e-commerce model requests into the shared Runtime ingress."""

    def __init__(self, database: Any) -> None:
        self._ingress = RuntimeIngress(database)

    async def submit(
        self, *, conversation_id: str, org_id: str | None, user_id: str,
        scope_kind: str, scope_id: str, agent_definition_id: str,
        agent_definition_revision: str, input_message_id: str,
        output_message_id: str, idempotency_key: str, model_id: str,
        messages: list[Mapping[str, Any]], feature: str,
        source_id: str, task_id: str, client_task_id: str, turn_id: str,
    ) -> RuntimeEcomIngressReceipt:
        if feature not in {"ecom_plan", "requirement_assist"}:
            raise ValueError("RUNTIME_ECOM_FEATURE_INVALID")
        if not messages:
            raise ValueError("RUNTIME_ECOM_MESSAGES_REQUIRED")
        receipt = await self._ingress.submit(
            conversation_id=conversation_id, org_id=org_id, user_id=user_id,
            scope_kind=scope_kind, scope_id=scope_id,
            agent_definition_id=agent_definition_id,
            agent_definition_revision=agent_definition_revision,
            command_type="submit_input",
            idempotency_key=idempotency_key,
            payload={
                "channel": "web", "model_id": model_id,
                "input_message_id": input_message_id,
                "output_message_id": output_message_id,
                "task_id": task_id, "client_task_id": client_task_id,
                "turn_id": turn_id, "request_id": idempotency_key,
                "feature": feature,
                "source_id": source_id, "messages": list(messages),
                "response_contract": "ecom-runtime-v1",
            },
        )
        return RuntimeEcomIngressReceipt(
            outcome=receipt.outcome, command_id=receipt.command_id,
            run_id=receipt.run_id, session_id=receipt.session_id,
            runtime_owned=receipt.runtime_owned is True,
        )

    async def readback(
        self, *, conversation_id: str, org_id: str | None, user_id: str,
        idempotency_key: str,
    ) -> RuntimeEcomReadback:
        response = self._ingress._database.rpc(
            "read_agent_runtime_ecom_model_v1",
            {
                "p_conversation_id": conversation_id,
                "p_org_id": org_id,
                "p_user_id": user_id,
                "p_idempotency_key": idempotency_key,
            },
        ).execute()
        import inspect
        if inspect.isawaitable(response):
            response = await response
        data = getattr(response, "data", None)
        if not isinstance(data, dict) or not isinstance(data.get("outcome"), str):
            raise RuntimeError("RUNTIME_ECOM_READBACK_INVALID")
        return RuntimeEcomReadback(
            status=data["outcome"], run_id=data.get("run_id"),
            model_step_id=data.get("model_step_id"), content=data.get("content"),
            structured_content=data.get("structured_content"),
            reason_code=data.get("reason_code"),
        )


__all__ = ["RuntimeEcomIngressReceipt", "RuntimeEcomModelIngress", "RuntimeEcomReadback"]
