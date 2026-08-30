"""ChangeSet 稳定状态/时间线 API 契约测试。"""

from __future__ import annotations

from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient


class Query:
    def __init__(self, rows):
        self.rows = rows

    def select(self, *args, **kwargs):
        return self

    def eq(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def order(self, *args, **kwargs):
        return self

    def execute(self):
        response = MagicMock()
        response.data = list(self.rows)
        return response


class DB:
    def __init__(self, rows, checks=None, events=None):
        self.rows = rows
        self.checks = checks or []
        self.events = events or []

    def table(self, name):
        return Query({
            "change_sets": self.rows,
            "change_checks": self.checks,
            "change_events": self.events,
        }.get(name, []))


def _app(db):
    from api.deps import get_current_user_id, get_org_context, get_scoped_db, OrgContext
    from api.routes.change_sets import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_current_user_id] = lambda: "user-1"
    app.dependency_overrides[get_org_context] = lambda: OrgContext(
        user_id="user-1", org_id="org-1", org_role="member",
    )
    app.dependency_overrides[get_scoped_db] = lambda: db
    return app


def _row():
    return {
        "id": "cs-1", "org_id": "org-1", "resource_type": "detail_project",
        "resource_id": "project-1", "operation": "update", "base_revision": "3",
        "base_snapshot": {}, "proposed_snapshot": {}, "patch": [], "diff": {},
        "risk_level": "low", "policy_snapshot": {}, "status": "draft",
        "idempotency_key": "idempotency-1", "expires_at": "2030-01-01T00:00:00Z",
        "created_by": "user-1", "created_by_type": "user", "audit_subject": {},
        "revision": 0, "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }


def test_detail_and_timeline_are_stable_projections():
    db = DB([_row()], events=[{
        "id": "event-1", "change_set_id": "cs-1", "org_id": "org-1",
        "sequence": 1, "event_type": "created", "from_status": None,
        "to_status": "draft", "actor_id": "user-1", "actor_type": "user",
        "payload": {"contract_version": "changeset.v1"},
        "created_at": "2026-01-01T00:00:00Z",
    }])
    client = TestClient(_app(db))

    detail = client.get("/api/change-sets/cs-1")
    timeline = client.get("/api/change-sets/cs-1/timeline")

    assert detail.status_code == 200
    assert detail.json()["data"]["status"] == "draft"
    assert timeline.status_code == 200
    assert timeline.json()["data"]["events"][0]["sequence"] == 1
