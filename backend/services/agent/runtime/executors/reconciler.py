"""Recovery helpers enforcing reconcile-only semantics."""

from __future__ import annotations

from services.agent.runtime.domain import ActionAttemptStatus, ActionStatus


def assert_reconcile_only(action_status: ActionStatus, attempt_status: ActionAttemptStatus) -> None:
    if action_status not in {ActionStatus.ACCEPTED, ActionStatus.UNKNOWN}:
        raise ValueError("RECONCILE_STATUS_REQUIRED")
    if attempt_status not in {ActionAttemptStatus.ACCEPTED, ActionAttemptStatus.UNKNOWN}:
        raise ValueError("RECONCILE_ATTEMPT_STATUS_REQUIRED")


def provider_facts_require_readback(facts: dict[str, object]) -> bool:
    """True when the receipt is insufficient to prove a terminal outcome."""
    return not bool(facts.get("provider_task_ref") or facts.get("readback"))


__all__ = ["assert_reconcile_only", "provider_facts_require_readback"]
