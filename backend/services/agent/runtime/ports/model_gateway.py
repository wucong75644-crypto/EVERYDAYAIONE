"""Stable Runtime-side receipt for atomic Model Gateway dispatch binding."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class ModelGatewayDispatchOutcome(StrEnum):
    DISPATCHING = "dispatching"
    ALREADY_DISPATCHING = "already_dispatching"
    NOT_FOUND = "not_found"


@dataclass(frozen=True, kw_only=True)
class ModelGatewayDispatchBinding:
    operation_id: str
    request_id: str
    org_id: str | None
    user_id: str
    session_id: str
    run_id: str
    model_step_id: str
    model_attempt_id: str
    worker_id: str
    execution_token: str
    request_hash: str
    attempt_state_version: int
    model_id: str
    provider: str
    provider_revision: str
    model_revision: str
    purpose: str
    tenant_kill_epoch: int
    provider_kill_epoch: int
    capability_kill_epoch: int


@dataclass(frozen=True, kw_only=True)
class ModelGatewayDispatchReceipt:
    outcome: ModelGatewayDispatchOutcome
    binding: ModelGatewayDispatchBinding | None = None


class ModelGatewayDispatchRepositoryPort(Protocol):
    async def start_dispatch(
        self, *, request_id: str, session_id: str, run_id: str,
        model_step_id: str, model_attempt_id: str,
        run_execution_token: str, request_hash: str,
        expected_attempt_version: int, model_id: str, provider: str,
        provider_revision: str, model_revision: str, purpose: str,
    ) -> ModelGatewayDispatchReceipt: ...


__all__ = [
    "ModelGatewayDispatchBinding",
    "ModelGatewayDispatchOutcome",
    "ModelGatewayDispatchReceipt",
    "ModelGatewayDispatchRepositoryPort",
]
