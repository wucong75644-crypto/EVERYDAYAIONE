from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from services.agent.runtime.domain import (
    ActionAttempt, ActionAttemptStatus, Lease, RuntimeScope, ScopeKind,
)
from services.agent.runtime.executors.contracts import canonical_request_hash
from services.agent.runtime.executors.erp_factory import (
    OrgScopedErpDispatcherFactory,
)
from services.agent.runtime.executors.provider_adapters import ERPQueryProvider


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

    async def create(self, scope: RuntimeScope) -> _Dispatcher:
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
    assert receipt.state.value == "unknown"
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
    factory = OrgScopedErpDispatcherFactory(object())
    with pytest.raises(ValueError, match="ERP_ORG_SCOPE_REQUIRED"):
        await factory.create(RuntimeScope(ScopeKind.USER, "user-1", "user-1", None))


@pytest.mark.asyncio
async def test_missing_enterprise_config_fails_closed_without_constructing_client(
    monkeypatch,
) -> None:
    import services.org.config_resolver as config_resolver
    import services.kuaimai.client as client_module

    class _MissingResolver:
        def __init__(self, database) -> None:
            pass

        async def get_erp_credentials(self, org_id: str):
            raise ValueError("enterprise ERP config missing")

    def forbidden_client(**kwargs):
        raise AssertionError("platform/default client fallback")

    monkeypatch.setattr(config_resolver, "AsyncOrgConfigResolver", _MissingResolver)
    monkeypatch.setattr(client_module, "KuaiMaiClient", forbidden_client)
    factory = OrgScopedErpDispatcherFactory(object())
    with pytest.raises(ValueError, match="enterprise ERP config missing"):
        await factory.create(RuntimeScope(ScopeKind.USER, "user-1", "user-1", "org-a"))
