"""Runtime bridge to the existing prepared media task lifecycle.

This adapter only owns the handoff between a Runtime Provider receipt and the
already-created application task.  Provider submission, readback and cancel
remain methods on the Runtime provider; this port never retries a Provider.
"""

from __future__ import annotations

from typing import Any, Mapping

from services.agent.runtime.domain import ActionAttempt
from services.agent.runtime.executors.specialist_contracts import ProviderReceipt
from services.generation_lifecycle import GenerationLifecycle


class RuntimeMediaTaskPort:
    """Attach Runtime-owned KIE work to an existing generation task."""

    def __init__(self, database: Any) -> None:
        if database is None:
            raise RuntimeError("MEDIA_TASK_DATABASE_REQUIRED")
        self._lifecycle = GenerationLifecycle(database)

    async def prepare(
        self, attempt: ActionAttempt, request: Mapping[str, object], *, kind: str,
    ) -> Mapping[str, object]:
        values = self._identity(attempt, request, kind=kind)
        return {
            "task_id": values["task_id"],
            "user_id": values["user_id"],
            "org_id": values["org_id"],
            "credit_transaction_id": values["credit_transaction_id"],
            "kind": kind,
        }

    async def attach(
        self, attempt: ActionAttempt, request: Mapping[str, object],
        receipt: ProviderReceipt, *, kind: str,
    ) -> Mapping[str, object]:
        values = self._identity(attempt, request, kind=kind)
        external_task_id = receipt.provider_task_ref
        if not external_task_id:
            raise RuntimeError("MEDIA_PROVIDER_TASK_REF_REQUIRED")
        await self._lifecycle.attach_external_task_async(
            task_id=values["task_id"],
            external_task_id=external_task_id,
            credit_transaction_id=values["credit_transaction_id"],
            org_id=values["org_id"],
            user_id=values["user_id"],
            provider=receipt.provider,
            actual_model_id=_optional_text(request.get("model")),
            actual_request_params=_public_request(request),
        )
        return {
            "task_id": values["task_id"],
            "external_task_id": external_task_id,
            "attached": True,
        }

    def _identity(
        self, attempt: ActionAttempt, request: Mapping[str, object], *, kind: str,
    ) -> dict[str, str | None]:
        if kind not in {"image", "video"}:
            raise RuntimeError("MEDIA_KIND_INVALID")
        if request.get("kind") not in {None, kind}:
            raise RuntimeError("MEDIA_KIND_MISMATCH")
        task_id = _required_text(request.get("task_id"), "MEDIA_TASK_ID_REQUIRED")
        user_id = _optional_text(request.get("user_id")) or _optional_text(
            attempt.scope.user_id,
        )
        if not user_id:
            raise RuntimeError("MEDIA_USER_ID_REQUIRED")
        org_id = _optional_text(request.get("org_id"))
        if org_id is not None and org_id != attempt.scope.org_id:
            raise RuntimeError("MEDIA_ORG_SCOPE_CONFLICT")
        transaction_id = _required_text(
            request.get("credit_transaction_id"),
            "MEDIA_CREDIT_TRANSACTION_REQUIRED",
        )
        return {
            "task_id": task_id, "user_id": user_id,
            "org_id": org_id or attempt.scope.org_id,
            "credit_transaction_id": transaction_id,
        }


def build_runtime_media_task_port(database: Any) -> RuntimeMediaTaskPort:
    """Create the only Runtime adapter for prepared media task attachment."""
    return RuntimeMediaTaskPort(database)


def _required_text(value: object, error: str) -> str:
    text = _optional_text(value)
    if text is None:
        raise RuntimeError(error)
    return text


def _optional_text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _public_request(request: Mapping[str, object]) -> dict[str, object]:
    forbidden = {"task_id", "user_id", "org_id", "credit_transaction_id"}
    return {key: value for key, value in request.items() if key not in forbidden}


__all__ = ["RuntimeMediaTaskPort", "build_runtime_media_task_port"]
