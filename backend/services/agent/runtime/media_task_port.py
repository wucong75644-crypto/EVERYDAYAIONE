"""Attempt-fenced bridge from Runtime image Actions to existing media Tasks."""

from __future__ import annotations

from typing import Any, Mapping

from core.db_scope import DatabaseAccessKind, database_scope_from_client
from services.agent.runtime.domain import ActionAttempt


class RuntimeMediaTaskPort:
    """Prepare and read server-owned Task/credit bindings through narrow RPCs."""

    def __init__(self, database: Any) -> None:
        scope = database_scope_from_client(database)
        if scope is None or scope.access_kind is not DatabaseAccessKind.AGENT_RUNTIME:
            raise ValueError("WORKER_SCOPED_DATABASE_CLIENT_REQUIRED")
        self._database = database

    async def prepare(
        self, attempt: ActionAttempt, *, kind: str,
    ) -> Mapping[str, object]:
        self._validate(attempt, kind)
        manifest_response = await self._database.rpc(
            "read_agent_runtime_media_manifest_v1",
            self._attempt_params(attempt),
        ).execute()
        manifest = _mapping(manifest_response.data, "MEDIA_MANIFEST_RESPONSE_INVALID")
        if manifest.get("outcome") != "found":
            raise RuntimeError("MEDIA_MANIFEST_UNAVAILABLE")
        manifest_hash = _required_text(
            manifest.get("reference_manifest_hash"),
            "MEDIA_MANIFEST_HASH_REQUIRED",
        )
        response = await self._database.rpc(
            "prepare_agent_runtime_media_batch_v1", {
                **self._attempt_params(attempt),
                "p_reference_manifest_hash": manifest_hash,
            },
        ).execute()
        prepared = _mapping(response.data, "MEDIA_PREPARE_RESPONSE_INVALID")
        if prepared.get("outcome") not in {"prepared", "already_prepared"}:
            raise RuntimeError("MEDIA_BATCH_PREPARE_FAILED")
        return _binding(prepared.get("binding"))

    async def read(
        self, attempt: ActionAttempt, *, kind: str,
    ) -> Mapping[str, object]:
        self._validate(attempt, kind)
        response = await self._database.rpc(
            "read_agent_runtime_media_binding_v1",
            self._attempt_params(attempt),
        ).execute()
        result = _mapping(response.data, "MEDIA_BINDING_RESPONSE_INVALID")
        if result.get("outcome") != "found":
            raise RuntimeError("MEDIA_BINDING_NOT_PREPARED")
        binding = dict(_binding(result.get("binding")))
        request_params = result.get("request_params")
        if not isinstance(request_params, Mapping):
            raise RuntimeError("MEDIA_BINDING_RESPONSE_INVALID")
        binding["request_params"] = dict(request_params)
        return binding

    @staticmethod
    def _validate(attempt: ActionAttempt, kind: str) -> None:
        if kind != "image":
            raise RuntimeError("MEDIA_KIND_NOT_SUPPORTED_BY_BINDING_V1")
        if not attempt.run_id or not attempt.session_id:
            raise RuntimeError("MEDIA_ACTION_CONTEXT_REQUIRED")

    @staticmethod
    def _attempt_params(attempt: ActionAttempt) -> dict[str, object]:
        return {
            "p_action_id": str(attempt.action_id),
            "p_attempt_id": str(attempt.attempt_id),
            "p_worker_id": attempt.worker_id,
            "p_execution_token": str(attempt.lease.fencing_token),
            "p_expected_attempt_version": attempt.state_version,
            "p_request_hash": attempt.request_hash,
        }


def build_runtime_media_task_port(database: Any) -> RuntimeMediaTaskPort:
    """Create the Runtime Worker-scoped media binding port."""
    return RuntimeMediaTaskPort(database)


def _mapping(value: object, error: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise RuntimeError(error)
    return value


def _binding(value: object) -> Mapping[str, object]:
    result = _mapping(value, "MEDIA_BINDING_RESPONSE_INVALID")
    required = (
        "action_id", "task_id", "run_id", "model_step_id", "batch_hash",
        "action_index", "action_arguments_hash", "action_request_hash",
        "input_message_id", "output_message_id",
        "credit_transaction_id", "pricing_model_id", "pricing_resolution",
        "provider_request_hash", "unit_credits", "reference_manifest_hash",
    )
    if any(result.get(key) is None for key in required):
        raise RuntimeError("MEDIA_BINDING_RESPONSE_INVALID")
    return dict(result)


def _required_text(value: object, error: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(error)
    return value.strip()


__all__ = ["RuntimeMediaTaskPort", "build_runtime_media_task_port"]
