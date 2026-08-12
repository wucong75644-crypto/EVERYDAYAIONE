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
    def __init__(self, database: Any, version_registry: Any | None = None,
                 *, contract_revision: int = 2,
                 require_runtime_owner: bool = False) -> None:
        self._database = database
        if contract_revision not in {2, 3}:
            raise ValueError("RUNTIME_INGRESS_CONTRACT_REVISION_INVALID")
        self._contract_revision = contract_revision
        self._require_runtime_owner = require_runtime_owner
        if version_registry is None:
            from services.agent.runtime.catalog import build_runtime_version_registry
            version_registry = build_runtime_version_registry()
        self._versions = version_registry

    async def submit(
        self, *, conversation_id: str, org_id: str | None, user_id: str,
        scope_kind: str, scope_id: str, agent_definition_id: str,
        agent_definition_revision: str, command_type: str,
        idempotency_key: str, payload: Mapping[str, object],
    ) -> RuntimeIngressReceipt:
        from core.config import get_settings
        settings = get_settings()
        through = str(payload.get("input_message_id") or payload.get("output_message_id") or "")
        if not through:
            raise RuntimeError("RUNTIME_INGRESS_THROUGH_MESSAGE_MISSING")
        from services.agent.runtime.model_resolution import (
            resolve_runtime_model, snapshot_from_resolution,
        )

        model_resolution = resolve_runtime_model(payload.get("model_id"))
        definition_hash = ""
        catalog_revision = ""
        if self._contract_revision == 2:
            agent, catalog = self._versions.resolve_for_agent(
                agent_definition_id, agent_definition_revision,
            )
            definition_hash = agent.definition_hash
            catalog_revision = catalog.revision
        else:
            fact_response = self._database.rpc("get_agent_runtime_definition_fact", {
                "p_agent_key": agent_definition_id,
                "p_definition_revision": agent_definition_revision,
            }).execute()
            if inspect.isawaitable(fact_response):
                fact_response = await fact_response
            fact = getattr(fact_response, "data", None)
            if not isinstance(fact, dict) or fact.get("outcome") == "not_found":
                raise RuntimeError("RUNTIME_DEFINITION_FACT_UNAVAILABLE")
            definition_hash = str(fact.get("definition_hash") or "")
            catalog_revision = str(fact.get("catalog_revision") or "")
            if not definition_hash or not catalog_revision:
                raise RuntimeError("RUNTIME_DEFINITION_FACT_INVALID")
        rpc_name = await self._resolve_rpc_name()
        if self._require_runtime_owner and rpc_name != "runtime_submit_ingress_v5":
            raise RuntimeError("RUNTIME_INGRESS_REQUIRED_CAPABILITY_UNAVAILABLE")
        owner_transition = (
            self._contract_revision == 3
            and bool(payload.get("task_id"))
            and rpc_name == "runtime_submit_ingress_v5"
        )
        if owner_transition:
            rpc_name = (
                "runtime_submit_ingress_v6_required"
                if self._require_runtime_owner
                else "runtime_submit_ingress_v5_owner_transition"
            )
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
        if owner_transition:
            rpc_params.update({
                "p_task_id": str(payload["task_id"]),
                "p_client_task_id": str(payload.get("client_task_id") or ""),
                "p_input_message_id": str(payload.get("input_message_id") or ""),
                "p_output_message_id": str(payload.get("output_message_id") or ""),
                "p_turn_id": str(payload.get("turn_id") or ""),
                "p_request_id": str(payload.get("request_id") or idempotency_key),
            })
        try:
            response = await self._execute_rpc(rpc_name, rpc_params)
        except Exception as error:
            if rpc_name != "runtime_submit_ingress_v4" or not self._is_missing_rpc(error):
                raise
            response = await self._execute_rpc("runtime_submit_ingress_v3", rpc_params)
        data = getattr(response, "data", None)
        if not isinstance(data, dict) or not isinstance(data.get("outcome"), str):
            raise RuntimeError("RUNTIME_INGRESS_RECEIPT_INVALID")
        raw_outcome = data["outcome"]
        outcome, owner_state, runtime_owned = _owner_transition_evidence(
            raw_outcome, owner_transition=owner_transition,
            runtime_owned=data.get("runtime_owned"),
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

    async def _resolve_rpc_name(self) -> str:
        if self._contract_revision == 2:
            return "runtime_submit_ingress_v2"
        try:
            capability = await self._execute_rpc(
                "get_agent_runtime_ingress_capability", {},
            )
        except Exception as error:
            if not self._is_missing_rpc(error):
                raise
            return await self._resolve_legacy_rpc_name()
        data = getattr(capability, "data", None)
        if not isinstance(data, dict) or data.get("ingress_version") != 5:
            raise RuntimeError("RUNTIME_INGRESS_V5_CAPABILITY_INVALID")
        return "runtime_submit_ingress_v5"

    async def _resolve_legacy_rpc_name(self) -> str:
        return "runtime_submit_ingress_v4"

    async def _execute_rpc(
        self, name: str, params: Mapping[str, object],
    ) -> Any:
        response = self._database.rpc(name, dict(params)).execute()
        if inspect.isawaitable(response):
            response = await response
        return response

    @staticmethod
    def _is_missing_rpc(error: Exception) -> bool:
        code = str(getattr(error, "code", ""))
        text = str(error).lower()
        return code in {"PGRST202", "42883"} or any(
            marker in text for marker in (
                "could not find the function", "undefined function",
                "function does not exist", "does not exist",
            )
        )


def _owner_transition_evidence(
    raw_outcome: str, *, owner_transition: bool, runtime_owned: object,
) -> tuple[str, str | None, bool | None]:
    """Normalize 227.14 owner markers without hiding the database outcome."""
    if raw_outcome in {"marked", "already_runtime_owned"}:
        outcome = "created" if raw_outcome == "marked" else "already_exists"
        return outcome, "runtime_owned", True
    if raw_outcome in {"restored", "already_actor_owned"}:
        return "fallback_to_legacy", "legacy_fallback", False
    if raw_outcome == "runtime_required_unavailable":
        return raw_outcome, "runtime_required_unavailable", False
    if raw_outcome in {
        "ingress_disabled", "org_not_enabled", "subject_not_enabled", "fenced",
    }:
        return raw_outcome, "gate_blocked", False
    if owner_transition and raw_outcome in {"created", "already_exists"}:
        return raw_outcome, "runtime_owned", True
    if isinstance(runtime_owned, bool):
        state = "runtime_owned" if runtime_owned else "legacy_fallback"
        return raw_outcome, state, runtime_owned
    return raw_outcome, None, None
