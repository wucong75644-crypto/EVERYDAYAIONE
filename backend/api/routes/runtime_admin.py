"""Super-admin runtime operations through narrow, audited PostgreSQL RPCs."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, Field

from api.deps import CurrentUserId, Database
from api.routes.admin_users_helpers import _require_super_admin
from core.database import get_runtime_admin_db
from core.db_scope import (
    DatabaseAccessKind, DatabaseScope, ScopedDatabaseClient,
)
from services.agent.runtime.status import RuntimeStatusSnapshot


router = APIRouter(prefix="/admin/agent-runtime", tags=["agent-runtime-admin"])


class DeadProjectionRecoveryRequest(BaseModel):
    org_id: UUID
    expected_status: str = Field(pattern="^dead$")
    expected_recovery_version: int = Field(ge=0)
    expected_attempt_count: int = Field(ge=8)
    recovery_request_id: UUID
    reason: str = Field(min_length=1, max_length=500)
    not_before: datetime


class RuntimeControlRequest(BaseModel):
    org_id: UUID
    expected_state_version: int = Field(ge=0)
    patch: dict[str, object]
    reason: str = Field(min_length=1, max_length=500)


class RuntimeRolloutRequest(BaseModel):
    org_id: UUID
    enabled: bool
    reason: str = Field(min_length=1, max_length=500)


class ProviderOperationsQuery(BaseModel):
    provider: str | None = Field(default=None, max_length=200)
    capability: str | None = Field(default=None, max_length=200)
    state: Literal["accepted", "unknown", "reconcile_required"] | None = None
    created_after: datetime | None = None
    created_before: datetime | None = None
    limit: int = Field(default=100, ge=1, le=200)


class ProviderOperationRequest(BaseModel):
    operation: Literal["readback", "reconcile", "cancel"]
    expected_state_version: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=500)


def _admin_db(user_id: str, org_id: str | None, request_id: str):
    return ScopedDatabaseClient(
        get_runtime_admin_db(),
        DatabaseScope(
            actor_user_id=user_id,
            org_id=org_id,
            access_kind=DatabaseAccessKind.RUNTIME_ADMIN,
            request_id=request_id,
        ),
    )


@router.get("/status")
async def runtime_status(
    user_id: CurrentUserId,
    db: Database,
    org_id: UUID,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
) -> dict:
    _require_super_admin(user_id, db)
    response = _admin_db(
        user_id, str(org_id), idempotency_key,
    ).rpc("get_agent_runtime_admin_status", {}).execute()
    snapshot = RuntimeStatusSnapshot.from_admin_payload(
        response.data, tenant_id=str(org_id),
    ).to_dict()
    return {"success": True, "data": response.data, "snapshot": snapshot}


@router.get("/provider-operations")
async def provider_operations(
    user_id: CurrentUserId,
    db: Database,
    org_id: UUID,
    query: ProviderOperationsQuery = Depends(),
) -> dict:
    _require_super_admin(user_id, db)
    response = _admin_db(user_id, str(org_id), "provider-operations-read").rpc(
        "list_agent_runtime_provider_operations", {
            "p_org_id": str(org_id), "p_provider": query.provider,
            "p_capability": query.capability, "p_state": query.state,
            "p_created_after": query.created_after,
            "p_created_before": query.created_before, "p_limit": query.limit,
        },
    ).execute()
    return {"success": True, "data": response.data}


@router.post("/provider-operations/{submission_id}")
async def request_provider_operation(
    submission_id: UUID,
    body: ProviderOperationRequest,
    user_id: CurrentUserId,
    db: Database,
    org_id: UUID,
    idempotency_key: UUID = Header(..., alias="Idempotency-Key"),
) -> dict:
    _require_super_admin(user_id, db)
    response = _admin_db(user_id, str(org_id), str(idempotency_key)).rpc(
        "request_agent_runtime_provider_operation", {
            "p_request_id": str(idempotency_key), "p_org_id": str(org_id),
            "p_submission_id": str(submission_id), "p_operation": body.operation,
            "p_expected_state_version": body.expected_state_version,
            "p_reason": body.reason, "p_idempotency_key": str(idempotency_key),
        },
    ).execute()
    return {"success": True, "data": response.data}


@router.post("/control")
async def update_runtime_control(
    body: RuntimeControlRequest,
    user_id: CurrentUserId,
    db: Database,
    idempotency_key: UUID = Header(..., alias="Idempotency-Key"),
) -> dict:
    _require_super_admin(user_id, db)
    response = _admin_db(
        user_id, str(body.org_id), str(idempotency_key),
    ).rpc("set_agent_runtime_control", {
        "p_request_id": str(idempotency_key),
        "p_expected_state_version": body.expected_state_version,
        "p_patch": body.patch,
        "p_reason": body.reason,
    }).execute()
    return {"success": True, "data": response.data}


@router.post("/rollout")
async def update_runtime_rollout(
    body: RuntimeRolloutRequest,
    user_id: CurrentUserId,
    db: Database,
    idempotency_key: UUID = Header(..., alias="Idempotency-Key"),
) -> dict:
    _require_super_admin(user_id, db)
    response = _admin_db(
        user_id, str(body.org_id), str(idempotency_key),
    ).rpc("set_agent_runtime_org_rollout", {
        "p_request_id": str(idempotency_key),
        "p_org_id": str(body.org_id),
        "p_enabled": body.enabled,
        "p_reason": body.reason,
    }).execute()
    return {"success": True, "data": response.data}


@router.post("/projection-dead/{outbox_id}/requeue")
async def requeue_projection_dead(
    outbox_id: UUID,
    body: DeadProjectionRecoveryRequest,
    user_id: CurrentUserId,
    db: Database,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
) -> dict:
    _require_super_admin(user_id, db)
    if idempotency_key != str(body.recovery_request_id):
        return {
            "success": False,
            "error": {"code": "IDEMPOTENCY_KEY_MISMATCH"},
        }
    response = _admin_db(
        user_id, str(body.org_id), idempotency_key,
    ).rpc("admin_requeue_agent_projection_dead", {
        "p_outbox_id": str(outbox_id),
        "p_expected_status": body.expected_status,
        "p_expected_recovery_version": body.expected_recovery_version,
        "p_expected_attempt_count": body.expected_attempt_count,
        "p_recovery_request_id": str(body.recovery_request_id),
        "p_reason": body.reason,
        "p_not_before": body.not_before,
    }).execute()
    return {"success": True, "data": response.data}
