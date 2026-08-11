from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
from typing import Any

import pytest
from loguru import logger

from core.db_scope import (
    AsyncScopedDatabaseClient,
    DatabaseAccessKind,
    DatabaseScope,
    database_scope_from_client,
)
from services.agent.runtime.credential_broker import CredentialAuditEvent
from services.agent.runtime.wecom_app_credentials import (
    WECOM_APP_PROVIDER,
    WECOM_APP_SEND_PURPOSE,
)
from services.configuration.material_service import SecretMaterialService
from services.wecom.scheduled_runtime_composition import (
    ScheduledWecomCredentialJournalAuditSink,
    build_scheduled_wecom_runtime_components,
)


ORG_ID = "11111111-1111-1111-1111-111111111111"
IDENTITY = "wecom-app:" + "a" * 64
SECRET_SAMPLE = "must-never-enter-scheduled-wecom-journal"


class _Database:
    def __init__(self) -> None:
        self.rpc_calls = 0

    def rpc(self, _name: str, _params: object = None) -> object:
        self.rpc_calls += 1
        raise AssertionError("composition must not call RPC")


class _HttpClient:
    def __init__(self) -> None:
        self.calls = 0

    async def post(self, *_args: object, **_kwargs: object) -> object:
        self.calls += 1
        raise AssertionError("composition must not send")


class _AuditSink:
    def __init__(self) -> None:
        self.calls = 0

    async def record(self, _event: CredentialAuditEvent) -> None:
        self.calls += 1


class _Getters:
    def __init__(self) -> None:
        self.ws_calls = 0
        self.token_calls = 0

    def ws(self, _org_id: str) -> object | None:
        self.ws_calls += 1
        raise AssertionError("composition must not resolve Smart transport")

    async def token(self, *_args: object, **_kwargs: object) -> str | None:
        self.token_calls += 1
        raise AssertionError("composition must not read credentials")


def _material_service() -> SecretMaterialService:
    return SecretMaterialService(object())  # type: ignore[arg-type]


def _dependencies() -> dict[str, Any]:
    getters = _Getters()
    return {
        "database": _Database(),
        "get_ws_client": getters.ws,
        "material_service": _material_service(),
        "get_access_token": getters.token,
        "outbound_http_client": _HttpClient(),
        "worker_id": "scheduled-wecom-runtime-01",
        "audit_sink": _AuditSink(),
        "_getters": getters,
    }


def test_builder_composes_one_non_owning_runtime_graph_without_side_effects() -> None:
    dependencies = _dependencies()
    getters = dependencies.pop("_getters")

    components = build_scheduled_wecom_runtime_components(**dependencies)

    repository_database = components.repository._database
    scope = database_scope_from_client(repository_database)
    assert isinstance(repository_database, AsyncScopedDatabaseClient)
    assert scope is not None
    assert scope.actor_user_id is None
    assert scope.org_id is None
    assert scope.access_kind is DatabaseAccessKind.WORKER
    assert scope.request_id.startswith("scheduled-wecom-runtime:")
    assert len(scope.request_id) <= 128
    assert components.worker._repository is components.repository
    assert components.worker._router is components.router
    assert components.router._repository is components.repository
    assert components.router._smart_dispatch._repository is components.repository
    assert components.router._app_dispatch._repository is components.repository
    assert (
        components.router._app_binding_resolver
        is components.app_binding_resolver
    )
    assert (
        components.router._smart_dispatch._transport_resolver
        is components.smart_transport_resolver
    )
    assert components.app_binding_resolver._database is dependencies["database"]
    assert components.smart_transport_resolver._get_ws_client == dependencies[
        "get_ws_client"
    ]
    assert components.app_binding_resolver._audit_sink is dependencies["audit_sink"]
    assert dependencies["database"].rpc_calls == 0
    assert dependencies["outbound_http_client"].calls == 0
    assert dependencies["audit_sink"].calls == 0
    assert getters.ws_calls == 0
    assert getters.token_calls == 0
    with pytest.raises(FrozenInstanceError):
        components.worker = object()  # type: ignore[misc]


def test_builder_defaults_to_secret_free_journal_sink() -> None:
    dependencies = _dependencies()
    dependencies.pop("_getters")
    dependencies["audit_sink"] = None

    components = build_scheduled_wecom_runtime_components(**dependencies)

    assert isinstance(
        components.app_binding_resolver._audit_sink,
        ScheduledWecomCredentialJournalAuditSink,
    )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("database", None),
        ("get_ws_client", None),
        ("material_service", None),
        ("get_access_token", None),
        ("outbound_http_client", None),
        ("audit_sink", object()),
        ("worker_id", " "),
    ),
)
def test_missing_or_invalid_dependencies_fail_before_any_side_effect(
    field: str,
    value: object,
) -> None:
    dependencies = _dependencies()
    getters = dependencies.pop("_getters")
    dependencies[field] = value

    with pytest.raises(ValueError):
        build_scheduled_wecom_runtime_components(**dependencies)

    database = dependencies.get("database")
    if isinstance(database, _Database):
        assert database.rpc_calls == 0
    http_client = dependencies.get("outbound_http_client")
    if isinstance(http_client, _HttpClient):
        assert http_client.calls == 0
    audit_sink = dependencies.get("audit_sink")
    if isinstance(audit_sink, _AuditSink):
        assert audit_sink.calls == 0
    assert getters.ws_calls == 0
    assert getters.token_calls == 0


def test_builder_rejects_a_pre_scoped_database() -> None:
    dependencies = _dependencies()
    dependencies.pop("_getters")
    raw_database = dependencies["database"]
    dependencies["database"] = AsyncScopedDatabaseClient(
        raw_database,
        DatabaseScope(None, None, DatabaseAccessKind.WORKER, "already-scoped"),
    )

    with pytest.raises(ValueError, match="RAW_DATABASE"):
        build_scheduled_wecom_runtime_components(**dependencies)

    assert raw_database.rpc_calls == 0


@pytest.mark.asyncio
async def test_journal_sink_logs_only_validated_whitelist_fields() -> None:
    messages: list[str] = []
    sink_id = logger.add(messages.append, format="{message}")
    event = _event()
    try:
        await ScheduledWecomCredentialJournalAuditSink().record(event)
    finally:
        logger.remove(sink_id)

    assert len(messages) == 1
    prefix, raw_fields = messages[0].strip().split(" | ", maxsplit=1)
    assert prefix == "scheduled_wecom_credential_audit"
    fields = json.loads(raw_fields)
    assert fields == {
        "tenant_id": ORG_ID,
        "handle": IDENTITY,
        "provider": WECOM_APP_PROVIDER,
        "revision": IDENTITY,
        "purpose": WECOM_APP_SEND_PURPOSE,
        "outcome": "issued",
        "occurred_at": "2026-08-11T00:00:00+00:00",
    }
    assert SECRET_SAMPLE not in messages[0]
    assert "token" not in messages[0].lower()
    assert "payload" not in messages[0].lower()
    assert "exception" not in messages[0].lower()
    assert "path" not in messages[0].lower()
    assert SECRET_SAMPLE not in repr(ScheduledWecomCredentialJournalAuditSink())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome",
    (
        "backend_not_ready",
        "unavailable",
        "backend_error",
        "binding_rejected",
        "issued",
    ),
)
async def test_journal_sink_accepts_only_broker_outcomes(outcome: str) -> None:
    sink_id = logger.add(lambda _message: None, format="{message}")
    try:
        await ScheduledWecomCredentialJournalAuditSink().record(
            replace(_event(), outcome=outcome),
        )
    finally:
        logger.remove(sink_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("tenant_id", "not-a-tenant"),
        ("handle", "wecom-app:invalid"),
        ("handle", None),
        ("provider", "other-provider"),
        ("revision", "wecom-app:invalid"),
        ("revision", None),
        ("purpose", "other-purpose"),
        ("outcome", "other-outcome"),
        ("occurred_at", datetime(2026, 8, 11)),
    ),
)
async def test_journal_sink_rejects_every_invalid_event_field(
    field: str,
    value: object,
) -> None:
    event = replace(_event(), **{field: value})

    with pytest.raises(ValueError, match="CREDENTIAL_AUDIT_INVALID"):
        await ScheduledWecomCredentialJournalAuditSink().record(event)


@pytest.mark.asyncio
async def test_journal_sink_rejects_non_event_objects() -> None:
    with pytest.raises(ValueError, match="CREDENTIAL_AUDIT_INVALID"):
        await ScheduledWecomCredentialJournalAuditSink().record(  # type: ignore[arg-type]
            object(),
        )


def _event() -> CredentialAuditEvent:
    return CredentialAuditEvent(
        tenant_id=ORG_ID,
        handle=IDENTITY,
        provider=WECOM_APP_PROVIDER,
        revision=IDENTITY,
        purpose=WECOM_APP_SEND_PURPOSE,
        outcome="issued",
        occurred_at=datetime(2026, 8, 11, tzinfo=timezone.utc),
    )
