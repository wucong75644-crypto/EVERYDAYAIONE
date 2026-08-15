"""Single production ingress into a durable Runtime Session and Command."""

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
    effective_toolset_revision: str | None = None
    effective_toolset_hash: str | None = None
    gate_state: str | None = None
    owner_state: str | None = None
    runtime_owned: bool | None = None
    raw_outcome: str | None = None

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
        from core.config import get_settings
        settings = get_settings()
        required = {
            name: str(payload.get(name) or "")
            for name in (
                "task_id", "client_task_id", "input_message_id",
                "output_message_id", "turn_id", "request_id",
            )
        }
        if any(not value for value in required.values()):
            raise RuntimeError("RUNTIME_INGRESS_OWNER_BINDING_MISSING")
        if required["request_id"] != idempotency_key:
            raise RuntimeError("RUNTIME_INGRESS_REQUEST_ID_MISMATCH")
        through = required["input_message_id"]
        from services.agent.runtime.model_resolution import (
            resolve_runtime_model, snapshot_from_resolution,
        )

        model_resolution = resolve_runtime_model(payload.get("model_id"))
        fact_response = await self._execute_rpc(
            "get_agent_runtime_definition_fact", {
                "p_agent_key": agent_definition_id,
                "p_definition_revision": agent_definition_revision,
            },
        )
        fact = getattr(fact_response, "data", None)
        if not isinstance(fact, dict) or fact.get("outcome") == "not_found":
            raise RuntimeError("RUNTIME_DEFINITION_FACT_UNAVAILABLE")
        definition_hash = str(fact.get("definition_hash") or "")
        catalog_revision = str(fact.get("catalog_revision") or "")
        if not definition_hash or not catalog_revision:
            raise RuntimeError("RUNTIME_DEFINITION_FACT_INVALID")
        rpc_params = {
            "p_conversation_id": conversation_id, "p_org_id": org_id,
            "p_user_id": user_id, "p_scope_kind": scope_kind,
            "p_scope_id": scope_id, "p_created_by_user_id": user_id,
            "p_agent_definition_id": agent_definition_id,
            "p_agent_definition_revision": agent_definition_revision,
            "p_agent_definition_hash": definition_hash,
            "p_command_type": command_type,
            "p_idempotency_key": idempotency_key,
            "p_channel": str(payload.get("channel") or "web"),
            "p_through_message_id": through,
            "p_base_context_revision": f"message:{through}",
            "p_effective_toolset_revision": catalog_revision,
            "p_effective_toolset_hash": None,
            "p_config_snapshot": snapshot_from_resolution(model_resolution),
            "p_capability_snapshot": {"requested_groups": ["code"]},
            "p_release_revision": settings.agent_runtime_release_revision,
            "p_payload": dict(payload),
        }
        rpc_params.update({
            "p_task_id": required["task_id"],
            "p_client_task_id": required["client_task_id"],
            "p_input_message_id": required["input_message_id"],
            "p_output_message_id": required["output_message_id"],
            "p_turn_id": required["turn_id"],
            "p_request_id": required["request_id"],
        })
        response = await self._execute_rpc(
            "submit_runtime_ingress_required_v1", rpc_params,
        )
        data = getattr(response, "data", None)
        if not isinstance(data, dict) or not isinstance(data.get("outcome"), str):
            raise RuntimeError("RUNTIME_INGRESS_RECEIPT_INVALID")
        raw_outcome = data["outcome"]
        outcome, owner_state, runtime_owned = _required_owner_evidence(
            raw_outcome, data.get("runtime_owned"),
        )
        return RuntimeIngressReceipt(
            outcome=outcome, session_id=data.get("session_id"),
            command_id=data.get("entity_id"),
            run_id=data.get("result_entity_id"),
            effective_toolset_revision=data.get("effective_toolset_revision"),
            effective_toolset_hash=data.get("effective_toolset_hash"),
            gate_state=data.get("gate_state"), owner_state=owner_state,
            runtime_owned=runtime_owned, raw_outcome=raw_outcome,
        )

    async def _execute_rpc(
        self, name: str, params: Mapping[str, object],
    ) -> Any:
        response = self._database.rpc(name, dict(params)).execute()
        if inspect.isawaitable(response):
            response = await response
        return response

def _required_owner_evidence(
    raw_outcome: str, runtime_owned: object,
) -> tuple[str, str, bool]:
    """Normalize the single required-owner contract."""
    if raw_outcome in {"marked", "already_runtime_owned"}:
        outcome = "created" if raw_outcome == "marked" else "already_exists"
        return outcome, "runtime_owned", True
    if raw_outcome == "runtime_required_unavailable":
        return raw_outcome, "runtime_required_unavailable", False
    if raw_outcome in {"created", "already_exists"} and runtime_owned is True:
        return raw_outcome, "runtime_owned", True
    raise RuntimeError(f"RUNTIME_REQUIRED_OWNER_{raw_outcome}")
