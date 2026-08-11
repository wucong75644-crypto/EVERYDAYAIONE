"""Contract tests for the read-only Runtime super-admin endpoints."""
from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient

ORG_ID = "11111111-1111-1111-1111-111111111111"
ADMIN_ID = "admin-001"
USER_ID = "user-001"


class FakeQuery:
    def __init__(self, data):
        self.data = data

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def maybe_single(self):
        return self

    def execute(self):
        return SimpleNamespace(data=self.data)


class FakeAuthDb:
    def __init__(self, role: str):
        self.role = role

    def table(self, name: str):
        assert name == "users"
        return FakeQuery({"role": self.role})


class FakeRpc:
    def __init__(self, data):
        self.data = data

    def execute(self):
        return SimpleNamespace(data=self.data)


class FakeAdminDb:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls: list[tuple[str, dict]] = []

    def rpc(self, name, params):
        self.calls.append((name, params))
        return FakeRpc(next(self.responses))


def _status_payload():
    return {
        "tenant_id": ORG_ID,
        "control": {"kill_epoch": 4, "state_version": 7, "production_enabled": False},
        "composition": {"state": "degraded", "summary": {}},
        "workers": [],
        "projection": {"state": "ready", "summary": {"backlog": 2, "dead": 1}},
        "unknown": {"state": "degraded", "summary": {"unknown": 3}},
        "production_ready": False,
    }


def _build_app(auth_db, user_id: str):
    from api.deps import get_current_user_id
    from api.deps import get_request_db
    from api.routes.runtime_admin import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    app.dependency_overrides[get_request_db] = lambda: auth_db
    return app


def test_super_admin_gets_all_read_models_and_tenant_scope(monkeypatch):
    from api.routes import runtime_admin

    admin_db = FakeAdminDb([
        _status_payload(),
        {"outcome": "readback", "org_id": ORG_ID, "items": []},
        {"outcome": "readback", "org_id": ORG_ID, "items": []},
        {"outcome": "readback", "org_id": ORG_ID, "cost_ledger": [], "side_effect_ledger": []},
    ])
    scopes = []
    monkeypatch.setattr(
        runtime_admin, "_admin_db",
        lambda user, org, request: (scopes.append((user, org, request)) or admin_db),
    )
    client = TestClient(_build_app(FakeAuthDb("super_admin"), ADMIN_ID))

    assert client.get(
        "/api/admin/agent-runtime/status",
        params={"org_id": ORG_ID}, headers={"Idempotency-Key": "read-status"},
    ).status_code == 200
    assert client.get("/api/admin/agent-runtime/provider-operations", params={"org_id": ORG_ID}).status_code == 200
    assert client.get("/api/admin/agent-runtime/recovery", params={"org_id": ORG_ID}).status_code == 200
    assert client.get("/api/admin/agent-runtime/cost-side-effects", params={"org_id": ORG_ID}).status_code == 200

    assert [name for name, _ in admin_db.calls] == [
        "get_agent_runtime_admin_status",
        "list_agent_runtime_provider_operations",
        "list_agent_runtime_recovery_snapshot",
        "get_agent_runtime_cost_side_effect_snapshot",
    ]
    assert admin_db.calls[1][1]["p_org_id"] == ORG_ID
    assert admin_db.calls[2][1]["p_org_id"] == ORG_ID
    assert admin_db.calls[3][1]["p_org_id"] == ORG_ID
    assert scopes[0] == (ADMIN_ID, ORG_ID, "read-status")
    assert all(scope[1] == ORG_ID for scope in scopes)
    rendered = repr(admin_db.calls)
    assert "token" not in rendered.lower()
    assert "payload" not in rendered.lower()


def test_non_super_admin_is_denied_before_runtime_rpc(monkeypatch):
    from api.routes import runtime_admin

    admin_db = FakeAdminDb([])
    monkeypatch.setattr(runtime_admin, "_admin_db", lambda *_args: admin_db)
    client = TestClient(_build_app(FakeAuthDb("admin"), USER_ID))
    response = client.get(
        "/api/admin/agent-runtime/status",
        params={"org_id": ORG_ID}, headers={"Idempotency-Key": "read-status"},
    )
    assert response.status_code == 403
    assert admin_db.calls == []


def test_route_has_no_read_only_contract_hidden_mutations():
    from api.routes.runtime_admin import router

    post_paths = {route.path for route in router.routes if "POST" in route.methods}
    assert post_paths
    get_paths = {route.path for route in router.routes if "GET" in route.methods}
    assert get_paths == {
        "/admin/agent-runtime/status",
        "/admin/agent-runtime/provider-operations",
        "/admin/agent-runtime/recovery",
        "/admin/agent-runtime/cost-side-effects",
    }
    assert all("requeue" not in path for path in get_paths)
    assert UUID(ORG_ID)
