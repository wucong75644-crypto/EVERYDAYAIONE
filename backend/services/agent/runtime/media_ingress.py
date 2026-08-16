"""Runtime ingress for prepared image/video tasks.

The ingress only persists a Runtime Action. Provider submission remains owned
by ActionLoop and the media specialist executor.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from services.agent.runtime.catalog.image_release import IMAGE_CATALOG_REVISION


@dataclass(frozen=True, kw_only=True)
class RuntimeMediaIngressReceipt:
    outcome: str
    action_id: str | None = None
    run_id: str | None = None
    model_step_id: str | None = None
    runtime_owned: bool = False
    readiness_revision: int | None = None

    @property
    def accepted(self) -> bool:
        return self.outcome in {"created", "already_exists"}


@dataclass(frozen=True, kw_only=True)
class RuntimeMediaBatchIngressReceipt:
    outcome: str
    receipts: tuple[RuntimeMediaIngressReceipt, ...] = ()
    runtime_owned: bool = False
    readiness_revision: int | None = None

    @property
    def accepted(self) -> bool:
        return self.outcome in {"created", "already_exists"}

    @property
    def run_id(self) -> str | None:
        return self.receipts[0].run_id if self.receipts else None


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
        forbidden = {
            "task_id", "user_id", "org_id", "credit_transaction_id",
            "reserved_credits", "currency", "image_urls", "input_urls",
            "runtime_task", "internal_facts",
        }
        if forbidden.intersection(request):
            raise RuntimeError("RUNTIME_MEDIA_INTERNAL_ARGUMENT_FORBIDDEN")
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
            "p_catalog_revision": (
                IMAGE_CATALOG_REVISION if kind == "image" else "runtime-media-v1"
            ),
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
            readiness_revision=(
                data.get("readiness_revision")
                if isinstance(data.get("readiness_revision"), int) else None
            ),
        )

    async def submit_image_batch(
        self, *, conversation_id: str, org_id: str | None, user_id: str,
        scope_kind: str, scope_id: str, agent_definition_id: str,
        agent_definition_revision: str, input_message_id: str,
        output_message_id: str, turn_id: str | None, batch_id: str,
        model_id: str, items: Sequence[Mapping[str, object]],
        model_provider: str = "runtime", model_revision: str = "media-runtime-v1",
    ) -> RuntimeMediaBatchIngressReceipt:
        if not 1 <= len(items) <= 10:
            raise RuntimeError("RUNTIME_MEDIA_IMAGE_BATCH_COUNT_INVALID")
        forbidden = {
            "task_id", "user_id", "org_id", "credit_transaction_id",
            "reserved_credits", "currency", "image_urls", "input_urls",
            "runtime_task", "internal_facts",
        }
        encoded_items = []
        for item in items:
            task_id = item.get("task_id")
            idempotency_key = item.get("idempotency_key")
            request = item.get("request")
            if (
                not isinstance(task_id, str) or not task_id
                or not isinstance(idempotency_key, str) or not idempotency_key
                or not isinstance(request, Mapping)
            ):
                raise RuntimeError("RUNTIME_MEDIA_IMAGE_BATCH_ITEM_INVALID")
            if forbidden.intersection(request):
                raise RuntimeError("RUNTIME_MEDIA_INTERNAL_ARGUMENT_FORBIDDEN")
            encoded_items.append({
                "task_id": task_id,
                "idempotency_key": idempotency_key,
                "arguments": dict(request),
            })
        params = {
            "p_conversation_id": conversation_id,
            "p_org_id": org_id,
            "p_user_id": user_id,
            "p_scope_kind": scope_kind,
            "p_scope_id": scope_id,
            "p_created_by_user_id": user_id,
            "p_agent_definition_id": agent_definition_id,
            "p_agent_definition_revision": agent_definition_revision,
            "p_input_message_id": input_message_id,
            "p_output_message_id": output_message_id,
            "p_turn_id": turn_id,
            "p_batch_id": batch_id,
            "p_model_id": model_id,
            "p_model_provider": model_provider,
            "p_model_revision": model_revision,
            "p_catalog_revision": IMAGE_CATALOG_REVISION,
            "p_policy_revision": "runtime-media-v1",
            "p_items": encoded_items,
        }
        response = self._database.rpc(
            "submit_agent_runtime_media_image_batch_v2", params,
        ).execute()
        if inspect.isawaitable(response):
            response = await response
        data = getattr(response, "data", None)
        if not isinstance(data, dict) or not isinstance(data.get("outcome"), str):
            raise RuntimeError("RUNTIME_MEDIA_IMAGE_BATCH_RECEIPT_INVALID")
        raw_results = data.get("results", [])
        if not isinstance(raw_results, list):
            raise RuntimeError("RUNTIME_MEDIA_IMAGE_BATCH_RECEIPT_INVALID")
        receipts = tuple(self._receipt(result) for result in raw_results)
        runtime_owned = bool(data.get("runtime_owned", False))
        if runtime_owned and len(receipts) != len(items):
            raise RuntimeError("RUNTIME_MEDIA_IMAGE_BATCH_RECEIPT_INVALID")
        return RuntimeMediaBatchIngressReceipt(
            outcome=data["outcome"], receipts=receipts,
            runtime_owned=runtime_owned,
            readiness_revision=(
                data.get("readiness_revision")
                if isinstance(data.get("readiness_revision"), int) else None
            ),
        )

    @staticmethod
    def _receipt(data: object) -> RuntimeMediaIngressReceipt:
        if not isinstance(data, dict) or not isinstance(data.get("outcome"), str):
            raise RuntimeError("RUNTIME_MEDIA_IMAGE_BATCH_RECEIPT_INVALID")
        return RuntimeMediaIngressReceipt(
            outcome=data["outcome"], action_id=data.get("action_id"),
            run_id=data.get("run_id"), model_step_id=data.get("model_step_id"),
            runtime_owned=bool(data.get("runtime_owned", False)),
            readiness_revision=(
                data.get("readiness_revision")
                if isinstance(data.get("readiness_revision"), int) else None
            ),
        )


__all__ = [
    "RuntimeMediaBatchIngressReceipt", "RuntimeMediaIngress",
    "RuntimeMediaIngressReceipt",
]
