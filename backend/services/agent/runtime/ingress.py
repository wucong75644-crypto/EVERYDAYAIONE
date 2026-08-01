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
    def __init__(self, database: Any, version_registry: Any | None = None) -> None:
        self._database = database
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
        from services.agent.runtime.catalog import EffectiveToolset
        from core.config import get_settings
        settings = get_settings()
        through = str(payload.get("input_message_id") or payload.get("output_message_id") or "")
        if not through:
            raise RuntimeError("RUNTIME_INGRESS_THROUGH_MESSAGE_MISSING")
        agent, catalog = self._versions.resolve_for_agent(
            agent_definition_id, agent_definition_revision,
        )
        toolset = EffectiveToolset.build(
            agent=agent, catalog=catalog, scope=scope_kind,
            channel=str(payload.get("channel") or "web"),
            entitled_groups=frozenset({"code"}),
            authorized_names=frozenset({"code_execute"}),
        )
        response = self._database.rpc("runtime_submit_ingress_v2", {
            "p_conversation_id": conversation_id, "p_org_id": org_id,
            "p_user_id": user_id, "p_scope_kind": scope_kind,
            "p_scope_id": scope_id, "p_created_by_user_id": user_id,
            "p_agent_definition_id": agent_definition_id,
            "p_agent_definition_revision": agent_definition_revision,
            "p_agent_definition_hash": agent.definition_hash,
            "p_command_type": command_type,
            "p_idempotency_key": idempotency_key,
            "p_channel": str(payload.get("channel") or "web"),
            "p_through_message_id": through,
            "p_base_context_revision": f"message:{through}",
            "p_effective_toolset_revision": catalog.revision,
            "p_effective_toolset_hash": toolset.toolset_hash,
            "p_config_snapshot": {"model_id": payload.get("model_id") or ""},
            "p_capability_snapshot": {"requested_groups": ["code"]},
            "p_release_revision": settings.agent_runtime_release_revision,
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
