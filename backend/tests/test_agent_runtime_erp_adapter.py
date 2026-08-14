from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from services.agent.runtime.domain import RuntimeScope, ScopeKind
from services.agent.runtime.erp_adapter import (
    MockErpProvider,
    RuntimeErpAdapter,
)
from services.agent.runtime.provider_facts import (
    MockProviderSubmissionFacts,
    ProviderFactState,
    ProviderFactsError,
)
from services.kuaimai.registry import TOOL_REGISTRIES


ACTION = next(iter(TOOL_REGISTRIES["erp_execute"]))


def _attempt(*, run_id: str | None = "run-a") -> SimpleNamespace:
    scope = RuntimeScope(
        kind=ScopeKind.USER, scope_id="user-a", user_id="user-a", org_id="org-a",
    )
    return SimpleNamespace(
        attempt_id="attempt-a", action_id="action-a", run_id=run_id,
        scope=scope, request_hash="a" * 64,
        lease=SimpleNamespace(
            fencing_token="execution-a",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        ),
    )


def _request() -> dict[str, object]:
    return {"tool_name": "erp_execute", "action": ACTION, "params": {}}


def _adapter(provider: object | None = None) -> tuple[RuntimeErpAdapter, MockProviderSubmissionFacts]:
    facts = MockProviderSubmissionFacts()
    adapter = RuntimeErpAdapter(
        facts=facts, provider=provider or MockErpProvider(), provider_revision="erp-mock-v1",
    )
    return adapter, facts


@pytest.mark.asyncio
async def test_a3_isolated_adapter_is_not_production_ready() -> None:
    adapter, _ = _adapter()
    assert adapter.readiness.ready is False
    assert adapter.readiness.error_code == "CREDENTIAL_BACKEND_NOT_READY"

    class UnsafeProvider(MockErpProvider):
        isolated_only = False

    with pytest.raises(RuntimeError, match="ISOLATED_PROVIDER_REQUIRED"):
        RuntimeErpAdapter(facts=MockProviderSubmissionFacts(), provider=UnsafeProvider(), provider_revision="v1")


@pytest.mark.asyncio
async def test_submit_records_pending_then_accepted_with_external_identity() -> None:
    adapter, facts = _adapter()
    receipt = await adapter.submit(_attempt(), _request(), idempotency_key="external-a")
    assert receipt.state.value == "accepted"
    assert receipt.provider_task_ref
    assert receipt.evidence["state_version"] == 1
    fact = await facts.recover(receipt.evidence["submission_id"])
    assert fact.state is ProviderFactState.SUBMITTED

    replay = await adapter.submit(_attempt(), _request(), idempotency_key="external-a")
    assert replay.provider_task_ref == receipt.provider_task_ref
    assert replay.state.value == "accepted"


@pytest.mark.asyncio
async def test_submit_failure_is_unknown_and_does_not_retry_provider() -> None:
    class FailingProvider(MockErpProvider):
        calls = 0

        async def submit(self, *args, **kwargs):
            self.calls += 1
            raise TimeoutError("provider timeout")

    provider = FailingProvider()
    adapter, facts = _adapter(provider)
    receipt = await adapter.submit(_attempt(), _request(), idempotency_key="external-b")
    assert receipt.state.value == "unknown"
    assert provider.calls == 1
    fact = await facts.recover(receipt.evidence["submission_id"] if "submission_id" in receipt.evidence else "missing")
    assert fact.state is ProviderFactState.UNKNOWN


@pytest.mark.asyncio
async def test_reconcile_readback_confirms_completion_and_cancel_is_fenced() -> None:
    adapter, _ = _adapter()
    accepted = await adapter.submit(_attempt(), _request(), idempotency_key="external-c")
    completed = await adapter.reconcile(_attempt(), {
        **dict(accepted.evidence), "provider_task_ref": accepted.provider_task_ref,
    })
    assert completed.state.value == "completed"
    assert completed.evidence["state_version"] == 2

    with pytest.raises(ProviderFactsError, match="FENCE_CONFLICT"):
        facts = adapter.facts
        await facts.request_cancel(
            submission_id=accepted.evidence["submission_id"], execution_token="wrong",
            request_hash="a" * 64, expected_state_version=1, reason="cancel",
        )


@pytest.mark.asyncio
async def test_context_and_public_request_secrets_fail_closed() -> None:
    adapter, _ = _adapter()
    with pytest.raises(RuntimeError, match="RUN_CONTEXT_REQUIRED"):
        await adapter.submit(_attempt(run_id=None), _request(), idempotency_key="external-d")
    with pytest.raises(PermissionError, match="SECRET_HANDLE_REQUIRED"):
        await adapter.submit(
            _attempt(), {**_request(), "params": {"app_secret": "must-not-cross"}},
            idempotency_key="external-e",
        )
