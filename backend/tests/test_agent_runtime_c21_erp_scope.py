from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from services.agent.runtime.domain import (
    ActionAttempt, ActionAttemptStatus, Lease, RuntimeScope, ScopeKind,
)
from services.agent.runtime.executors.contracts import canonical_request_hash
from services.agent.runtime.executors.erp_factory import (
    OrgScopedErpDispatcherFactory,
)
from services.agent.runtime.executors.provider_adapters import ERPQueryProvider
from services.agent.runtime.production_composition import (
    build_runtime_erp_read_registry,
)


def _attempt(org_id: str | None, request: dict[str, object]) -> ActionAttempt:
    now = datetime.now(timezone.utc)
    return ActionAttempt(
        attempt_id=f"attempt-{org_id or 'missing'}", action_id="action-1",
        scope=RuntimeScope(
            ScopeKind.USER, "user-1", "user-1", org_id,
        ), attempt_number=1, status=ActionAttemptStatus.DISPATCHING,
        worker_id="worker-1", idempotency_key="idem-1",
        request_hash=canonical_request_hash(request),
        lease=Lease(fencing_token="fence-1", expires_at=now + timedelta(minutes=1)),
        started_at=now,
    )


class _Dispatcher:
    def __init__(self, org_id: str) -> None:
        self.org_id = org_id
        self.calls: list[tuple[str, str, dict[str, object]]] = []
        self.closed = False

    async def execute(self, tool_name, action, params):
        self.calls.append((tool_name, action, params))
        return type("Result", (), {
            "status": "success", "summary": self.org_id, "data": [],
        })()

    async def close(self) -> None:
        self.closed = True


class _Factory:
    def __init__(self) -> None:
        self.dispatchers: dict[str, _Dispatcher] = {}
        self.scopes: list[RuntimeScope] = []

    async def create(self, attempt: ActionAttempt, request) -> _Dispatcher:
        scope = attempt.scope
        self.scopes.append(scope)
        dispatcher = _Dispatcher(scope.org_id or "missing")
        self.dispatchers[scope.org_id or "missing"] = dispatcher
        return dispatcher


@pytest.mark.asyncio
async def test_read_dispatcher_is_created_per_scope_and_payload_cannot_override() -> None:
    factory = _Factory()
    provider = ERPQueryProvider(
        tool_name="erp_trade_query", dispatcher_factory=factory,
    )
    request = {
        "action": "order_list", "params": {"org_id": "org-b"},
    }
    first = await provider.submit(
        _attempt("org-a", request), request, idempotency_key="i-a",
    )
    second = await provider.submit(
        _attempt("org-b", request), request, idempotency_key="i-b",
    )

    assert first.result["summary"] == "org-a"
    assert second.result["summary"] == "org-b"
    assert [scope.org_id for scope in factory.scopes] == ["org-a", "org-b"]
    assert all(dispatcher.closed for dispatcher in factory.dispatchers.values())


@pytest.mark.asyncio
async def test_real_specialist_executor_preserves_attempt_dispatch_context() -> None:
    factory = _Factory()
    registry = build_runtime_erp_read_registry(factory)
    descriptor, executor = registry.resolve("erp_trade_query")
    request = {
        "action": "order_list", "params": {},
        "_dispatch_context": {
            "dispatch_intent_id": "intent-1",
            "expected_action_version": 2,
            "expected_attempt_version": 7,
        },
    }
    attempt = _attempt("org-a", request)

    receipt = await executor.dispatch(attempt, request)

    assert descriptor.executor_type == "runtime_remote_read:erp_trade_query"
    assert receipt.outcome.value == "completed"
    assert factory.dispatchers["org-a"].calls == [
        ("erp_trade_query", "order_list", {}),
    ]


@pytest.mark.asyncio
async def test_missing_scope_org_is_rejected_before_dispatcher_creation() -> None:
    factory = _Factory()
    provider = ERPQueryProvider(
        tool_name="erp_trade_query", dispatcher_factory=factory,
    )
    with pytest.raises(ValueError, match="ERP_ORG_SCOPE_REQUIRED"):
        await provider.submit(
            _attempt(None, {"action": "order_list", "params": {}}),
            {"action": "order_list", "params": {}}, idempotency_key="i",
        )
    assert factory.scopes == []


@pytest.mark.asyncio
async def test_read_provider_rejects_write_registry_action_and_never_calls_write() -> None:
    factory = _Factory()
    provider = ERPQueryProvider(
        tool_name="erp_trade_query", dispatcher_factory=factory,
    )
    receipt = await provider.submit(
        _attempt("org-a", {"action": "order_create", "params": {}}),
        {"action": "order_create", "params": {}}, idempotency_key="i",
    )
    assert receipt.state.value == "failed"
    assert receipt.evidence["error_code"] == "ERP_ACTION_NOT_REGISTERED"
    assert factory.scopes == []


@pytest.mark.asyncio
async def test_write_provider_does_not_use_read_factory() -> None:
    factory = _Factory()
    dispatcher = _Dispatcher("legacy-bound")
    provider = ERPQueryProvider(
        dispatcher, tool_name="erp_execute", write=True,
        dispatcher_factory=factory,
    )
    request = {"action": "order_create", "params": {}}
    await provider.submit(_attempt("org-a", request), request, idempotency_key="i")
    assert factory.scopes == []


@pytest.mark.asyncio
async def test_factory_requires_org_and_never_falls_back_to_platform_config() -> None:
    factory = OrgScopedErpDispatcherFactory(
        object(), worker_id="worker-1", material_service=MagicMock(),
    )
    with pytest.raises(ValueError, match="ERP_ORG_SCOPE_REQUIRED"):
        await factory.create(
            _attempt(None, {"action": "order_list", "params": {}}),
            {"_dispatch_context": {"expected_attempt_version": 1}},
        )


@pytest.mark.asyncio
async def test_missing_enterprise_config_fails_closed_without_constructing_client(
    monkeypatch,
) -> None:
    import services.configuration.bundles as bundles
    import services.kuaimai.client as client_module

    async def missing_bundle(self, params):
        raise ValueError("enterprise ERP config missing")

    def forbidden_client(**kwargs):
        raise AssertionError("platform/default client fallback")

    monkeypatch.setattr(
        bundles.AsyncSecretBundleResolver, "runtime_erp", missing_bundle,
    )
    monkeypatch.setattr(client_module, "KuaiMaiClient", forbidden_client)
    factory = OrgScopedErpDispatcherFactory(
        object(), worker_id="worker-1", material_service=MagicMock(),
    )
    with pytest.raises(ValueError, match="enterprise ERP config missing"):
        await factory.create(
            _attempt("org-a", {"action": "order_list", "params": {}}),
            {"_dispatch_context": {"expected_attempt_version": 1}},
        )


@pytest.mark.asyncio
async def test_factory_builds_existing_dispatcher_from_fenced_tenant_bundle(
    monkeypatch,
) -> None:
    import services.configuration.bundles as bundles
    import services.kuaimai.client as client_module
    import services.kuaimai.dispatcher as dispatcher_module

    resolver_calls: list[dict[str, object]] = []
    client_kwargs: dict[str, object] = {}
    dispatcher_kwargs: dict[str, object] = {}

    async def runtime_bundle(self, params):
        resolver_calls.append(dict(params))
        return SimpleNamespace(
            values={
                "erp.app_credentials": {
                    "app_key": "tenant-app", "app_secret": "tenant-secret",
                },
                "erp.token_pair": {
                    "access_token": "tenant-access",
                    "refresh_token": "tenant-refresh",
                },
            },
            versions={"erp.token_pair": 4},
        )

    class Client:
        def __init__(self, **kwargs):
            client_kwargs.update(kwargs)

        async def load_cached_token(self):
            raise AssertionError("Runtime must not use Redis as ERP token source")

        async def close(self):
            return None

    class Dispatcher:
        def __init__(self, client, **kwargs):
            dispatcher_kwargs.update(kwargs)
            self.client = client

    class Database:
        def __init__(self):
            self.calls: list[tuple[str, dict[str, object]]] = []

        def rpc(self, name, params):
            self.calls.append((name, dict(params)))

            async def execute():
                return SimpleNamespace(data={"version": 5})

            return SimpleNamespace(execute=execute)

    envelope = SimpleNamespace(
        payload_ciphertext="ciphertext", wrapped_dek="wrapped",
        kek_version="kek-v1",
    )
    material = MagicMock()
    material.encrypt_payload.return_value = envelope
    database = Database()
    monkeypatch.setattr(
        bundles.AsyncSecretBundleResolver, "runtime_erp", runtime_bundle,
    )
    monkeypatch.setattr(client_module, "KuaiMaiClient", Client)
    monkeypatch.setattr(dispatcher_module, "ErpDispatcher", Dispatcher)
    factory = OrgScopedErpDispatcherFactory(
        database, worker_id="worker-1", material_service=material,
    )
    request = {
        "action": "order_list", "params": {},
        "_dispatch_context": {"expected_attempt_version": 7},
    }
    attempt = _attempt("org-a", request)

    dispatcher = await factory.create(attempt, request)
    await client_kwargs["token_persister"](
        "org-a", "rotated-access", "rotated-refresh",
    )

    expected_fence = {
        "p_attempt_id": attempt.attempt_id,
        "p_worker_id": "worker-1",
        "p_execution_token": "fence-1",
        "p_expected_attempt_version": 7,
        "p_request_hash": attempt.request_hash,
    }
    assert resolver_calls == [expected_fence]
    assert client_kwargs == {
        "app_key": "tenant-app", "app_secret": "tenant-secret",
        "access_token": "tenant-access", "refresh_token": "tenant-refresh",
        "org_id": "org-a", "token_persister": client_kwargs["token_persister"],
    }
    assert dispatcher.client is not None
    assert dispatcher_kwargs == {
        "db_source": None, "record_param_knowledge": False,
        "log_request_params": False,
    }
    material.encrypt_payload.assert_called_once_with(
        scope_kind="organization", scope_id="org-a",
        secret_name="erp.token_pair", payload_version=5,
        payload={
            "access_token": "rotated-access",
            "refresh_token": "rotated-refresh",
        },
    )
    assert database.calls == [(
        "rotate_agent_runtime_erp_token_pair_v1",
        {
            **expected_fence,
            "p_secret_envelope": {
                "payload_ciphertext": "ciphertext",
                "wrapped_dek": "wrapped", "kek_version": "kek-v1",
            },
            "p_expected_config_version": 4,
        },
    )]


@pytest.mark.asyncio
async def test_erp_receipt_does_not_contain_runtime_secret_material() -> None:
    secret = "erp-app-secret-value"
    factory = _Factory()
    provider = ERPQueryProvider(
        tool_name="erp_trade_query", dispatcher_factory=factory,
    )
    request = {"action": "order_list", "params": {}}
    receipt = await provider.submit(
        _attempt("org-a", request), request, idempotency_key="i",
    )
    assert secret not in repr(receipt)
    assert secret not in repr(_attempt("org-a", request))
