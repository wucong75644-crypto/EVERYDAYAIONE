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


class RuntimeEcomModelIngress:
    """Convert e-commerce model requests into the shared Runtime ingress."""

    def __init__(self, database: Any, *, require_runtime_owner: bool = True) -> None:
        self._ingress = RuntimeIngress(
            database, contract_revision=3,
            require_runtime_owner=require_runtime_owner,
        )

    async def submit(
        self, *, conversation_id: str, org_id: str | None, user_id: str,
        scope_kind: str, scope_id: str, agent_definition_id: str,
        agent_definition_revision: str, input_message_id: str,
        output_message_id: str, idempotency_key: str, model_id: str,
        messages: list[Mapping[str, Any]], feature: str,
        source_id: str, task_id: str | None = None,
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
            command_type="ecom_model_request",
            idempotency_key=idempotency_key,
            payload={
                "channel": "web", "model_id": model_id,
                "input_message_id": input_message_id,
                "output_message_id": output_message_id,
                "task_id": task_id, "feature": feature,
                "source_id": source_id, "messages": list(messages),
                "response_contract": "ecom-runtime-v1",
            },
        )
        return RuntimeEcomIngressReceipt(
            outcome=receipt.outcome, command_id=receipt.command_id,
            run_id=receipt.run_id, session_id=receipt.session_id,
            runtime_owned=receipt.runtime_owned is True,
        )


__all__ = ["RuntimeEcomIngressReceipt", "RuntimeEcomModelIngress"]
