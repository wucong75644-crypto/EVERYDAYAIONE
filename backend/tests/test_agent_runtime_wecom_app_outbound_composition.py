from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

import pytest

from services.agent.runtime.credential_broker import (
    CredentialBroker,
    CredentialLease,
    InMemoryCredentialAuditSink,
    InMemoryCredentialBackend,
)
from services.agent.runtime.domain import RuntimeScope, ScopeKind
from services.agent.runtime.wecom_app_credentials import build_runtime_wecom_app_outbound
from services.wecom.app_outbound import (
    WecomAppOutboundErrorClass,
    WecomAppOutboundStatus,
)


NOW = datetime(2026, 8, 10, 9, 0, tzinfo=timezone.utc)
TOKEN = "test-token"


class _Broker:
    def require_production_ready(self) -> None:
        pass

    async def resolve(self, **binding: object) -> CredentialLease[object]:
        return CredentialLease(
            tenant_id="org-a",
            handle="opaque-handle",
            provider="wecom_app",
            revision="revision-1",
            purpose="wecom.app.send",
            expires_at=NOW + timedelta(minutes=1),
            material=object(),
            clock=lambda: NOW,
        )


class _Exchange:
    operational = True
    production_ready = True

    async def exchange(self, material: object) -> str:
        return TOKEN


class _MockExchange(_Exchange):
    production_ready = False


class _Response:
    status_code = 200

    def json(self) -> object:
        return {"errcode": 0, "msgid": "provider-message-1"}


class _HttpClient:
    is_closed = False

    def __init__(self) -> None:
        self.calls = 0

    async def post(
        self,
        url: str,
        *,
        params: Mapping[str, str],
        json: Mapping[str, Any],
        headers: Mapping[str, str],
    ) -> _Response:
        self.calls += 1
        assert params == {"access_token": TOKEN}
        return _Response()


def _outbound(exchange: object, client: object):
    return build_runtime_wecom_app_outbound(
        broker=_Broker(),  # type: ignore[arg-type]
        scope=RuntimeScope(ScopeKind.USER, "user-a", "user-a", "org-a"),
        credential_handle="opaque-handle",
        provider_revision="revision-1",
        token_exchange=exchange,  # type: ignore[arg-type]
        outbound_http_client=client,  # type: ignore[arg-type]
    )


def _payload() -> dict[str, Any]:
    return {
        "touser": "user-a",
        "msgtype": "text",
        "agentid": 1000,
        "text": {"content": "safe"},
    }


@pytest.mark.asyncio
async def test_composition_returns_typed_ack_without_token_http_exchange() -> None:
    client = _HttpClient()
    receipt = await _outbound(_Exchange(), client).send_typed(
        provider_request_id="runtime-request-001",
        target="user-a",
        payload=_payload(),
    )

    assert receipt.status is WecomAppOutboundStatus.ACKNOWLEDGED
    assert client.calls == 1


@pytest.mark.asyncio
async def test_mock_exchange_cannot_promote_transport_to_started() -> None:
    client = _HttpClient()
    receipt = await _outbound(_MockExchange(), client).send_typed(
        provider_request_id="runtime-request-002",
        target="user-a",
        payload=_payload(),
    )

    assert receipt.status is WecomAppOutboundStatus.NOT_STARTED
    assert receipt.error_class is WecomAppOutboundErrorClass.CREDENTIAL_UNAVAILABLE
    assert client.calls == 0


@pytest.mark.asyncio
async def test_nonproduction_real_broker_prevents_exchange_and_http() -> None:
    class TrackingExchange(_Exchange):
        def __init__(self) -> None:
            self.calls = 0

        async def exchange(self, material: object) -> str:
            self.calls += 1
            return TOKEN

    broker = CredentialBroker(
        InMemoryCredentialBackend(),
        InMemoryCredentialAuditSink(),
        clock=lambda: NOW,
    )
    exchange = TrackingExchange()
    client = _HttpClient()
    outbound = build_runtime_wecom_app_outbound(
        broker=broker,
        scope=RuntimeScope(ScopeKind.USER, "user-a", "user-a", "org-a"),
        credential_handle="opaque-handle",
        provider_revision="revision-1",
        token_exchange=exchange,
        outbound_http_client=client,
    )

    receipt = await outbound.send_typed(
        provider_request_id="runtime-request-003",
        target="user-a",
        payload=_payload(),
    )

    assert broker.readiness().production_ready is False
    assert receipt.status is WecomAppOutboundStatus.NOT_STARTED
    assert receipt.error_class is WecomAppOutboundErrorClass.CREDENTIAL_UNAVAILABLE
    assert exchange.calls == 0
    assert client.calls == 0


def test_composition_requires_explicit_app_http_client() -> None:
    with pytest.raises(ValueError, match="WECOM_APP_HTTP_CLIENT_REQUIRED"):
        _outbound(_Exchange(), None)
