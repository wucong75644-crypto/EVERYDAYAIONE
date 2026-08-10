from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Mapping

import pytest

from core.db_scope import DatabaseAccessKind
from services.agent.runtime.credential_broker import (
    BackendCredential,
    CredentialAuditEvent,
    CredentialBrokerError,
)
from services.agent.runtime.wecom_app_credentials import (
    WECOM_APP_PROVIDER,
    WECOM_APP_SEND_PURPOSE,
)
from services.configuration.bundles import ResolvedConfigurationBundle
from services.configuration.material_service import SecretMaterialService
from services.wecom import scheduled_app_binding as adapter
from services.wecom.app_outbound import WecomAppOutboundStatus


ORG_A = "10000000-0000-0000-0000-000000000001"
ORG_B = "20000000-0000-0000-0000-000000000002"
CORP_A = "corp-a"
CORP_B = "corp-b"
SECRET_A = "scheduled-secret-a-test-only"
SECRET_B = "scheduled-secret-b-test-only"
TOKEN_A = "scheduled-token-a-test-only"
KEYS = (
    "wecom.corp_id",
    "wecom.oauth_agent_id",
    "wecom.oauth_agent_secret",
)


def _bundle(
    *,
    corp_id: object = CORP_A,
    agent_id: object = "1001",
    secret: object = SECRET_A,
    versions: tuple[int, int, int] = (1, 2, 3),
    source: str = "organization",
) -> ResolvedConfigurationBundle:
    payload = secret if isinstance(secret, Mapping) else {"agent_secret": secret}
    return ResolvedConfigurationBundle(
        name="wecom.app",
        values=MappingProxyType({
            "wecom.corp_id": corp_id,
            "wecom.oauth_agent_id": agent_id,
            "wecom.oauth_agent_secret": payload,
        }),
        sources=MappingProxyType({key: source for key in KEYS}),
        versions=MappingProxyType(dict(zip(KEYS, versions, strict=True))),
    )


class _Response:
    status_code = 200

    def json(self) -> object:
        return {"errcode": 0, "msgid": "scheduled-provider-message"}


class _HttpClient:
    is_closed = False

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def post(self, url: str, **kwargs: object) -> _Response:
        self.calls.append({"url": url, **kwargs})
        return _Response()


class _TokenManager:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    async def __call__(self, org_id: str, corp_id: str, secret: str) -> str:
        self.calls.append((org_id, corp_id, secret))
        return TOKEN_A


class _Database:
    def rpc(self, _name: str, _params: object = None) -> object:
        raise AssertionError("patched bundle resolver must own test reads")


class _AuditSink:
    def __init__(self) -> None:
        self.events: list[CredentialAuditEvent] = []

    async def record(self, event: CredentialAuditEvent) -> None:
        self.events.append(event)

    def __repr__(self) -> str:
        return "_AuditSink(secret-free)"


def _material_service() -> SecretMaterialService:
    return SecretMaterialService(object())  # type: ignore[arg-type]


def _resolver(
    *,
    token_manager: object | None = None,
    http_client: object | None = None,
    audit_sink: object | None = None,
) -> adapter.ScheduledWecomAppBindingResolver:
    return adapter.ScheduledWecomAppBindingResolver(
        database=_Database(),
        material_service=_material_service(),
        get_access_token=token_manager or _TokenManager(),  # type: ignore[arg-type]
        outbound_http_client=http_client or _HttpClient(),  # type: ignore[arg-type]
        audit_sink=audit_sink or _AuditSink(),  # type: ignore[arg-type]
    )


def _install_bundles(
    monkeypatch: pytest.MonkeyPatch,
    bundles: Mapping[str, ResolvedConfigurationBundle | BaseException],
) -> list[object]:
    scoped_databases: list[object] = []

    class Resolver:
        def __init__(self, database: object, _material: object) -> None:
            scoped_databases.append(database)

        async def wecom_app(self) -> ResolvedConfigurationBundle:
            scope = scoped_databases[-1].scope  # type: ignore[attr-defined]
            result = bundles[scope.org_id]
            if isinstance(result, BaseException):
                raise result
            return result

    monkeypatch.setattr(adapter, "AsyncSecretBundleResolver", Resolver)
    return scoped_databases


@pytest.mark.parametrize(
    ("dependency", "error"),
    [
        ("database", "WECOM_APP_DATABASE_REQUIRED"),
        ("material_service", "WECOM_APP_MATERIAL_SERVICE_REQUIRED"),
        ("get_access_token", "WECOM_APP_TOKEN_MANAGER_REQUIRED"),
        ("outbound_http_client", "WECOM_APP_HTTP_CLIENT_REQUIRED"),
        ("audit_sink", "WECOM_APP_CREDENTIAL_AUDIT_REQUIRED"),
    ],
)
def test_all_five_trusted_dependencies_are_required(
    dependency: str,
    error: str,
) -> None:
    dependencies: dict[str, object] = {
        "database": _Database(),
        "material_service": _material_service(),
        "get_access_token": _TokenManager(),
        "outbound_http_client": _HttpClient(),
        "audit_sink": _AuditSink(),
    }
    dependencies[dependency] = None

    with pytest.raises(ValueError, match=f"^{error}$"):
        adapter.ScheduledWecomAppBindingResolver(**dependencies)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_two_tenants_resolve_isolated_exact_bindings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scopes = _install_bundles(monkeypatch, {
        ORG_A: _bundle(),
        ORG_B: _bundle(corp_id=CORP_B, agent_id="2002", secret=SECRET_B),
    })
    token_manager = _TokenManager()
    resolver = _resolver(token_manager=token_manager)

    binding_a = await resolver.resolve_app_binding(org_id=ORG_A, corp_id=CORP_A)
    binding_b = await resolver.resolve_app_binding(org_id=ORG_B, corp_id=CORP_B)

    assert binding_a is not None and binding_b is not None
    assert (binding_a.org_id, binding_a.corp_id, binding_a.agent_id) == (
        ORG_A, CORP_A, 1001,
    )
    assert (binding_b.org_id, binding_b.corp_id, binding_b.agent_id) == (
        ORG_B, CORP_B, 2002,
    )
    assert binding_a.transport is not binding_b.transport
    assert [database.scope.org_id for database in scopes] == [ORG_A, ORG_B]  # type: ignore[attr-defined]
    assert await binding_a.transport._token_provider() == TOKEN_A  # type: ignore[attr-defined]
    assert await binding_b.transport._token_provider() == TOKEN_A  # type: ignore[attr-defined]
    assert token_manager.calls == [
        (ORG_A, CORP_A, SECRET_A),
        (ORG_B, CORP_B, SECRET_B),
    ]


@pytest.mark.asyncio
async def test_scope_is_actorless_worker_exact_org_and_bounded_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scopes = _install_bundles(monkeypatch, {ORG_A: _bundle()})

    assert await _resolver().resolve_app_binding(org_id=ORG_A, corp_id=CORP_A)
    scope = scopes[0].scope  # type: ignore[attr-defined]
    assert scope.actor_user_id is None
    assert scope.org_id == ORG_A
    assert scope.access_kind is DatabaseAccessKind.WORKER
    assert scope.request_id == f"scheduled-wecom-app-binding:{ORG_A}"
    assert len(scope.request_id) <= 128


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "org_id",
    ["invalid", "AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA", f" {ORG_A}"],
)
async def test_noncanonical_org_uuid_fails_before_database(
    monkeypatch: pytest.MonkeyPatch,
    org_id: str,
) -> None:
    scopes = _install_bundles(monkeypatch, {ORG_A: _bundle()})

    assert await _resolver().resolve_app_binding(org_id=org_id, corp_id=CORP_A) is None
    assert scopes == []


@pytest.mark.asyncio
async def test_expected_corp_must_match_exactly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_bundles(monkeypatch, {ORG_A: _bundle()})

    assert await _resolver().resolve_app_binding(org_id=ORG_A, corp_id=CORP_B) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("agent_id", [None, 0, 1, "", "0", "01", "+1", " 1", "1 "])
async def test_agent_id_must_be_canonical_positive_integer(
    monkeypatch: pytest.MonkeyPatch,
    agent_id: object,
) -> None:
    _install_bundles(monkeypatch, {ORG_A: _bundle(agent_id=agent_id)})

    assert await _resolver().resolve_app_binding(org_id=ORG_A, corp_id=CORP_A) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "secret",
    [None, "", " ", {"agent_secret": SECRET_A, "extra": "forbidden"}, {"wrong": SECRET_A}],
)
async def test_secret_payload_must_be_exact_and_nonempty(
    monkeypatch: pytest.MonkeyPatch,
    secret: object,
) -> None:
    _install_bundles(monkeypatch, {ORG_A: _bundle(secret=secret)})

    assert await _resolver().resolve_app_binding(org_id=ORG_A, corp_id=CORP_A) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("malformation", ["missing", "source", "version"])
async def test_missing_or_drifted_bundle_facts_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    malformation: str,
) -> None:
    valid = _bundle()
    values = dict(valid.values)
    sources = dict(valid.sources)
    versions = dict(valid.versions)
    if malformation == "missing":
        values.pop("wecom.oauth_agent_secret")
    elif malformation == "source":
        sources["wecom.corp_id"] = "platform"
    else:
        versions["wecom.oauth_agent_secret"] = 0
    malformed = ResolvedConfigurationBundle(
        name="wecom.app",
        values=MappingProxyType(values),
        sources=MappingProxyType(sources),
        versions=MappingProxyType(versions),
    )
    _install_bundles(monkeypatch, {ORG_A: malformed})

    assert await _resolver().resolve_app_binding(org_id=ORG_A, corp_id=CORP_A) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [RuntimeError(SECRET_A), ValueError(SECRET_A)])
async def test_database_or_decryption_failure_is_secret_free_none(
    monkeypatch: pytest.MonkeyPatch,
    failure: BaseException,
) -> None:
    _install_bundles(monkeypatch, {ORG_A: failure})

    result = await _resolver().resolve_app_binding(org_id=ORG_A, corp_id=CORP_A)
    assert result is None
    assert SECRET_A not in repr(result)


@pytest.mark.asyncio
async def test_resolver_cancellation_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_bundles(monkeypatch, {ORG_A: asyncio.CancelledError()})

    with pytest.raises(asyncio.CancelledError):
        await _resolver().resolve_app_binding(org_id=ORG_A, corp_id=CORP_A)


def test_handle_and_revision_change_only_with_nonsecret_binding_facts() -> None:
    first = adapter._binding_identity(
        ORG_A, CORP_A, 1001, dict(zip(KEYS, (1, 2, 3))),
    )
    same = adapter._binding_identity(
        ORG_A, CORP_A, 1001, dict(zip(KEYS, (1, 2, 3))),
    )
    changed = adapter._binding_identity(
        ORG_A, CORP_A, 1001, dict(zip(KEYS, (1, 2, 4))),
    )
    changed_agent = adapter._binding_identity(
        ORG_A, CORP_A, 1002, dict(zip(KEYS, (1, 2, 3))),
    )
    other_tenant = adapter._binding_identity(
        ORG_B, CORP_B, 1001, dict(zip(KEYS, (1, 2, 3))),
    )

    assert first == same
    assert first != changed != other_tenant
    assert changed_agent != first
    rendered = repr((first, changed, changed_agent, other_tenant))
    assert SECRET_A not in rendered and SECRET_B not in rendered
    assert CORP_A not in rendered and ORG_A not in rendered


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"tenant_id": ORG_B},
        {"handle": "wrong"},
        {"provider": "wrong"},
        {"revision": "wrong"},
        {"purpose": "wrong"},
    ],
)
async def test_exact_backend_fences_tenant_handle_revision_provider_and_purpose(
    changes: Mapping[str, str],
) -> None:
    material = adapter._WecomAppMaterial(ORG_A, CORP_A, SECRET_A)
    record = BackendCredential(
        tenant_id=ORG_A,
        handle="opaque-handle",
        provider=WECOM_APP_PROVIDER,
        revision="opaque-revision",
        purpose=WECOM_APP_SEND_PURPOSE,
        expires_at=datetime.max.replace(tzinfo=timezone.utc),
        _material=material,
    )
    backend = adapter._ExactCredentialBackend(record)
    binding = {
        "tenant_id": ORG_A,
        "handle": "opaque-handle",
        "provider": WECOM_APP_PROVIDER,
        "revision": "opaque-revision",
        "purpose": WECOM_APP_SEND_PURPOSE,
        **changes,
    }

    with pytest.raises(CredentialBrokerError, match="^CREDENTIAL_UNAVAILABLE$"):
        await backend.resolve(**binding)
    assert SECRET_A not in repr(backend)


@pytest.mark.asyncio
async def test_token_exchange_occurs_only_on_lease_consumption_and_reuses_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_bundles(monkeypatch, {ORG_A: _bundle()})
    token_manager = _TokenManager()
    http_client = _HttpClient()
    audit_sink = _AuditSink()
    binding = await _resolver(
        token_manager=token_manager,
        http_client=http_client,
        audit_sink=audit_sink,
    ).resolve_app_binding(org_id=ORG_A, corp_id=CORP_A)

    assert binding is not None
    assert token_manager.calls == []
    assert binding.transport._http_client is http_client  # type: ignore[attr-defined]
    receipt = await binding.transport.send_typed(
        provider_request_id="scheduled-binding-request-1",
        target="wecom-user-a",
        payload={
            "touser": "wecom-user-a",
            "msgtype": "text",
            "agentid": binding.agent_id,
            "text": {"content": "safe text"},
        },
    )

    assert receipt.status is WecomAppOutboundStatus.ACKNOWLEDGED
    assert token_manager.calls == [(ORG_A, CORP_A, SECRET_A)]
    assert len(http_client.calls) == 1
    assert [event.outcome for event in audit_sink.events] == ["issued"]
    assert SECRET_A not in repr(audit_sink.events)


@pytest.mark.asyncio
async def test_secret_never_appears_in_binding_repr_failure_or_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_bundles(monkeypatch, {ORG_A: _bundle()})

    class FailingTokenManager:
        async def __call__(self, _org: str, _corp: str, _secret: str) -> str:
            raise RuntimeError(SECRET_A)

    audit_sink = _AuditSink()
    resolver = _resolver(
        token_manager=FailingTokenManager(),
        audit_sink=audit_sink,
    )
    binding = await resolver.resolve_app_binding(
        org_id=ORG_A,
        corp_id=CORP_A,
    )
    assert binding is not None
    token = await binding.transport._token_provider()  # type: ignore[attr-defined]
    evidence = repr((
        binding,
        binding.transport,
        token,
        audit_sink,
        audit_sink.events,
        resolver,
    ))
    assert token is None
    assert [event.outcome for event in audit_sink.events] == ["issued"]
    assert SECRET_A not in evidence
    assert TOKEN_A not in evidence


@pytest.mark.asyncio
async def test_token_manager_cancellation_propagates_from_lease_consumer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_bundles(monkeypatch, {ORG_A: _bundle()})

    async def cancel(_org: str, _corp: str, _secret: str) -> str:
        raise asyncio.CancelledError

    binding = await _resolver(token_manager=cancel).resolve_app_binding(
        org_id=ORG_A,
        corp_id=CORP_A,
    )
    assert binding is not None

    with pytest.raises(asyncio.CancelledError):
        await binding.transport._token_provider()  # type: ignore[attr-defined]
