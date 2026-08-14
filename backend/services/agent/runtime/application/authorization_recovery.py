"""Recover approved authorization facts into dispatchable Actions."""

from __future__ import annotations

import hashlib
import json

from services.agent.runtime.executors.registry import ExecutorRegistry
from services.agent.runtime.policy.evaluator import PolicyEvaluator
from services.agent.runtime.policy.types import (
    AuthorizationEvidence,
    PermissionMode,
    PolicyContext,
    PolicyDecisionKind,
)
from services.agent.runtime.ports.authorization import ActionAuthorizationPort


class AuthorizationRecoveryDriver:
    def __init__(
        self, *, repository: ActionAuthorizationPort,
        registry: ExecutorRegistry, evaluator: PolicyEvaluator,
        worker_id: str,
    ) -> None:
        self._repository = repository
        self._registry = registry
        self._evaluator = evaluator
        self._worker_id = worker_id

    async def run_once(self) -> bool:
        claim = await self._repository.claim_recovery(
            worker_id=self._worker_id,
        )
        if claim is None:
            return False
        action = claim.action
        grant = claim.grant
        descriptor, _ = self._registry.resolve(_text(action, "tool_name"))
        grant_kind = _text(grant, "grant_kind")
        evidence = AuthorizationEvidence(
            action_grant_id=(
                _text(grant, "id") if grant_kind == "action" else None
            ),
            workflow_grant_id=(
                _text(grant, "id") if grant_kind == "workflow" else None
            ),
        )
        context = PolicyContext(
            action_id=_text(action, "id"),
            run_id=_text(action, "run_id"),
            session_id=_text(action, "session_id"),
            actor_id=_authorization_actor(action, claim.interaction_id),
            org_id=_optional_text(action.get("org_id")),
            action_kind=_text(action, "tool_name"),
            arguments_hash=_text(action, "arguments_hash"),
            permission_mode=PermissionMode.ASK,
            entitled=True,
            resource_scope_valid=True,
            descriptor=descriptor,
            evidence=evidence,
            effective_scope=_mapping(grant.get("effective_scope")),
        )
        decision = self._evaluator.evaluate(context)
        if decision.decision is not PolicyDecisionKind.ALLOW:
            raise RuntimeError("AUTHORIZATION_RECOVERY_NOT_ALLOWED")
        policy_revision = _text(action, "policy_revision")
        receipt_hash = _receipt_hash(
            action_id=context.action_id,
            arguments_hash=context.arguments_hash,
            descriptor_type=descriptor.executor_type,
            descriptor_revision=descriptor.revision,
            policy_revision=policy_revision,
            grant_id=_text(grant, "id"),
            reason_codes=decision.reason_codes,
        )
        receipt = await self._repository.record_allow_receipt(
            claim=claim, descriptor=descriptor,
            policy_revision=policy_revision,
            reason_codes=decision.reason_codes,
            obligations=decision.obligations,
            receipt_hash=receipt_hash,
        )
        await self._repository.activate(claim=claim, receipt=receipt)
        return True


def _receipt_hash(**facts: object) -> str:
    canonical = json.dumps(
        facts, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _text(value: object, field: str) -> str:
    if not isinstance(value, dict):
        try:
            item = value[field]  # type: ignore[index]
        except (KeyError, TypeError) as error:
            raise ValueError(f"{field} required") from error
    else:
        item = value.get(field)
    if not isinstance(item, str) or not item.strip():
        raise ValueError(f"{field} required")
    return item


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("optional identity must be nonblank")
    return value


def _authorization_actor(action: object, interaction_id: object) -> str:
    if isinstance(action, dict):
        user_id = action.get("user_id")
        if isinstance(user_id, str) and user_id.strip():
            return user_id
    return f"authorization-interaction:{interaction_id}"


def _mapping(value: object):
    if not isinstance(value, dict):
        raise ValueError("mapping required")
    return value
