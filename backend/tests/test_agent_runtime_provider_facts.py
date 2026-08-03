from __future__ import annotations

import pytest

from services.agent.runtime.provider_facts import (
    MockProviderSubmissionFacts,
    ProviderFactState,
    ProviderFactsError,
    ProviderSubmissionContext,
)


def _context(*, org: str = "org-a", key: str = "external-a") -> ProviderSubmissionContext:
    return ProviderSubmissionContext(
        attempt_id="attempt-a", action_id="action-a", run_id="run-a",
        org_id=org, user_id="user-a", scope_kind="user", scope_id="user-a",
        execution_token="execution-a", request_hash="a" * 64,
        provider="mock-provider", provider_revision="mock-v1",
        external_idempotency_key=key,
    )


async def _created() -> tuple[MockProviderSubmissionFacts, object]:
    facts = MockProviderSubmissionFacts()
    _, fact = await facts.create(_context())
    return facts, fact


@pytest.mark.asyncio
async def test_mock_provider_facts_are_not_production_ready() -> None:
    assert MockProviderSubmissionFacts().production_ready is False


@pytest.mark.asyncio
async def test_idempotency_returns_existing_fact_without_resubmission() -> None:
    facts = MockProviderSubmissionFacts()
    first_outcome, first = await facts.create(_context())
    second_outcome, second = await facts.create(_context())
    assert first_outcome == "created"
    assert second_outcome == "already_applied"
    assert first.submission_id == second.submission_id

    with pytest.raises(ProviderFactsError, match="IDEMPOTENCY_CONFLICT"):
        await facts.create(_context(org="org-b", key="external-a"))


@pytest.mark.asyncio
async def test_unknown_never_automatically_resubmits_and_reconciles() -> None:
    facts, fact = await _created()
    unknown = await facts.unknown(
        submission_id=fact.submission_id, execution_token="execution-a",
        request_hash="a" * 64, expected_state_version=0,
        evidence={"transport": "timeout"},
    )
    assert unknown.state is ProviderFactState.UNKNOWN
    recovered = await facts.recover(fact.submission_id)
    assert recovered.state is ProviderFactState.UNKNOWN
    with pytest.raises(ProviderFactsError, match="STALE_VERSION"):
        await facts.submitted(
            submission_id=fact.submission_id, execution_token="execution-a",
            request_hash="a" * 64, expected_state_version=0,
            provider_task_ref="task-duplicate",
        )
    resolved = await facts.reconcile(
        submission_id=fact.submission_id, execution_token="execution-a",
        request_hash="a" * 64, expected_state_version=1,
        resolution="readback_confirmed", readback_hash="b" * 64,
    )
    assert resolved.state is ProviderFactState.READBACK_CONFIRMED


@pytest.mark.asyncio
async def test_readback_and_cancel_are_fenced_and_cancel_is_not_terminal_intent() -> None:
    facts, fact = await _created()
    submitted = await facts.submitted(
        submission_id=fact.submission_id, execution_token="execution-a",
        request_hash="a" * 64, expected_state_version=0,
        provider_task_ref="task-a", provider_receipt_hash="c" * 64,
    )
    with pytest.raises(ProviderFactsError, match="FENCE_CONFLICT"):
        await facts.request_cancel(
            submission_id=fact.submission_id, execution_token="wrong",
            request_hash="a" * 64, expected_state_version=submitted.state_version,
            reason="user_cancel",
        )
    cancel = await facts.request_cancel(
        submission_id=fact.submission_id, execution_token="execution-a",
        request_hash="a" * 64, expected_state_version=submitted.state_version,
        reason="user_cancel",
    )
    assert cancel.state is ProviderFactState.CANCEL_REQUESTED
    cancelled = await facts.readback(
        submission_id=fact.submission_id, execution_token="execution-a",
        request_hash="a" * 64, expected_state_version=cancel.state_version,
        provider_state="cancelled", readback_hash="d" * 64,
    )
    assert cancelled.state is ProviderFactState.CANCELLED


@pytest.mark.asyncio
async def test_completed_readback_requires_hash_and_sensitive_evidence_is_rejected() -> None:
    facts, fact = await _created()
    submitted = await facts.submitted(
        submission_id=fact.submission_id, execution_token="execution-a",
        request_hash="a" * 64, expected_state_version=0,
        provider_task_ref="task-a",
    )
    with pytest.raises(ProviderFactsError, match="SENSITIVE_EVIDENCE"):
        await facts.unknown(
            submission_id=fact.submission_id, execution_token="execution-a",
            request_hash="a" * 64, expected_state_version=submitted.state_version,
            evidence={"access_token": "must-not-persist"},
        )
    completed = await facts.readback(
        submission_id=fact.submission_id, execution_token="execution-a",
        request_hash="a" * 64, expected_state_version=submitted.state_version,
        provider_state="completed", readback_hash="e" * 64,
    )
    assert completed.state is ProviderFactState.READBACK_CONFIRMED


def test_context_rejects_invalid_scope_and_request_hash() -> None:
    with pytest.raises(ProviderFactsError, match="SCOPE_INVALID"):
        _context().__class__(**{**_context().__dict__, "scope_kind": "tenant"})
    with pytest.raises(ProviderFactsError, match="REQUEST_HASH_INVALID"):
        _context().__class__(**{**_context().__dict__, "request_hash": "bad"})
