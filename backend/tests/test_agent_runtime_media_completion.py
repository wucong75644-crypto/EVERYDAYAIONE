from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from services.agent.runtime.domain import RuntimeScope, ScopeKind
from services.agent.runtime.media_adapter import MockMediaProvider, RuntimeMediaAdapter
from services.agent.runtime.provider_facts import MockProviderSubmissionFacts, ProviderFactState, ProviderFactsError


def _attempt(*, run_id: str | None = "run-a", scope_id: str = "user-a") -> SimpleNamespace:
    return SimpleNamespace(
        attempt_id="attempt-a", action_id="action-a", run_id=run_id,
        scope=RuntimeScope(kind=ScopeKind.USER, scope_id=scope_id, user_id=scope_id, org_id="org-a"),
        request_hash="b" * 64,
        lease=SimpleNamespace(fencing_token="execution-a", expires_at=datetime.now(timezone.utc) + timedelta(minutes=5)),
    )


def _request() -> dict[str, object]:
    return {"kind": "image", "prompt": "isolated test"}


def _adapter(provider: object | None = None) -> tuple[RuntimeMediaAdapter, MockProviderSubmissionFacts, MockMediaProvider]:
    facts = MockProviderSubmissionFacts()
    actual = provider or MockMediaProvider()
    return RuntimeMediaAdapter(facts=facts, provider=actual, provider_revision="media-mock-v1", kind="image"), facts, actual


@pytest.mark.asyncio
async def test_a4_mock_media_is_not_production_ready_and_rejects_unsafe_provider() -> None:
    adapter, _, _ = _adapter()
    assert adapter.readiness.ready is False
    assert adapter.readiness.error_code == "CREDENTIAL_BACKEND_NOT_READY"

    class UnsafeProvider(MockMediaProvider):
        isolated_only = False

    with pytest.raises(RuntimeError, match="ISOLATED_PROVIDER_REQUIRED"):
        RuntimeMediaAdapter(facts=MockProviderSubmissionFacts(), provider=UnsafeProvider(), provider_revision="v1", kind="image")


@pytest.mark.asyncio
async def test_media_submit_is_idempotent_and_fact_backed() -> None:
    adapter, facts, provider = _adapter()
    receipt = await adapter.submit(_attempt(), _request(), idempotency_key="media-key-a")
    replay = await adapter.submit(_attempt(), _request(), idempotency_key="media-key-a")
    assert receipt.state.value == "accepted"
    assert replay.provider_task_ref == receipt.provider_task_ref
    assert provider.submit_calls == 1
    assert (await facts.recover(receipt.evidence["submission_id"])).state is ProviderFactState.SUBMITTED


@pytest.mark.asyncio
async def test_media_unknown_only_reconciles_and_readback_confirms_completion() -> None:
    class UnknownProvider(MockMediaProvider):
        async def submit(self, *args, **kwargs):
            self.submit_calls += 1
            raise TimeoutError("isolated timeout")

    adapter, facts, provider = _adapter(UnknownProvider())
    unknown = await adapter.submit(_attempt(), _request(), idempotency_key="media-key-b")
    assert unknown.state.value == "unknown"
    assert provider.submit_calls == 1
    recovered = await facts.recover(unknown.evidence["submission_id"])
    reconciled = await adapter.reconcile(_attempt(), {**dict(unknown.evidence), "provider_task_ref": "mock-task"})
    assert reconciled.state.value == "completed"
    assert provider.reconcile_calls == 1
    assert (await facts.recover(recovered.submission_id)).state is ProviderFactState.READBACK_CONFIRMED


@pytest.mark.asyncio
async def test_media_cancel_is_fenced_and_owned_by_facts_boundary() -> None:
    adapter, facts, provider = _adapter()
    accepted = await adapter.submit(_attempt(), _request(), idempotency_key="media-key-c")
    cancelled = await adapter.cancel(_attempt(), {**dict(accepted.evidence), "provider_task_ref": accepted.provider_task_ref})
    assert cancelled.state.value == "cancelled"
    assert provider.cancel_calls == 1
    with pytest.raises(ProviderFactsError, match="FENCE_CONFLICT"):
        await facts.request_cancel(submission_id=accepted.evidence["submission_id"], execution_token="wrong",
                                    request_hash="b" * 64, expected_state_version=1, reason="cancel")


@pytest.mark.asyncio
async def test_media_context_kind_and_secret_fail_closed() -> None:
    adapter, _, _ = _adapter()
    with pytest.raises(RuntimeError, match="RUN_CONTEXT_REQUIRED"):
        await adapter.submit(_attempt(run_id=None), _request(), idempotency_key="media-key-d")
    with pytest.raises(RuntimeError, match="KIND_MISMATCH"):
        await adapter.submit(_attempt(), {"kind": "video", "prompt": "x"}, idempotency_key="media-key-e")
    with pytest.raises(PermissionError, match="SECRET_HANDLE_REQUIRED"):
        await adapter.submit(_attempt(), {**_request(), "api_token": "must-not-cross"}, idempotency_key="media-key-f")
