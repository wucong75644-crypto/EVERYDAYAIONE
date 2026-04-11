"""测试 /api/auth/me 端点扩展（V1.0+）

验证返回结构：
- current_org.member 包含职位、部门、数据范围
- current_org.permissions 是扁平化权限码列表
- orgs 列表包含所有所属组织
"""
from __future__ import annotations
import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ════════════════════════════════════════════════════════
# Fake DB（参考 test_org_routes.py 的模式）
# ════════════════════════════════════════════════════════

class FakeQueryBuilder:
    def __init__(self, data=None):
        self._data = data if isinstance(data, list) else ([data] if data else [])
        self._is_single = False
        self._limit = None
        self._filters = {}

    def select(self, *a, **kw): return self
    def eq(self, field, value):
        self._filters[field] = value
        return self
    def in_(self, *a): return self
    def limit(self, n):
        self._limit = n
        return self
    def single(self):
        self._is_single = True
        return self

    def execute(self):
        r = MagicMock()
        data = self._data
        if self._is_single:
            r.data = data[0] if data else None
        else:
            r.data = data[: self._limit] if self._limit else data
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


def _build_app(db, current_user, org_id_from_header=None):
    """构建测试 app，mock 认证依赖

    Args:
        org_id_from_header: 模拟 X-Org-Id header 解析后的 org_id，
                            None = 散客（无 header）
    """
    from api.routes.auth import router
    from api.deps import get_current_user, get_org_context, OrgContext
    from core.database import get_db

    app = FastAPI()
    app.include_router(router, prefix="/api")

    app.dependency_overrides[get_current_user] = lambda: current_user
    app.dependency_overrides[get_db] = lambda: db
    # mock OrgCtx：默认散客；测试可通过 org_id_from_header 模拟带 X-Org-Id
    app.dependency_overrides[get_org_context] = lambda: OrgContext(
        user_id=current_user["id"],
        org_id=org_id_from_header,
        org_role="owner" if org_id_from_header else None,
    )

    return app


# ════════════════════════════════════════════════════════
# Tests
# ════════════════════════════════════════════════════════

class TestAuthMeEndpoint:

    def _make_user(self, **overrides):
        base = {
            "id": "user_zhangsan",
            "nickname": "张三",
            "avatar_url": None,
            "phone": "13800138000",
            "role": "user",
            "credits": 100,
            "created_at": "2026-04-01T00:00:00Z",
            "current_org_id": "org_lanchuang",
        }
        base.update(overrides)
        return base

    def test_me_basic_fields(self):
        """基础字段返回正常（无 current_org_id 时）"""
        user = self._make_user(current_org_id=None)
        db = FakeDB()
        app = _build_app(db, user)

        client = TestClient(app)
        resp = client.get("/api/auth/me")
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == "user_zhangsan"
        assert body["nickname"] == "张三"
        assert body["phone"] == "138****8000"  # 脱敏
        assert body["current_org"] is None
        assert body["orgs"] == []

    def test_me_with_org_member_context(self):
        """带 org 时返回完整 member + permissions"""
        user = self._make_user()
        db = FakeDB()

        # organizations 表
        db.add("organizations", {"id": "org_lanchuang", "name": "蓝创科技"})
        # org_members.role
        db.add("org_members", [{"role": "member"}])
        # 用户所属组织列表
        db.add("org_members", [{"org_id": "org_lanchuang", "role": "member"}])
        db.add("organizations", [{"id": "org_lanchuang", "name": "蓝创科技"}])

        # mock get_member_context 和 compute_user_permissions
        with patch("api.routes.auth.get_member_context", new=AsyncMock(return_value={
            "position_code": "manager",
            "department_id": "dept_ops_1",
            "department_name": "运营一部",
            "department_type": "ops",
            "job_title": None,
            "data_scope": "dept_subtree",
            "managed_departments": None,
        })), patch("api.routes.auth.compute_user_permissions", new=AsyncMock(return_value=[
            "task.view", "task.create", "task.edit", "task.delete", "task.execute",
            "order.view", "order.edit", "order.export",
        ])):
            app = _build_app(db, user)
            client = TestClient(app)
            resp = client.get("/api/auth/me")

        assert resp.status_code == 200
        body = resp.json()

        assert body["current_org"]["id"] == "org_lanchuang"
        assert body["current_org"]["name"] == "蓝创科技"
        assert body["current_org"]["role"] == "member"

        member = body["current_org"]["member"]
        assert member["position_code"] == "manager"
        assert member["department_type"] == "ops"
        assert member["data_scope"] == "dept_subtree"

        perms = body["current_org"]["permissions"]
        assert "task.view" in perms
        assert "order.export" in perms
        assert "sys.erp.config" not in perms

    def test_me_boss_has_all_permissions(self):
        """老板返回全部权限"""
        from services.permissions.permission_points import PERMISSIONS
        user = self._make_user()
        db = FakeDB()
        db.add("organizations", {"id": "org_lanchuang", "name": "蓝创科技"})
        db.add("org_members", [{"role": "owner"}])
        db.add("org_members", [{"org_id": "org_lanchuang", "role": "owner"}])
        db.add("organizations", [{"id": "org_lanchuang", "name": "蓝创科技"}])

        with patch("api.routes.auth.get_member_context", new=AsyncMock(return_value={
            "position_code": "boss",
            "department_id": None,
            "department_name": None,
            "department_type": None,
            "job_title": None,
            "data_scope": "all",
            "managed_departments": None,
        })), patch("api.routes.auth.compute_user_permissions", new=AsyncMock(
            return_value=sorted(PERMISSIONS.keys())
        )):
            app = _build_app(db, user)
            client = TestClient(app)
            resp = client.get("/api/auth/me")

        body = resp.json()
        perms = body["current_org"]["permissions"]
        # 老板应该有 sys.* 权限
        assert "sys.erp.config" in perms
        assert "sys.member.add" in perms
        assert len(perms) == len(PERMISSIONS)

    def test_me_member_no_dept_no_perms(self):
        """没分配部门的员工 → 空权限码"""
        user = self._make_user()
        db = FakeDB()
        db.add("organizations", {"id": "org_lanchuang", "name": "蓝创科技"})
        db.add("org_members", [{"role": "member"}])
        db.add("org_members", [{"org_id": "org_lanchuang", "role": "member"}])
        db.add("organizations", [{"id": "org_lanchuang", "name": "蓝创科技"}])

        with patch("api.routes.auth.get_member_context", new=AsyncMock(return_value={
            "position_code": "member",
            "department_id": None,
            "department_name": None,
            "department_type": None,
            "job_title": None,
            "data_scope": "self",
            "managed_departments": None,
        })), patch("api.routes.auth.compute_user_permissions", new=AsyncMock(return_value=[])):
            app = _build_app(db, user)
            client = TestClient(app)
            resp = client.get("/api/auth/me")

        body = resp.json()
        assert body["current_org"]["permissions"] == []
        assert body["current_org"]["member"]["data_scope"] == "self"

    def test_me_uses_x_org_id_header_over_token(self):
        """X-Org-Id header 的 org_id 优先于 token 里的 current_org_id

        修复 bug: 之前 /api/auth/me 只读 token.current_org_id,
        但前端切换企业时只更新 X-Org-Id header,token 不变。
        导致 current_org 永远是 null,permissions 数组拿不到。
        """
        # token 里 current_org_id=None（模拟老 token 没这个字段）
        user = self._make_user(current_org_id=None)
        db = FakeDB()
        db.add("organizations", {"id": "org_lanchuang", "name": "蓝创"})
        db.add("org_members", [{"role": "owner"}])
        db.add("org_members", [{"org_id": "org_lanchuang", "role": "owner"}])
        db.add("organizations", [{"id": "org_lanchuang", "name": "蓝创"}])

        with patch("api.routes.auth.get_member_context", new=AsyncMock(return_value={
            "position_code": "boss",
            "department_id": None,
            "department_name": None,
            "department_type": None,
            "job_title": None,
            "data_scope": "all",
            "managed_departments": None,
        })), patch("api.routes.auth.compute_user_permissions", new=AsyncMock(return_value=[
            "task.view", "sys.member.edit", "sys.member.add",
        ])):
            # 关键：mock OrgCtx 返回有效的 org_id（来自 X-Org-Id header）
            app = _build_app(db, user, org_id_from_header="org_lanchuang")
            client = TestClient(app)
            resp = client.get("/api/auth/me")

        assert resp.status_code == 200
        body = resp.json()
        # 应该正确返回 current_org（即使 token 里 current_org_id 为 None）
        assert body["current_org"] is not None
        assert body["current_org"]["id"] == "org_lanchuang"
        assert "sys.member.edit" in body["current_org"]["permissions"]
