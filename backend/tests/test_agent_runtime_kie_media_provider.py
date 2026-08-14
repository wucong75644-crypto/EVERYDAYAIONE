from __future__ import annotations

from datetime import datetime, timezone
import pytest

from services.agent.runtime.executors.specialist_contracts import (
    ProviderState, receipt_facts,
)
from services.agent.runtime.providers.kie_transport import KieHttpResponse
from services.agent.runtime.application.action_loop import ActionLoopDriver
from services.agent.runtime.ports.executor import (
    ExecutionOutcome, ExecutionReceipt,
)
from tests.agent_runtime_kie_media_provider_test_support import (
    ExistingProviderFacts,
    FakeCredentials,
    FakeProviderFacts,
    FakeTaskPort,
    FakeTransport,
    attempt,
    provider,
    provider_receipt,
)


@pytest.mark.asyncio
async def test_submit_is_single_shot_and_uses_only_server_provider_body():
    transport = FakeTransport()
    credentials = FakeCredentials()
    receipt = await provider(transport, credentials=credentials).submit(
        attempt(), {"prompt": "model supplied"}, idempotency_key="ignored",
    )

    assert receipt.state is ProviderState.ACCEPTED
    assert receipt.provider_task_ref == "kie-1"
    assert len(transport.calls) == 1
    assert transport.calls[0][0] == "submit"
    body = transport.calls[0][1]["body"]
    assert body["input"]["prompt"] == "make it blue"
    encoded = str(body)
    assert "fixture-key" not in encoded
    assert not any(name in encoded for name in (
        "task_id", "user_id", "org_id", "reserved_credits", "runtime_task",
    ))
    assert credentials.hashes == ["b" * 64]
    assert receipt.evidence["provider_request_hash"] == "b" * 64
    assert receipt.evidence["provider_idempotency_key"] == "ignored"


@pytest.mark.asyncio
async def test_submit_timeout_is_unknown_and_never_retries():
    transport = FakeTransport(error=TimeoutError("uncertain"))
    receipt = await provider(transport).submit(
        attempt(), {}, idempotency_key="one-shot",
    )

    assert receipt.state is ProviderState.UNKNOWN
    assert receipt.evidence["error_code"] == "KIE_SUBMIT_RESULT_UNKNOWN"
    assert [name for name, _ in transport.calls] == ["submit"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("phase", "expected", "fact_state"), (
    ("accepted", ProviderState.ACCEPTED, "submitted"),
    ("unknown", ProviderState.UNKNOWN, "unknown"),
    ("completed", ProviderState.COMPLETED, "readback_confirmed"),
))
async def test_prepared_video_provider_facts_cover_nonterminal_and_terminal(
    phase, expected, fact_state,
):
    facts = FakeProviderFacts()
    if phase == "completed":
        transport = FakeTransport(query=KieHttpResponse(
            status_code=200, payload={"code": 200, "data": {
                "taskId": "kie-1", "state": "success",
                "resultJson": '{"resultUrls":["https://cdn.example/video.mp4"]}',
            }},
        ))
        receipt = await provider(
            transport, facts=facts, kind="video",
        ).reconcile(attempt(), provider_receipt())
    else:
        transport = FakeTransport(
            error=TimeoutError("uncertain") if phase == "unknown" else None,
        )
        receipt = await provider(
            transport, facts=facts, kind="video",
        ).submit(attempt(), {}, idempotency_key=f"video-{phase}")
    assert receipt.state is expected
    assert receipt.evidence["provider_fact_state"] == fact_state


@pytest.mark.asyncio
async def test_existing_submission_fact_never_redispatches_provider():
    transport = FakeTransport()
    receipt = await provider(
        transport, facts=ExistingProviderFacts(),
    ).submit(attempt(), {}, idempotency_key="one-shot")

    assert receipt.state is ProviderState.UNKNOWN
    assert receipt.provider_task_ref == "kie-existing"
    assert receipt.evidence["error_code"] == (
        "KIE_SUBMISSION_FACT_REQUIRES_READBACK"
    )
    assert transport.calls == []


@pytest.mark.asyncio
async def test_deterministic_submit_rejection_is_failed():
    transport = FakeTransport(submit=KieHttpResponse(
        status_code=400, payload={"code": 422, "msg": "invalid"},
    ))
    receipt = await provider(transport).submit(
        attempt(), {}, idempotency_key="one-shot",
    )
    assert receipt.state is ProviderState.FAILED
    assert receipt.evidence["error_code"] == "KIE_SUBMIT_REJECTED"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state", "result_json", "expected"),
    [
        ("queuing", None, ProviderState.ACCEPTED),
        ("generating", None, ProviderState.ACCEPTED),
        ("fail", None, ProviderState.FAILED),
        ("success", '{"resultUrls":["https://cdn.example/result.png"]}',
         ProviderState.COMPLETED),
    ],
)
async def test_reconcile_maps_only_kie_readback_states(
    state, result_json, expected,
):
    data = {"taskId": "kie-1", "state": state}
    if result_json is not None:
        data["resultJson"] = result_json
    transport = FakeTransport(query=KieHttpResponse(
        status_code=200, payload={"code": 200, "data": data},
    ))
    credentials = FakeCredentials()

    receipt = await provider(transport, credentials=credentials).reconcile(
        attempt(), provider_receipt(),
    )

    assert receipt.state is expected
    assert [name for name, _ in transport.calls] == ["query"]
    assert credentials.ownership == [("reconcile-token", 4)]
    if expected is ProviderState.COMPLETED:
        # 228_06 projection consumes image_urls/urls/images from Action result.
        assert receipt.result["image_urls"] == [
            "https://cdn.example/result.png",
        ]
        durable = receipt_facts(receipt)
        assert "image_urls" not in str(durable)
        assert "result_hash" in durable


@pytest.mark.asyncio
async def test_success_with_invalid_result_urls_stays_unknown():
    transport = FakeTransport(query=KieHttpResponse(
        status_code=200, payload={
            "code": 200,
            "data": {"taskId": "kie-1", "state": "success",
                     "resultJson": '{"resultUrls":["file:///secret"]}'},
        },
    ))
    receipt = await provider(transport).reconcile(
        attempt(), provider_receipt(),
    )
    assert receipt.state is ProviderState.UNKNOWN
    assert receipt.evidence["error_code"] == "KIE_RESULT_URLS_AMBIGUOUS"


@pytest.mark.asyncio
async def test_cancel_requires_explicit_provider_confirmation():
    transport = FakeTransport()
    facts = FakeProviderFacts()
    receipt = await provider(transport, facts=facts).cancel(
        attempt(), provider_receipt(),
    )
    assert receipt.state is ProviderState.UNKNOWN
    assert receipt.evidence["error_code"] == "CANCEL_UNPROVEN"
    assert receipt.evidence["cancel_unproven"] is True
    assert receipt.evidence["provider_request_hash"] == "b" * 64
    assert receipt.evidence["provider_idempotency_key"] == "c" * 64
    assert facts.calls == ["read", "request_cancel"]
    assert transport.calls == []


@pytest.mark.asyncio
async def test_cancel_recovers_provider_ref_from_durable_fact():
    stale = provider_receipt()
    stale["provider_task_ref"] = None
    receipt = await provider(FakeTransport()).cancel(attempt(), stale)
    assert receipt.state is ProviderState.UNKNOWN
    assert receipt.provider_task_ref == "kie-1"
    assert receipt.evidence["cancel_unproven"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(("state", "expected"), [
    ("success", ProviderState.COMPLETED),
    ("fail", ProviderState.FAILED),
    ("cancelled", ProviderState.CANCELLED),
    ("waiting", ProviderState.UNKNOWN),
])
async def test_cancel_unproven_next_operation_is_record_info_readback(
    state, expected,
):
    result_json = (
        '{"resultUrls":["https://cdn.example/result.png"]}'
        if state == "success" else None
    )
    data = {"taskId": "kie-1", "state": state}
    if result_json:
        data["resultJson"] = result_json
    transport = FakeTransport(query=KieHttpResponse(
        status_code=200, payload={"code": 200, "data": data},
    ))
    facts = FakeProviderFacts()
    kie = provider(transport, facts=facts)
    await kie.cancel(attempt(), provider_receipt())
    stale_receipt = provider_receipt()
    stale_receipt["provider_task_ref"] = None
    stale_receipt["evidence"]["state_version"] = 0
    stale_receipt["evidence"]["provider_fact_state"] = "submitted"
    readback = await kie.cancel(attempt(), stale_receipt)

    assert readback.state is expected
    assert [name for name, _ in transport.calls] == ["query"]
    assert facts.calls == ["read", "request_cancel", "read", "readback"]
    assert readback.evidence["cancel_unproven"] is True
    assert readback.provider_task_ref == "kie-1"
    if state == "waiting":
        assert readback.evidence["error_code"] == (
            "KIE_CANCEL_UNPROVEN_PROVIDER_PENDING"
        )
        repeated = await kie.cancel(attempt(), stale_receipt)
        assert repeated.state is ProviderState.UNKNOWN
        assert [name for name, _ in transport.calls] == ["query", "query"]
        assert facts.calls == [
            "read", "request_cancel", "read", "readback", "read", "readback",
        ]


@pytest.mark.asyncio
@pytest.mark.parametrize("receipt_phase", (
    "accepted", "unknown", "cancel_unproven",
))
@pytest.mark.parametrize(("state", "terminal"), (
    ("success", ProviderState.COMPLETED),
    ("fail", ProviderState.FAILED),
    ("waiting", ProviderState.ACCEPTED),
))
async def test_stale_receipt_recovers_provider_ref_from_durable_fact(
    receipt_phase, state, terminal,
):
    data = {"taskId": "kie-1", "state": state}
    if state == "success":
        data["resultJson"] = '{"resultUrls":["https://cdn.example/video.mp4"]}'
    transport = FakeTransport(query=KieHttpResponse(
        status_code=200, payload={"code": 200, "data": data},
    ))
    stale = provider_receipt(cancel_unproven=receipt_phase == "cancel_unproven")
    stale["provider_task_ref"] = None
    stale["evidence"]["provider_fact_state"] = receipt_phase
    readback = await provider(
        transport, facts=FakeProviderFacts(), kind="video",
    ).reconcile(attempt(), stale)

    expected = (
        ProviderState.UNKNOWN
        if receipt_phase == "cancel_unproven" and state == "waiting"
        else terminal
    )
    assert readback.state is expected
    assert readback.provider_task_ref == "kie-1"
    assert transport.calls == [("query", {
        "api_key": "fixture-key", "provider_task_ref": "kie-1",
    })]


@pytest.mark.asyncio
async def test_missing_credential_fails_closed_before_network():
    transport = FakeTransport()
    receipt = await provider(
        transport, credentials=FakeCredentials(RuntimeError("missing")),
    ).submit(attempt(), {}, idempotency_key="blocked")
    assert receipt.state is ProviderState.FAILED
    assert receipt.evidence["error_code"] == "KIE_MEDIA_CONFIGURATION_UNAVAILABLE"
    assert transport.calls == []


class _ReconciliationFacts:
    def __init__(self):
        self.accepted = self.unknown = self.dispatched_unknown = None

    async def still_accepted(self, **kwargs):
        self.accepted = kwargs

    async def still_unknown(self, **kwargs):
        self.unknown = kwargs

    async def media_provider_unknown(self, **kwargs):
        self.dispatched_unknown = kwargs


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", (
    ExecutionOutcome.ACCEPTED, ExecutionOutcome.UNKNOWN,
))
async def test_nonterminal_readback_schedules_reconcile_without_redispatch(
    outcome,
):
    facts = _ReconciliationFacts()
    driver = ActionLoopDriver(
        recovery_repository=object(), action_repository=object(),
        authorization_repository=object(), resolver=object(), worker_id="media",
        specialist_facts=facts,
    )
    receipt = ExecutionReceipt(
        outcome=outcome, request_hash="a" * 64,
        external_receipt=(
            {"provider_task_ref": "kie-1"}
            if outcome is ExecutionOutcome.ACCEPTED else {}
        ),
        ambiguity_evidence=(
            {"error_code": "STILL_UNKNOWN"}
            if outcome is ExecutionOutcome.UNKNOWN else {}
        ),
    )
    persisted = await driver._persist_specialist_nonterminal(
        receipt, attempt_id="attempt", token="token", state_version=7,
        request_hash="a" * 64, reconciliation=True, specialist=True,
    )
    call = facts.accepted or facts.unknown
    assert persisted is True
    assert call["next_reconcile_at"].tzinfo is not None
    assert call["next_reconcile_at"] > datetime.now(timezone.utc)
    assert (facts.accepted is not None) is (
        outcome is ExecutionOutcome.ACCEPTED
    )


@pytest.mark.asyncio
async def test_dispatch_unknown_persists_provider_fact_identity():
    facts = _ReconciliationFacts()
    driver = ActionLoopDriver(
        recovery_repository=object(), action_repository=object(),
        authorization_repository=object(), resolver=object(), worker_id="media",
        specialist_facts=facts,
    )
    external = {
        "provider": "kie", "provider_task_ref": None,
        "evidence": {
            "submission_id": "submission-1", "state_version": 1,
            "provider_request_hash": "b" * 64,
            "provider_idempotency_key": "c" * 64,
        },
    }
    receipt = ExecutionReceipt(
        outcome=ExecutionOutcome.UNKNOWN, request_hash="a" * 64,
        external_receipt=external,
        ambiguity_evidence={"error_code": "KIE_SUBMIT_RESULT_UNKNOWN"},
    )
    assert await driver._persist_specialist_nonterminal(
        receipt, attempt_id="attempt", token="token", state_version=7,
        request_hash="a" * 64, reconciliation=False, specialist=True,
    )
    assert facts.dispatched_unknown["provider_receipt"] == external
    assert facts.dispatched_unknown["expected_state_version"] == 7
    assert facts.dispatched_unknown["next_reconcile_at"] > datetime.now(
        timezone.utc,
    )
