"""227_46/227_47 fake-database contracts for the Scheduled WeCom repository."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone

import pytest
from psycopg import InterfaceError, OperationalError

from core.db_scope import DatabaseAccessKind, DatabaseScope
from services.agent.runtime.domain.errors import PersistenceContractError
from services.agent.runtime.infrastructure.postgres.scheduled_wecom_delivery import (
    PostgresScheduledWecomDeliveryRepository,
)
from services.agent.runtime.infrastructure.postgres.scheduled_wecom_parsing import (
    parse_delivery_claim,
)
from services.agent.runtime.ports.scheduled_wecom_delivery import (
    AttemptOperationOutcome,
    AttemptStatus,
    DeliveryStatus,
    DeliveryFence,
    DispatchChannel,
    DispatchAttempt,
    DispatchPayload,
    DispatchPayloadOutcome,
    DispatchPayloadVersions,
    ItemStatus,
    PreparedRecovery,
    ProviderDispatchIdentity,
    RecoveryOutcome,
    UnavailableDispatchPayload,
    UnavailableReason,
    UnsupportedDispatchPayload,
    UnsupportedReason,
    UnsupportedTerminalizationOutcome,
    WecomAppDispatchTarget,
    WecomSmartRobotDispatchTarget,
)


REQUEST = "11111111-1111-1111-1111-111111111111"
INTENT = "22222222-2222-2222-2222-222222222222"
ITEM = "33333333-3333-3333-3333-333333333333"
LEASE = "44444444-4444-4444-4444-444444444444"
RUN = "55555555-5555-5555-5555-555555555555"
TERMINAL_REQUEST = "66666666-6666-6666-6666-666666666666"
ORG = "77777777-7777-7777-7777-777777777777"
NOW = datetime(2026, 8, 10, 8, tzinfo=timezone.utc)
ATTEMPT = "88888888-8888-8888-8888-888888888888"
PROVIDER_REQUEST = "provider-request-123"
IDEMPOTENCY = "9" * 64


class _Response:
    def __init__(self, data: object) -> None:
        self.data = data


class _Call:
    def __init__(self, database: _Database, name: str, params: dict[str, object]) -> None:
        self.database = database
        self.name = name
        self.params = params

    async def execute(self) -> _Response:
        self.database.calls.append((self.name, deepcopy(self.params)))
        values = self.database.responses[self.name]
        value = values.pop(0) if isinstance(values, list) else values
        if isinstance(value, BaseException):
            raise value
        return _Response(deepcopy(value))


class _Database:
    def __init__(self, responses: dict[str, object]) -> None:
        self.scope = DatabaseScope(
            None, None, DatabaseAccessKind.WORKER, "scheduled-wecom-payload-test",
        )
        self.responses = responses
        self.calls: list[tuple[str, dict[str, object]]] = []

    def rpc(self, name: str, params: dict[str, object]) -> _Call:
        return _Call(self, name, params)

    def table(self, _name: str) -> None:
        raise AssertionError("table access is forbidden")


def _claim():
    return parse_delivery_claim({
        "outcome": "claimed", "request_id": REQUEST, "claim_request_id": REQUEST,
        "intent_id": INTENT, "item_id": ITEM, "worker_id": "wecom-worker",
        "claim_kind": "continuation", "lease_token": LEASE, "lease_seconds": 60,
        "lease_expires_at": NOW, "previous_claim_request_id": None,
        "state_version": 7, "delivery_state_version": 7, "item_state_version": 3,
    })


def _payload(channel: str = "app") -> dict[str, object]:
    target = (
        {"org_id": ORG, "corp_id": "corp-1", "wecom_userid": "member-1"}
        if channel == "app" else {"org_id": ORG, "chatid": "chat-1"}
    )
    return {
        "outcome": "payload", "payload_revision": 2, "scheduled_run_id": RUN,
        "intent_id": INTENT, "item_id": ITEM, "item_key": "a" * 64,
        "ordinal": 1, "item_kind": "text", "source_role": "text",
        "source_revision": 1, "source_identity_hash": "b" * 64,
        "content_identity_hash": "c" * 64, "result_hash": "d" * 64,
        "target_hash": "e" * 64, "channel": channel, "target": target,
        "provider_revision": 4, "delivery_state_version": 7,
        "item_state_version": 3, "message_type": "text",
        "text": "Safe scheduled result", "payload_hash": "f" * 64,
    }


def _terminal(
    outcome: str = "terminalized", delivery_status: str = "failed",
) -> dict[str, object]:
    return {
        "outcome": outcome, "request_id": TERMINAL_REQUEST, "intent_id": INTENT,
        "item_id": ITEM, "reason_code": "wecom_artifact_identity_unsupported",
        "item_status": "cancelled", "delivery_status": delivery_status,
        "delivery_state_version": 8, "item_state_version": 4,
        "terminalized_at": NOW,
    }


def _repository(response: object, rpc_name: str):
    database = _Database({rpc_name: response})
    return database, PostgresScheduledWecomDeliveryRepository(database)


def _recovery(
    *, outcome: RecoveryOutcome = RecoveryOutcome.RECOVERED,
    status: AttemptStatus = AttemptStatus.PREPARED,
) -> PreparedRecovery:
    return PreparedRecovery(
        outcome=outcome,
        attempt=DispatchAttempt(
            outcome=AttemptOperationOutcome.READBACK,
            fence=DeliveryFence(
                intent_id=INTENT, item_id=ITEM, claim_request_id=REQUEST,
                lease_token=LEASE, worker_id="wecom-worker",
                delivery_state_version=7, item_state_version=3,
            ),
            attempt_id=ATTEMPT, attempt_number=2,
            identity=ProviderDispatchIdentity(
                provider_request_id=PROVIDER_REQUEST,
                idempotency_key=IDEMPOTENCY, provider_revision=4,
            ),
            payload_versions=DispatchPayloadVersions(
                delivery_state_version=5, item_state_version=0,
            ),
            status=status,
        ),
        lease_expires_at=NOW,
    )


@pytest.mark.asyncio
async def test_prepared_payload_read_uses_exact_recovery_and_provider_fences() -> None:
    rpc = "read_agent_runtime_scheduled_wecom_prepared_payload_v1"
    raw = _payload()
    raw["delivery_state_version"] = 5
    raw["item_state_version"] = 0
    database, repository = _repository(raw, rpc)

    result = await repository.read_prepared_dispatch_payload(_recovery())

    assert isinstance(result, DispatchPayload)
    assert database.calls == [(rpc, {
        "p_recovery_request_id": REQUEST, "p_intent_id": INTENT,
        "p_item_id": ITEM, "p_attempt_id": ATTEMPT, "p_attempt_number": 2,
        "p_claim_request_id": REQUEST, "p_lease_token": LEASE,
        "p_worker_id": "wecom-worker", "p_expected_delivery_state_version": 7,
        "p_expected_item_state_version": 3,
        "p_provider_request_id": PROVIDER_REQUEST,
        "p_idempotency_key": IDEMPOTENCY, "p_provider_revision": 4,
    })]


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [OperationalError("lost"), InterfaceError("lost")])
async def test_prepared_payload_response_loss_retries_same_pure_read(error: Exception) -> None:
    rpc = "read_agent_runtime_scheduled_wecom_prepared_payload_v1"
    raw = _payload("smart_robot")
    raw["delivery_state_version"] = 5
    raw["item_state_version"] = 0
    database, repository = _repository([error, raw], rpc)

    result = await repository.read_prepared_dispatch_payload(_recovery())

    assert isinstance(result, DispatchPayload)
    assert len(database.calls) == 2 and database.calls[0] == database.calls[1]


@pytest.mark.asyncio
async def test_prepared_payload_rejects_illegal_recovery_before_rpc() -> None:
    for recovery in (
        _recovery(outcome=RecoveryOutcome.FENCED),
        _recovery(status=AttemptStatus.DISPATCH_STARTED),
    ):
        database, repository = _repository(
            _payload(), "read_agent_runtime_scheduled_wecom_prepared_payload_v1",
        )
        with pytest.raises(PersistenceContractError):
            await repository.read_prepared_dispatch_payload(recovery)
        assert database.calls == []


@pytest.mark.asyncio
async def test_prepared_payload_parser_rechecks_recovery_derived_identity() -> None:
    rpc = "read_agent_runtime_scheduled_wecom_prepared_payload_v1"
    for field, value in (
        ("intent_id", ITEM), ("item_id", INTENT), ("provider_revision", 5),
    ):
        raw = _payload()
        raw["delivery_state_version"] = 5
        raw["item_state_version"] = 0
        raw[field] = value
        _, repository = _repository(raw, rpc)
        with pytest.raises(PersistenceContractError):
            await repository.read_prepared_dispatch_payload(_recovery())


@pytest.mark.asyncio
async def test_prepared_payload_cancelled_error_propagates() -> None:
    rpc = "read_agent_runtime_scheduled_wecom_prepared_payload_v1"
    _, repository = _repository(asyncio.CancelledError(), rpc)
    with pytest.raises(asyncio.CancelledError):
        await repository.read_prepared_dispatch_payload(_recovery())


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["app", "smart_robot"])
async def test_payload_read_projects_exact_target_and_claim_fence(channel: str) -> None:
    rpc = "read_agent_runtime_scheduled_wecom_dispatch_payload_v1"
    database, repository = _repository(_payload(channel), rpc)

    result = await repository.read_dispatch_payload(_claim())

    assert isinstance(result, DispatchPayload)
    assert result.outcome is DispatchPayloadOutcome.PAYLOAD
    assert result.channel is DispatchChannel(channel)
    if channel == "app":
        assert result.target == WecomAppDispatchTarget(
            org_id=ORG, corp_id="corp-1", wecom_userid="member-1",
        )
    else:
        assert result.target == WecomSmartRobotDispatchTarget(org_id=ORG, chatid="chat-1")
    assert database.calls == [(rpc, {
        "p_intent_id": INTENT, "p_item_id": ITEM, "p_claim_request_id": REQUEST,
        "p_lease_token": LEASE, "p_worker_id": "wecom-worker",
        "p_expected_delivery_state_version": 7,
        "p_expected_item_state_version": 3,
    })]


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["app", "smart_robot"])
async def test_payload_accepts_db_safe_url_and_opaque_secret_identifier(channel: str) -> None:
    raw = _payload(channel)
    raw["text"] = "See https://example.com/scheduled/result"
    raw["target"] = (
        {"org_id": ORG, "corp_id": "corp-secret", "wecom_userid": "secret-member"}
        if channel == "app" else {"org_id": ORG, "chatid": "secret-chat"}
    )
    _, repository = _repository(
        raw, "read_agent_runtime_scheduled_wecom_dispatch_payload_v1",
    )

    result = await repository.read_dispatch_payload(_claim())

    assert isinstance(result, DispatchPayload)
    assert result.text == "See https://example.com/scheduled/result"
    assert "secret" in (
        result.target.wecom_userid
        if isinstance(result.target, WecomAppDispatchTarget) else result.target.chatid
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("revision", [1, 2])
async def test_payload_parser_accepts_v2_and_legacy_rollback_revision(revision: int) -> None:
    raw = _payload("smart_robot")
    raw["payload_revision"] = revision
    raw["text"] = "任务已完成：中文摘要"
    raw["target"] = {"org_id": ORG, "chatid": "群聊-甲"}
    _, repository = _repository(
        raw, "read_agent_runtime_scheduled_wecom_dispatch_payload_v1",
    )

    result = await repository.read_dispatch_payload(_claim())

    assert isinstance(result, DispatchPayload)
    assert result.payload_revision == revision
    assert result.text == "任务已完成：中文摘要"
    assert result.target == WecomSmartRobotDispatchTarget(org_id=ORG, chatid="群聊-甲")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raw", "kind", "reason"),
    [
        (
            {"outcome": "unsupported", "reason_code": reason.value},
            UnsupportedDispatchPayload,
            reason,
        )
        for reason in UnsupportedReason
    ] + [
        (
            {"outcome": "unavailable", "reason_code": reason.value},
            UnavailableDispatchPayload,
            reason,
        )
        for reason in UnavailableReason
    ],
)
async def test_payload_read_preserves_non_payload_outcome(raw, kind, reason) -> None:
    rpc = "read_agent_runtime_scheduled_wecom_dispatch_payload_v1"
    _, repository = _repository(raw, rpc)

    result = await repository.read_dispatch_payload(_claim())

    assert isinstance(result, kind)
    assert result.reason is reason


@pytest.mark.asyncio
async def test_payload_not_found_is_none_and_fenced_fails_closed() -> None:
    rpc = "read_agent_runtime_scheduled_wecom_dispatch_payload_v1"
    _, repository = _repository({"outcome": "not_found"}, rpc)
    assert await repository.read_dispatch_payload(_claim()) is None

    _, repository = _repository({"outcome": "fenced"}, rpc)
    with pytest.raises(PersistenceContractError, match="dispatch_payload_fenced"):
        await repository.read_dispatch_payload(_claim())


@pytest.mark.asyncio
async def test_payload_read_does_not_retry_connection_loss() -> None:
    rpc = "read_agent_runtime_scheduled_wecom_dispatch_payload_v1"
    database, repository = _repository([OperationalError("lost"), _payload()], rpc)
    with pytest.raises(OperationalError, match="lost"):
        await repository.read_dispatch_payload(_claim())
    assert len(database.calls) == 1


@pytest.mark.asyncio
async def test_payload_parser_rejects_extra_missing_malformed_and_sensitive_fields() -> None:
    malformed = []
    missing = _payload()
    missing.pop("payload_hash")
    malformed.append(missing)
    for extra_field in (
        "access_token", "client_secret", "artifact_path", "text_content",
        "structured_content",
    ):
        extra = _payload()
        extra[extra_field] = "forbidden"
        malformed.append(extra)
    for field, value in (
        ("scheduled_run_id", "not-a-uuid"), ("item_key", "x" * 64),
        ("source_identity_hash", "A" * 64), ("source_revision", 2),
        ("payload_revision", 0), ("payload_revision", 3),
        ("provider_revision", True),
        ("delivery_state_version", 8), ("item_state_version", 4),
        ("item_kind", "artifact_identity"), ("message_type", "markdown"),
        ("text", "x" * 501), ("channel", "webhook"),
    ):
        row = _payload()
        row[field] = value
        malformed.append(row)
    for text in ("line one\nline two", "control\x00value", "invalid\ud800unicode"):
        row = _payload()
        row["text"] = text
        malformed.append(row)
    for target in (
        {"org_id": ORG, "corp_id": "corp-1"},
        {"org_id": "fake-org", "corp_id": "corp-1", "wecom_userid": "member-1"},
        {
            "org_id": ORG, "corp_id": "corp-1", "wecom_userid": "member-1",
            "user_id": "internal",
        },
        {
            "org_id": ORG, "corp_id": "corp-1", "wecom_userid": "member-1",
            "access_token": "forbidden",
        },
        {"org_id": ORG, "corp_id": "corp-1", "wecom_userid": "member\x00bad"},
    ):
        row = _payload()
        row["target"] = target
        malformed.append(row)

    for raw in malformed:
        _, repository = _repository(raw, "read_agent_runtime_scheduled_wecom_dispatch_payload_v1")
        with pytest.raises(PersistenceContractError):
            await repository.read_dispatch_payload(_claim())


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["terminalized", "readback"])
@pytest.mark.parametrize("delivery_status", ["pending", "partial", "failed"])
async def test_terminalization_receipt_is_exact_and_typed(
    outcome: str, delivery_status: str,
) -> None:
    rpc = "terminalize_agent_runtime_scheduled_wecom_unsupported_item_v1"
    database, repository = _repository(_terminal(outcome, delivery_status), rpc)

    receipt = await repository.terminalize_unsupported(
        _claim(), request_id=TERMINAL_REQUEST,
    )

    assert receipt.outcome is UnsupportedTerminalizationOutcome(outcome)
    assert receipt.reason is UnsupportedReason.ARTIFACT_IDENTITY
    assert receipt.item_status is ItemStatus.CANCELLED
    assert receipt.delivery_status is DeliveryStatus(delivery_status)
    assert database.calls == [(rpc, {
        "p_request_id": TERMINAL_REQUEST, "p_intent_id": INTENT,
        "p_item_id": ITEM, "p_claim_request_id": REQUEST,
        "p_lease_token": LEASE, "p_worker_id": "wecom-worker",
        "p_expected_delivery_state_version": 7,
        "p_expected_item_state_version": 3,
    })]


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [OperationalError("lost"), InterfaceError("lost")])
async def test_terminalization_replays_once_with_identical_request(error: Exception) -> None:
    rpc = "terminalize_agent_runtime_scheduled_wecom_unsupported_item_v1"
    database, repository = _repository([error, _terminal("readback")], rpc)

    receipt = await repository.terminalize_unsupported(
        _claim(), request_id=TERMINAL_REQUEST,
    )

    assert receipt.outcome is UnsupportedTerminalizationOutcome.READBACK
    assert len(database.calls) == 2
    assert database.calls[0] == database.calls[1]
    assert all(value not in {8, 4} for value in database.calls[0][1].values())


@pytest.mark.asyncio
async def test_terminalization_fences_conflict_and_identity_drift() -> None:
    rpc = "terminalize_agent_runtime_scheduled_wecom_unsupported_item_v1"
    for raw in ({"outcome": "fenced"}, {"outcome": "not_found"}):
        _, repository = _repository(raw, rpc)
        with pytest.raises(PersistenceContractError):
            await repository.terminalize_unsupported(_claim(), request_id=TERMINAL_REQUEST)

    for field, value in (
        ("request_id", REQUEST), ("intent_id", ITEM), ("item_id", INTENT),
        ("outcome", "recorded"), ("reason_code", "caller_reason"),
        ("item_status", "accepted"), ("delivery_status", "completed"),
        ("delivery_state_version", 0), ("item_state_version", True),
    ):
        raw = _terminal()
        raw[field] = value
        _, repository = _repository(raw, rpc)
        with pytest.raises(PersistenceContractError):
            await repository.terminalize_unsupported(_claim(), request_id=TERMINAL_REQUEST)

    raw = _terminal()
    raw["terminal_reason"] = "free text"
    _, repository = _repository(raw, rpc)
    with pytest.raises(PersistenceContractError):
        await repository.terminalize_unsupported(_claim(), request_id=TERMINAL_REQUEST)
