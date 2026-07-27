from __future__ import annotations

from datetime import datetime, timedelta, timezone

from services.agent.runtime.executors.types import (
    AuthorizationRequirement,
    CancellationSupport,
    ExecutionMode,
    ExecutorDescriptor,
    IdempotencySupport,
)
from services.agent.runtime.policy.evaluator import PolicyEvaluator
from services.agent.runtime.policy.types import (
    AuthorizationEvidence,
    PermissionMode,
    PolicyContext,
    PolicyDecisionKind,
    PolicyReceipt,
)


def _context(
    requirement: AuthorizationRequirement,
    *,
    mode: PermissionMode = PermissionMode.ASK,
    entitled: bool = True,
    scoped: bool = True,
    evidence: AuthorizationEvidence = AuthorizationEvidence(),
) -> PolicyContext:
    descriptor = ExecutorDescriptor(
        executor_type="external",
        revision=1,
        action_kinds=frozenset({"external.send"}),
        mode=ExecutionMode.EXTERNAL_ACTION,
        authorization=requirement,
        required_capabilities=frozenset({"network.provider"}),
        max_inline_ms=0,
        prepare_timeout_ms=1000,
        submit_timeout_ms=1000,
        execution_timeout_ms=1000,
        reconcile_timeout_ms=1000,
        idempotency=IdempotencySupport.ADAPTER,
        cancellation=CancellationSupport.BEST_EFFORT,
        query_status=True,
        progress=False,
        callback=False,
        result_schema_revision=1,
    )
    return PolicyContext(
        action_id="action", run_id="run", session_id="session",
        actor_id="actor", org_id="org", action_kind="external.send",
        arguments_hash="a" * 64, permission_mode=mode,
        entitled=entitled, resource_scope_valid=scoped,
        descriptor=descriptor, evidence=evidence,
    )


def test_policy_denies_entitlement_and_scope_before_authorization() -> None:
    evaluator = PolicyEvaluator()

    assert evaluator.evaluate(
        _context(AuthorizationRequirement.NONE, entitled=False),
    ).decision is PolicyDecisionKind.DENY
    assert evaluator.evaluate(
        _context(AuthorizationRequirement.NONE, scoped=False),
    ).reason_codes == ("RESOURCE_SCOPE_DENIED",)


def test_policy_is_three_state_and_action_grant_bound() -> None:
    evaluator = PolicyEvaluator()
    waiting = evaluator.evaluate(
        _context(AuthorizationRequirement.PERSISTED_INTERACTION),
    )
    allowed = evaluator.evaluate(_context(
        AuthorizationRequirement.PERSISTED_INTERACTION,
        evidence=AuthorizationEvidence(action_grant_id="grant-1"),
    ))

    assert waiting.decision is PolicyDecisionKind.REQUIRE_AUTHORIZATION
    assert allowed.decision is PolicyDecisionKind.ALLOW
    assert allowed.grant_id == "grant-1"


def test_workflow_grant_must_be_explicit_and_plan_side_effect_still_asks() -> None:
    evaluator = PolicyEvaluator()

    assert evaluator.evaluate(_context(
        AuthorizationRequirement.PREAPPROVED_WORKFLOW,
        evidence=AuthorizationEvidence(workflow_grant_id="workflow-1"),
    )).decision is PolicyDecisionKind.ALLOW
    decision = evaluator.evaluate(_context(
        AuthorizationRequirement.NONE, mode=PermissionMode.PLAN,
    ))
    assert decision.decision is PolicyDecisionKind.REQUIRE_AUTHORIZATION
    assert decision.reason_codes == ("PLAN_MODE_SIDE_EFFECT",)


def test_policy_receipt_hash_is_deterministic_and_tamper_evident() -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    receipt = PolicyReceipt(
        receipt_id="receipt", action_id="action",
        decision=PolicyDecisionKind.ALLOW, arguments_hash="a" * 64,
        executor_type="external", executor_revision=1,
        policy_revision="policy-v1", effective_scope={"org_id": "org"},
        reason_codes=("ACTION_GRANT_VALID",), obligations=("audit",),
        evaluated_at=now, expires_at=now + timedelta(minutes=5),
        grant_id="grant",
    )

    assert len(receipt.receipt_hash) == 64
    assert receipt.receipt_hash == receipt.canonical_hash()
