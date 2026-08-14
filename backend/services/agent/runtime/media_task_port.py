"""Attempt-fenced Runtime media provider request bridge."""

from __future__ import annotations

from typing import Any, Mapping

from core.db_scope import DatabaseAccessKind, database_scope_from_client
from services.agent.runtime.domain import ActionAttempt


class RuntimeMediaTaskPort:
    """Read only the server-normalized KIE request for one owned attempt."""

    def __init__(self, database: Any) -> None:
        scope = database_scope_from_client(database)
        if scope is None or scope.access_kind is not DatabaseAccessKind.AGENT_RUNTIME:
            raise ValueError("WORKER_SCOPED_DATABASE_CLIENT_REQUIRED")
        self._database = database

    async def prepare(
        self, attempt: ActionAttempt, *, kind: str,
    ) -> Mapping[str, object]:
        self._validate(attempt, kind)
        response = await self._database.rpc(
            "prepare_agent_runtime_media_dispatch_v1",
            self.attempt_params(attempt),
        ).execute()
        result = _mapping(response.data, "MEDIA_PREPARE_RESPONSE_INVALID")
        if result.get("outcome") not in {
            "prepared", "already_prepared", "already_exists",
        }:
            raise RuntimeError("MEDIA_DISPATCH_PREPARE_FAILED")
        return await self.read(attempt, kind=kind)

    async def read(
        self, attempt: ActionAttempt, *, kind: str,
        owner_token: str | None = None,
        expected_state_version: int | None = None,
    ) -> Mapping[str, object]:
        self._validate(attempt, kind)
        params = self.attempt_params(attempt)
        if owner_token is not None:
            params["p_owner_token"] = owner_token
        if expected_state_version is not None:
            params["p_expected_attempt_version"] = expected_state_version
        response = await self._database.rpc(
            "read_agent_runtime_media_provider_request_v1",
            params,
        ).execute()
        result = _mapping(response.data, "MEDIA_PROVIDER_REQUEST_RESPONSE_INVALID")
        if result.get("outcome") != "found" or result.get("kind") != kind:
            raise RuntimeError("MEDIA_PROVIDER_REQUEST_UNAVAILABLE")
        provider_request = _mapping(
            result.get("provider_request"),
            "MEDIA_PROVIDER_REQUEST_RESPONSE_INVALID",
        )
        _validate_provider_request(provider_request)
        provider_hash = _sha256(
            result.get("provider_request_hash"),
            "MEDIA_PROVIDER_REQUEST_HASH_INVALID",
        )
        source = result.get("source")
        if source not in {"media_ingress", "model_loop"}:
            raise RuntimeError("MEDIA_PROVIDER_REQUEST_SOURCE_INVALID")
        return {
            "kind": kind,
            "source": source,
            "provider_request": dict(provider_request),
            "provider_request_hash": provider_hash,
        }

    @staticmethod
    def attempt_params(attempt: ActionAttempt) -> dict[str, object]:
        return {
            "p_action_id": str(attempt.action_id),
            "p_attempt_id": str(attempt.attempt_id),
            "p_worker_id": attempt.worker_id,
            "p_owner_token": str(attempt.lease.fencing_token),
            "p_expected_attempt_version": attempt.state_version,
            "p_request_hash": attempt.request_hash,
        }

    @staticmethod
    def _validate(attempt: ActionAttempt, kind: str) -> None:
        if kind not in {"image", "video"}:
            raise RuntimeError("MEDIA_KIND_NOT_SUPPORTED")
        if not attempt.run_id or not attempt.session_id:
            raise RuntimeError("MEDIA_ACTION_CONTEXT_REQUIRED")


def build_runtime_media_task_port(database: Any) -> RuntimeMediaTaskPort:
    return RuntimeMediaTaskPort(database)


def _mapping(value: object, error: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise RuntimeError(error)
    return value


def _sha256(value: object, error: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(
        char not in "0123456789abcdef" for char in value
    ):
        raise RuntimeError(error)
    return value


def _validate_provider_request(value: object) -> None:
    forbidden = {
        "task_id", "user_id", "org_id", "credit_transaction_id",
        "reserved_credits", "currency", "runtime_task", "internal_facts",
    }
    if isinstance(value, Mapping):
        if forbidden.intersection(value):
            raise RuntimeError("MEDIA_PROVIDER_REQUEST_RESPONSE_INVALID")
        for item in value.values():
            _validate_provider_request(item)
    elif isinstance(value, list):
        for item in value:
            _validate_provider_request(item)


__all__ = ["RuntimeMediaTaskPort", "build_runtime_media_task_port"]
