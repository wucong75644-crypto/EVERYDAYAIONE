"""组织成员任职管理路由测试"""
from __future__ import annotations
import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pytest
from unittest.mock import MagicMock
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ════════════════════════════════════════════════════════
# Fake DB（继承定时任务路由测试的模式）
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
    from api.routes.org_members_assignments import router
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
# 1. 列表查询
# ════════════════════════════════════════════════════════

class TestListMembers:

    def test_list_returns_members_with_assignments(self):
        db = FakeDB()
        # 1. _require_admin 查询
        db.add("org_members", [{"role": "owner"}])
        # 2. list_members_with_assignments 主查询
        db.add("org_members", [
            {"user_id": "user_owner", "role": "owner", "status": "active"},
            {"user_id": "user_zhangsan", "role": "member", "status": "active"},
        ])
        # 3. users
        db.add("users", [
            {"id": "user_owner", "nickname": "王老板", "avatar_url": None, "phone": "138"},
            {"id": "user_zhangsan", "nickname": "张三", "avatar_url": None, "phone": "139"},
        ])
        # 4. assignments
        db.add("org_member_assignments", [
            {
                "user_id": "user_owner", "department_id": None,
                "position_id": "pos_boss", "data_scope": "all",
                "data_scope_dept_ids": None, "job_title": None,
            },
            {
                "user_id": "user_zhangsan", "department_id": "dept_ops",
                "position_id": "pos_member", "data_scope": "self",
                "data_scope_dept_ids": None, "job_title": "高级运营",
            },
        ])
        # 5. departments
        db.add("org_departments", [
            {"id": "dept_ops", "name": "运营一部", "type": "ops"},
        ])
        # 6. positions
        db.add("org_positions", [
            {"id": "pos_boss", "code": "boss", "name": "老板"},
            {"id": "pos_member", "code": "member", "name": "员工"},
        ])

        app = _build_app(db)
        client = TestClient(app)
        resp = client.get("/api/org-members/list")

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 2

        # 检查张三的信息
        zhang = next(m for m in body["data"] if m["user_id"] == "user_zhangsan")
        assert zhang["nickname"] == "张三"
        assert zhang["org_role"] == "member"
        assert zhang["assignment"]["department_name"] == "运营一部"
        assert zhang["assignment"]["position_code"] == "member"
        assert zhang["assignment"]["job_title"] == "高级运营"

    def test_list_requires_admin(self):
        """普通成员调用 → 403"""
        db = FakeDB()
        # _require_admin 返回 member
        db.add("org_members", [{"role": "member"}])

        app = _build_app(db, user_id="user_zhangsan")
        client = TestClient(app)
        resp = client.get("/api/org-members/list")
        assert resp.status_code == 403


# ════════════════════════════════════════════════════════
# 2. 部门和职位列表
# ════════════════════════════════════════════════════════

class TestListDepartments:

    def test_list_departments(self):
        db = FakeDB()
        db.add("org_departments", [
            {"id": "d1", "name": "运营一部", "type": "ops", "sort_order": 0},
            {"id": "d2", "name": "财务部", "type": "finance", "sort_order": 1},
        ])

        app = _build_app(db)
        client = TestClient(app)
        resp = client.get("/api/org-members/departments")

        assert resp.status_code == 200
        assert len(resp.json()["data"]) == 2


class TestListPositions:

    def test_list_positions(self):
        db = FakeDB()
        db.add("org_positions", [
            {"id": "p1", "code": "boss", "name": "老板", "level": 1},
            {"id": "p5", "code": "member", "name": "员工", "level": 5},
        ])

        app = _build_app(db)
        client = TestClient(app)
        resp = client.get("/api/org-members/positions")

        assert resp.status_code == 200
        assert len(resp.json()["data"]) == 2


# ════════════════════════════════════════════════════════
# 3. 修改成员任职
# ════════════════════════════════════════════════════════

class TestUpdateAssignment:

    def test_update_existing_assignment(self):
        db = FakeDB()
        # _require_admin
        db.add("org_members", [{"role": "owner"}])
        # 已存在的 assignment
        db.add("org_member_assignments", [
            {
                "id": "a1", "department_id": None,
                "position_id": "pos_member", "data_scope": "self",
            },
        ])
        # 校验部门
        db.add("org_departments", [{"id": "dept_ops"}])
        # 校验职位
        db.add("org_positions", [{"id": "pos_manager"}])

        app = _build_app(db)
        client = TestClient(app)
        resp = client.patch(
            "/api/org-members/user_zhangsan/assignment",
            json={
                "department_id": "dept_ops",
                "position_code": "manager",
                "data_scope": "dept_subtree",
            },
        )

        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_update_invalid_department(self):
        db = FakeDB()
        db.add("org_members", [{"role": "owner"}])
        db.add("org_member_assignments", [{"id": "a1"}])
        # 部门不存在
        db.add("org_departments", [])

        app = _build_app(db)
        client = TestClient(app)
        resp = client.patch(
            "/api/org-members/user_zhangsan/assignment",
            json={"department_id": "fake_dept"},
        )

        assert resp.status_code == 400

    def test_update_invalid_position(self):
        db = FakeDB()
        db.add("org_members", [{"role": "owner"}])
        db.add("org_member_assignments", [{"id": "a1"}])
        db.add("org_positions", [])  # 找不到 position

        app = _build_app(db)
        client = TestClient(app)
        resp = client.patch(
            "/api/org-members/user_zhangsan/assignment",
            json={"position_code": "manager"},
        )
        assert resp.status_code == 400

    def test_update_no_changes(self):
        db = FakeDB()
        db.add("org_members", [{"role": "owner"}])
        db.add("org_member_assignments", [{"id": "a1"}])

        app = _build_app(db)
        client = TestClient(app)
        resp = client.patch(
            "/api/org-members/user_zhangsan/assignment",
            json={},
        )
        assert resp.status_code == 200
        assert "无变更" in resp.json()["message"]

    def test_update_only_admin_can(self):
        db = FakeDB()
        db.add("org_members", [{"role": "member"}])  # 不是 admin

        app = _build_app(db, user_id="user_zhangsan")
        client = TestClient(app)
        resp = client.patch(
            "/api/org-members/user_lisi/assignment",
            json={"position_code": "member"},
        )
        assert resp.status_code == 403
