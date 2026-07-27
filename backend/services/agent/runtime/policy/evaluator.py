"""Fail-closed deterministic Policy evaluator."""

from __future__ import annotations

from services.agent.runtime.executors.types import (
    AuthorizationRequirement,
    ExecutionMode,
)
from services.agent.runtime.policy.types import (
    PermissionMode,
    PolicyContext,
    PolicyDecision,
    PolicyDecisionKind,
)


_SIDE_EFFECTING = {
    ExecutionMode.SANDBOX_JOB,
    ExecutionMode.RESOURCE_MUTATION,
    ExecutionMode.ASYNC_GENERATION,
    ExecutionMode.EXTERNAL_ACTION,
    ExecutionMode.REMOTE_EXTENSION,
    ExecutionMode.CHILD_RUN,
}


class PolicyEvaluator:
    """Evaluate registered executor metadata and persisted evidence."""

    def evaluate(self, context: PolicyContext) -> PolicyDecision:
        if not context.entitled:
            return _deny("ENTITLEMENT_DENIED")
        if not context.resource_scope_valid:
            return _deny("RESOURCE_SCOPE_DENIED")

        requirement = context.descriptor.authorization
        if requirement is AuthorizationRequirement.FORBIDDEN:
            return _deny("EXECUTOR_FORBIDDEN")
        if (
            context.permission_mode is PermissionMode.PLAN
            and context.descriptor.mode in _SIDE_EFFECTING
        ):
            return _require("PLAN_MODE_SIDE_EFFECT")
        if requirement is AuthorizationRequirement.NONE:
            return _allow("NO_AUTHORIZATION_REQUIRED")
        if requirement is AuthorizationRequirement.EXPLICIT_INTENT:
            if context.evidence.explicit_intent:
                return _allow("EXPLICIT_USER_INTENT")
            return _require("EXPLICIT_INTENT_REQUIRED")
        if requirement is AuthorizationRequirement.PERSISTED_INTERACTION:
            if context.evidence.action_grant_id:
                return _allow(
                    "ACTION_GRANT_VALID",
                    grant_id=context.evidence.action_grant_id,
                )
            return _require("ACTION_GRANT_REQUIRED")
        if requirement is AuthorizationRequirement.PREAPPROVED_WORKFLOW:
            if context.evidence.workflow_grant_id:
                return _allow(
                    "WORKFLOW_GRANT_VALID",
                    grant_id=context.evidence.workflow_grant_id,
                )
            return _require("WORKFLOW_GRANT_REQUIRED")
        return _deny("POLICY_METADATA_INVALID")


def _allow(reason: str, grant_id: str | None = None) -> PolicyDecision:
    return PolicyDecision(
        decision=PolicyDecisionKind.ALLOW,
        reason_codes=(reason,),
        obligations=("audit",),
        grant_id=grant_id,
    )


def _require(reason: str) -> PolicyDecision:
    return PolicyDecision(
        decision=PolicyDecisionKind.REQUIRE_AUTHORIZATION,
        reason_codes=(reason,),
    )


def _deny(reason: str) -> PolicyDecision:
    return PolicyDecision(
        decision=PolicyDecisionKind.DENY,
        reason_codes=(reason,),
    )
