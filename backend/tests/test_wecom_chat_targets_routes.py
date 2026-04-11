"""企微聊天目标管理路由测试

覆盖：
- GET /wecom-chat-targets/groups（管理员/非管理员/空）
- PATCH /{id}/name（成功/不存在/空名/非管理员）
"""
from __future__ import annotations
import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from unittest.mock import MagicMock
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ════════════════════════════════════════════════════════
# Fake DB（与 test_org_members_assignments 同款）
# ════════════════════════════════════════════════════════

class FakeQueryBuilder:
    def __init__(self, data=None):
        self._data = data if isinstance(data, list) else ([data] if data else [])
        self._is_single = False
        self._limit = None
        self._is_delete = False
        self._is_update = False

    def select(self, *a, **kw): return self
    def insert(self, data, **kw): return self
    def update(self, data, **kw):
        self._is_update = True
        return self
    def delete(self):
        self._is_delete = True
        return self
    def eq(self, *a): return self
    def in_(self, *a): return self
    def order(self, *a, **kw): return self
    def limit(self, n):
        self._limit = n
        return self
    def single(self):
        self._is_single = True
        return self

    def execute(self):
        r = MagicMock()
        if self._is_single:
            r.data = self._data[0] if self._data else None
        elif self._is_delete or self._is_update:
            r.data = []
        else:
            r.data = self._data[: self._limit] if self._limit else self._data
        return r


class FakeDB:
    def __init__(self):
        self._tables: dict = {}

    def add(self, name, data):
        self._tables.setdefault(name, []).append(FakeQueryBuilder(data))

    def table(self, name):
        items = self._tables.get(name, [])
        if items:
            return items.pop(0)
        return FakeQueryBuilder([])


def _build_app(db, user_id="user_owner", org_id="org_1"):
    from api.routes.wecom_chat_targets import router
    from api.deps import get_current_user_id, get_org_context, get_scoped_db, OrgContext
    from core.database import get_db

    app = FastAPI()
    app.include_router(router, prefix="/api")

    app.dependency_overrides[get_current_user_id] = lambda: user_id
    app.dependency_overrides[get_org_context] = lambda: OrgContext(
        user_id=user_id, org_id=org_id, org_role="owner"
    )
    app.dependency_overrides[get_scoped_db] = lambda: db
    app.dependency_overrides[get_db] = lambda: db

    return app


# ════════════════════════════════════════════════════════
# 1. GET /groups
# ════════════════════════════════════════════════════════

class TestListGroups:
    def test_list_returns_groups(self):
        db = FakeDB()
        # _require_admin
        db.add("org_members", [{"role": "owner"}])
        # 主查询
        db.add("wecom_chat_targets", [
            {
                "id": "g1",
                "chatid": "wriNwWOAAATq-Xq5_grMtJe8rP7SmR7A",
                "chat_type": "group",
                "chat_name": "运营群",
                "last_active": "2026-04-11T20:00:00Z",
                "first_seen": "2026-04-01T10:00:00Z",
                "message_count": 42,
                "is_active": True,
            },
            {
                "id": "g2",
                "chatid": "wriXyzAAAAA",
                "chat_type": "group",
                "chat_name": None,  # 还没标注
                "last_active": "2026-04-10T18:00:00Z",
                "first_seen": "2026-04-05T10:00:00Z",
                "message_count": 5,
                "is_active": True,
            },
        ])

        app = _build_app(db)
        client = TestClient(app)
        resp = client.get("/api/wecom-chat-targets/groups")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["total"] == 2
        assert body["data"][0]["chat_name"] == "运营群"
        assert body["data"][1]["chat_name"] is None

    def test_empty_groups(self):
        db = FakeDB()
        db.add("org_members", [{"role": "owner"}])
        db.add("wecom_chat_targets", [])

        app = _build_app(db)
        client = TestClient(app)
        resp = client.get("/api/wecom-chat-targets/groups")

        assert resp.status_code == 200
        assert resp.json()["data"] == []

    def test_only_admin_can(self):
        db = FakeDB()
        db.add("org_members", [{"role": "member"}])

        app = _build_app(db, user_id="not_admin")
        client = TestClient(app)
        resp = client.get("/api/wecom-chat-targets/groups")
        assert resp.status_code == 403


# ════════════════════════════════════════════════════════
# 2. PATCH /{id}/name
# ════════════════════════════════════════════════════════

class TestUpdateChatName:
    def test_update_success(self):
        db = FakeDB()
        # _require_admin
        db.add("org_members", [{"role": "owner"}])
        # 校验目标存在
        db.add("wecom_chat_targets", [{"id": "g1", "chat_type": "group"}])

        app = _build_app(db)
        client = TestClient(app)
        resp = client.patch(
            "/api/wecom-chat-targets/g1/name",
            json={"chat_name": "运营群"},
        )

        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_target_not_found(self):
        db = FakeDB()
        db.add("org_members", [{"role": "owner"}])
        db.add("wecom_chat_targets", [])  # 不存在

        app = _build_app(db)
        client = TestClient(app)
        resp = client.patch(
            "/api/wecom-chat-targets/g_unknown/name",
            json={"chat_name": "x"},
        )
        assert resp.status_code == 404

    def test_only_admin_can(self):
        db = FakeDB()
        db.add("org_members", [{"role": "member"}])

        app = _build_app(db, user_id="not_admin")
        client = TestClient(app)
        resp = client.patch(
            "/api/wecom-chat-targets/g1/name",
            json={"chat_name": "运营群"},
        )
        assert resp.status_code == 403

    def test_empty_name_rejected(self):
        db = FakeDB()
        db.add("org_members", [{"role": "owner"}])

        app = _build_app(db)
        client = TestClient(app)
        resp = client.patch(
            "/api/wecom-chat-targets/g1/name",
            json={"chat_name": ""},
        )
        # Pydantic min_length=1 → 422
        assert resp.status_code == 422
