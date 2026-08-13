"""Runtime ingress for prepared image/video tasks.

The ingress only persists a Runtime Action. Provider submission remains owned
by ActionLoop and the media specialist executor.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True, kw_only=True)
class RuntimeMediaIngressReceipt:
    outcome: str
    action_id: str | None = None
    run_id: str | None = None
    model_step_id: str | None = None
    runtime_owned: bool = False

    @property
    def accepted(self) -> bool:
        return self.outcome in {"created", "already_exists"}


class RuntimeMediaIngress:
    """Create one executable Runtime media Action for a prepared task."""

    def __init__(self, database: Any) -> None:
        if database is None:
            raise RuntimeError("RUNTIME_MEDIA_DATABASE_REQUIRED")
        self._database = database

    async def submit(
        self, *, conversation_id: str, org_id: str | None, user_id: str,
        scope_kind: str, scope_id: str, agent_definition_id: str,
        agent_definition_revision: str, task_id: str, input_message_id: str,
        output_message_id: str, turn_id: str | None, idempotency_key: str,
        kind: str, request: Mapping[str, object], model_id: str,
        model_provider: str = "runtime", model_revision: str = "media-runtime-v1",
    ) -> RuntimeMediaIngressReceipt:
        if kind not in {"image", "video"}:
            raise RuntimeError("RUNTIME_MEDIA_KIND_INVALID")
        tool_name = f"generate_{kind}"
        params = {
            "p_conversation_id": conversation_id,
            "p_org_id": org_id,
            "p_user_id": user_id,
            "p_scope_kind": scope_kind,
            "p_scope_id": scope_id,
            "p_created_by_user_id": user_id,
            "p_agent_definition_id": agent_definition_id,
            "p_agent_definition_revision": agent_definition_revision,
            "p_task_id": task_id,
            "p_input_message_id": input_message_id,
            "p_output_message_id": output_message_id,
            "p_turn_id": turn_id,
            "p_tool_name": tool_name,
            "p_arguments": dict(request),
            "p_model_id": model_id,
            "p_model_provider": model_provider,
            "p_model_revision": model_revision,
            "p_catalog_revision": "runtime-media-v1",
            "p_policy_revision": "runtime-media-v1",
            "p_idempotency_key": idempotency_key,
        }
        response = self._database.rpc(
            "submit_agent_runtime_media_action_v1", params,
        ).execute()
        if inspect.isawaitable(response):
            response = await response
        data = getattr(response, "data", None)
        if not isinstance(data, dict) or not isinstance(data.get("outcome"), str):
            raise RuntimeError("RUNTIME_MEDIA_INGRESS_RECEIPT_INVALID")
        return RuntimeMediaIngressReceipt(
            outcome=data["outcome"], action_id=data.get("action_id"),
            run_id=data.get("run_id"), model_step_id=data.get("model_step_id"),
            runtime_owned=bool(data.get("runtime_owned", False)),
        )


__all__ = ["RuntimeMediaIngress", "RuntimeMediaIngressReceipt"]
