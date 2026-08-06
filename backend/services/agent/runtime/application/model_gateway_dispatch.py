"""Atomic 227_20 dispatch helper for the explicit Model Gateway lane."""

from __future__ import annotations

from dataclasses import replace
from typing import Mapping, Protocol
from uuid import UUID, uuid5

from services.agent.runtime.ports.model import ModelStepRequest
from services.agent.runtime.ports.model_gateway import (
    ModelGatewayDispatchOutcome, ModelGatewayDispatchRepositoryPort,
)


_REQUEST_NAMESPACE = UUID("3ae7872b-9a2d-43e7-a654-d7202a3f28f8")


class GatewayCallPlan(Protocol):
    model_id: str
    provider: str
    model_revision: str
    request_receipt: Mapping[str, object]


async def start_gateway_dispatch(
    repository: ModelGatewayDispatchRepositoryPort, *,
    run: Mapping[str, object], plan: GatewayCallPlan,
    request: ModelStepRequest, attempt_id: str, attempt_version: int,
    run_id: str, run_execution_token: str,
) -> tuple[ModelStepRequest, int]:
    receipt = await repository.start_dispatch(
        request_id=str(uuid5(_REQUEST_NAMESPACE, attempt_id)),
        session_id=_required_text(run, "session_id"), run_id=run_id,
        model_step_id=str(request.model_step_id), model_attempt_id=attempt_id,
        run_execution_token=run_execution_token,
        request_hash=request.request_hash,
        expected_attempt_version=attempt_version, model_id=plan.model_id,
        provider=plan.provider,
        provider_revision=_required_text(
            plan.request_receipt, "credential_revision",
        ),
        model_revision=plan.model_revision,
        purpose=_required_text(plan.request_receipt, "credential_purpose"),
    )
    if receipt.outcome not in {
        ModelGatewayDispatchOutcome.DISPATCHING,
        ModelGatewayDispatchOutcome.ALREADY_DISPATCHING,
    } or receipt.binding is None:
        raise RuntimeError("MODEL_GATEWAY_DISPATCH_NOT_ESTABLISHED")
    return (
        replace(request, gateway_binding=receipt.binding),
        receipt.binding.attempt_state_version,
    )


def _required_text(value: Mapping[str, object], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise RuntimeError(f"MODEL_GATEWAY_BINDING_FACT_REQUIRED:{key}")
    return item
