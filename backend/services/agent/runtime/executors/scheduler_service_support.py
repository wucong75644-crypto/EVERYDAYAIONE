"""Small orchestration helpers for the Runtime Scheduler service port."""

from __future__ import annotations

from typing import Mapping


async def mutate_scheduler_service(
    service: object, task_id: str, expected_version: int,
    operation: str, payload: Mapping[str, object],
) -> Mapping[str, object]:
    if operation not in {"create", "update", "delete", "pause", "resume"}:
        raise ValueError("SCHEDULED_OPERATION_INVALID")
    facts = getattr(service, "facts", None)
    if facts is not None and hasattr(facts, "mutate_scheduler_task"):
        context = payload.get("_dispatch_context")
        context = context if isinstance(context, Mapping) else {}
        task_payload = payload.get("payload")
        result = await facts.mutate_scheduler_task(
            attempt=payload.get("_attempt"), task_id=task_id,
            expected_version=expected_version, operation=operation,
            payload=task_payload if isinstance(task_payload, Mapping) else payload,
            dispatch_intent_id=str(context.get("dispatch_intent_id", "")),
            attempt_state_version=int(context.get("expected_attempt_version", 0)),
        )
        if not isinstance(result, Mapping):
            raise RuntimeError("SCHEDULER_CONTROL_RESULT_INVALID")
        return dict(result)
    attempt = payload.get("_attempt")
    if facts is not None and attempt is not None:
        from services.agent.runtime.executors.resource_support import resource_params
        bound = await facts.mutate_resource(
            "manage_scheduled_task",
            **resource_params(
                attempt, operation, task_id, payload, expected_version,
            ),
        )
        if not isinstance(bound, Mapping) or bound.get("outcome") not in {"bound", "updated"}:
            raise RuntimeError("RESOURCE_FACT_BINDING_REQUIRED")
    return await service.store.cas(task_id, expected_version, operation, payload)


async def reconcile_scheduler_service(
    service: object, attempt: object, receipt: Mapping[str, object],
) -> Mapping[str, object]:
    facts = getattr(service, "facts", None)
    if facts is None or not hasattr(facts, "reconcile_scheduler_task"):
        return {"state": "unknown", "evidence": {"error_code": "SCHEDULER_READBACK_NOT_READY"}}
    token = receipt.get("reconciliation_token")
    version = receipt.get("reconciliation_state_version")
    if not isinstance(token, str) or not token or not isinstance(version, int):
        return {
            "state": "unknown",
            "evidence": {"error_code": "SCHEDULER_RECONCILIATION_OWNERSHIP_REQUIRED"},
        }
    return await facts.reconcile_scheduler_task(
        attempt=attempt, ownership_token=token,
        expected_state_version=version,
    )


async def cancel_scheduler_service(
    service: object, attempt: object, receipt: Mapping[str, object],
) -> Mapping[str, object]:
    facts = getattr(service, "facts", None)
    if facts is None or not hasattr(facts, "cancel_scheduler_task"):
        return {"state": "unknown", "evidence": {"error_code": "SCHEDULER_CANCEL_NOT_READY"}}
    return await facts.cancel_scheduler_task(
        attempt=attempt, reason=str(receipt.get("reason", "runtime_cancel")),
        ownership_token=str(receipt.get("reconciliation_token", "")),
        expected_state_version=int(receipt.get("reconciliation_state_version", -1)),
    )


__all__ = [
    "cancel_scheduler_service", "mutate_scheduler_service",
    "reconcile_scheduler_service",
]
