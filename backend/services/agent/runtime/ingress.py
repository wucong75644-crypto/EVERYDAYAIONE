"""Atomic API/WeCom ingress into durable Runtime Session and Command."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class RuntimeIngressReceipt:
    outcome: str
    session_id: str | None = None
    command_id: str | None = None
    run_id: str | None = None

    @property
    def accepted(self) -> bool:
        return self.outcome in {"created", "already_exists"}


class RuntimeIngress:
    def __init__(self, database: Any) -> None:
        self._database = database

    async def submit(
        self, *, conversation_id: str, org_id: str | None, user_id: str,
        scope_kind: str, scope_id: str, agent_definition_id: str,
        agent_definition_revision: str, command_type: str,
        idempotency_key: str, payload: Mapping[str, object],
    ) -> RuntimeIngressReceipt:
        response = self._database.rpc("runtime_submit_ingress", {
            "p_conversation_id": conversation_id, "p_org_id": org_id,
            "p_user_id": user_id, "p_scope_kind": scope_kind,
            "p_scope_id": scope_id, "p_created_by_user_id": user_id,
            "p_agent_definition_id": agent_definition_id,
            "p_agent_definition_revision": agent_definition_revision,
            "p_command_type": command_type,
            "p_idempotency_key": idempotency_key,
            "p_payload": dict(payload),
        }).execute()
        if inspect.isawaitable(response):
            response = await response
        data = getattr(response, "data", None)
        if not isinstance(data, dict) or not isinstance(data.get("outcome"), str):
            raise RuntimeError("RUNTIME_INGRESS_RECEIPT_INVALID")
        return RuntimeIngressReceipt(
            outcome=data["outcome"], session_id=data.get("session_id"),
            command_id=data.get("entity_id"),
            run_id=data.get("result_entity_id"),
        )
