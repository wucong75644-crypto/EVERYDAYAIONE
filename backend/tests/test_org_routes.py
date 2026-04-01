"""
企业管理路由层测试

覆盖:
- create_org: 超管校验、owner 状态检查、正常创建
- accept_invitation: 路由层错误映射
- get_org / list / members: 权限透传
"""

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pytest
from unittest.mock import MagicMock, patch, AsyncMock

from fastapi import HTTPException
from fastapi.testclient import TestClient


# ── Fixtures ────────────────────────────────────────────────


class FakeQueryBuilder:
    def __init__(self, data=None):
        self._data = [data] if isinstance(data, dict) else (data or [])
        self._is_single = False

    def select(self, *a, **kw): return self
    def eq(self, *a): return self
    def single(self):
        self._is_single = True
        return self
    def maybe_single(self):
        self._is_single = True
        return self

    def execute(self):
        r = MagicMock()
        if self._is_single:
            r.data = self._data[0] if self._data else None
        else:
            r.data = self._data
        return r


class FakeDB:
    def __init__(self):
        self._tables: dict[str, list[FakeQueryBuilder]] = {}

    def add(self, name: str, data=None):
        if name not in self._tables:
            self._tables[name] = []
        self._tables[name].append(FakeQueryBuilder(data))

    def table(self, name: str):
        builders = self._tables.get(name, [])
        if builders:
            return builders.pop(0)
        return FakeQueryBuilder()


def _build_app(db, org_service=None):
    """构建带 mock 依赖的 FastAPI app"""
    from fastapi import FastAPI
    from api.routes.org import router
    from api.deps import get_current_user_id, get_db
    from services.org.org_service import OrgService

    app = FastAPI()
    app.include_router(router, prefix="/api")

    # override dependencies
    app.dependency_overrides[get_current_user_id] = lambda: "user-1"
    app.dependency_overrides[get_db] = lambda: db

    return app


# ── create_org 测试 ─────────────────────────────────────────


class TestCreateOrg:

    def test_non_super_admin_rejected(self):
        """非超管创建企业返回 403"""
        db = FakeDB()
        db.add("users", {"role": "member"})  # 非超管

        app = _build_app(db)
        client = TestClient(app)

        resp = client.post("/api/org", json={
            "name": "测试企业",
            "owner_phone": "13800138000"
        })
        assert resp.status_code == 403
        assert "超级管理员" in resp.json()["detail"]

    def test_owner_phone_not_found(self):
        """owner 手机号未注册返回 404"""
        db = FakeDB()
        db.add("users", {"role": "super_admin"})  # 超管
        db.add("users", data=[])  # 手机号查询无结果

        app = _build_app(db)
        client = TestClient(app)

        resp = client.post("/api/org", json={
            "name": "测试企业",
            "owner_phone": "13800138000"
        })
        assert resp.status_code == 404
        assert "未注册" in resp.json()["detail"]

    def test_owner_disabled_rejected(self):
        """owner 已禁用返回 400"""
        db = FakeDB()
        db.add("users", {"role": "super_admin"})
        db.add("users", [{"id": "owner-1", "status": "disabled"}])

        app = _build_app(db)
        client = TestClient(app)

        resp = client.post("/api/org", json={
            "name": "测试企业",
            "owner_phone": "13800138000"
        })
        assert resp.status_code == 400
        assert "禁用" in resp.json()["detail"]

    def test_create_success(self):
        """超管创建企业成功"""
        db = FakeDB()
        db.add("users", {"role": "super_admin"})
        db.add("users", [{"id": "owner-1", "status": "active"}])

        with patch("api.routes.org.OrgService") as MockSvc:
            mock_svc = MagicMock()
            mock_svc.create_organization.return_value = {
                "id": "org-1", "name": "测试企业"
            }
            MockSvc.return_value = mock_svc

            app = _build_app(db)
            # 手动覆盖 _get_org_service
            from api.routes.org import _get_org_service
            app.dependency_overrides[_get_org_service] = lambda: mock_svc

            client = TestClient(app)
            resp = client.post("/api/org", json={
                "name": "测试企业",
                "owner_phone": "13800138000"
            })
            assert resp.status_code == 200
            assert resp.json()["success"] is True
            mock_svc.create_organization.assert_called_once_with("测试企业", "owner-1")

    def test_invalid_phone_format(self):
        """手机号格式不合法返回 422"""
        db = FakeDB()
        app = _build_app(db)
        client = TestClient(app)

        resp = client.post("/api/org", json={
            "name": "测试企业",
            "owner_phone": "123"  # 不符合正则
        })
        assert resp.status_code == 422


# ── get_org 测试 ────────────────────────────────────────────


class TestGetOrg:

    def test_get_org_success(self):
        """正常获取企业信息"""
        db = FakeDB()

        mock_svc = MagicMock()
        mock_svc.require_role.return_value = "member"
        mock_svc.get_organization.return_value = {
            "id": "org-1", "name": "测试", "status": "active"
        }

        app = _build_app(db)
        from api.routes.org import _get_org_service
        app.dependency_overrides[_get_org_service] = lambda: mock_svc

        client = TestClient(app)
        resp = client.get("/api/org/org-1")
        assert resp.status_code == 200
        assert resp.json()["name"] == "测试"

    def test_get_org_not_member(self):
        """非成员获取企业返回 403"""
        from core.exceptions import PermissionDeniedError

        db = FakeDB()
        mock_svc = MagicMock()
        mock_svc.require_role.side_effect = PermissionDeniedError("您不是该企业成员")

        app = _build_app(db)
        from api.routes.org import _get_org_service
        app.dependency_overrides[_get_org_service] = lambda: mock_svc

        client = TestClient(app)
        resp = client.get("/api/org/org-1")
        assert resp.status_code == 403


# ── accept_invitation 测试 ──────────────────────────────────


class TestAcceptInvitationRoute:

    def test_accept_success(self):
        """接受邀请成功"""
        db = FakeDB()
        mock_svc = MagicMock()
        mock_svc.accept_invitation.return_value = {
            "org_id": "org-1", "role": "member", "org_name": "测试"
        }

        app = _build_app(db)
        from api.routes.org import _get_org_service
        app.dependency_overrides[_get_org_service] = lambda: mock_svc

        client = TestClient(app)
        resp = client.post("/api/org/invitations/accept", json={
            "invite_token": "valid-token"
        })
        assert resp.status_code == 200
        assert resp.json()["data"]["org_id"] == "org-1"

    def test_accept_expired(self):
        """过期邀请返回对应错误"""
        from core.exceptions import ValidationError

        db = FakeDB()
        mock_svc = MagicMock()
        mock_svc.accept_invitation.side_effect = ValidationError("邀请已过期")

        app = _build_app(db)
        from api.routes.org import _get_org_service
        app.dependency_overrides[_get_org_service] = lambda: mock_svc

        client = TestClient(app)
        resp = client.post("/api/org/invitations/accept", json={
            "invite_token": "expired-token"
        })
        assert resp.status_code == 400


# ── list_my_orgs 测试 ───────────────────────────────────────


class TestListMyOrgs:

    def test_list_orgs(self):
        """列出用户企业列表"""
        db = FakeDB()
        mock_svc = MagicMock()
        mock_svc.list_user_organizations.return_value = [
            {"org_id": "org-1", "name": "企业A", "role": "member"}
        ]

        app = _build_app(db)
        from api.routes.org import _get_org_service
        app.dependency_overrides[_get_org_service] = lambda: mock_svc

        client = TestClient(app)
        resp = client.get("/api/org")
        assert resp.status_code == 200
        assert len(resp.json()) == 1


# ── 公开接口：get_org_name_public ─────────────────────────


class TestGetOrgNamePublic:
    """GET /org/public/{org_id}/name — 无需认证"""

    def _build_app(self, db):
        from fastapi import FastAPI
        from api.routes.org import router
        from api.deps import get_db

        app = FastAPI()
        app.include_router(router, prefix="/api")
        app.dependency_overrides[get_db] = lambda: db
        return app

    def test_returns_org_name(self):
        """正常返回企业名称"""
        db = FakeDB()
        db.add("organizations", {"name": "蓝创科技", "status": "active"})
        app = self._build_app(db)

        client = TestClient(app)
        resp = client.get("/api/org/public/org-123/name")
        assert resp.status_code == 200
        assert resp.json()["name"] == "蓝创科技"

    def test_returns_404_when_not_found(self):
        """企业不存在返回 404"""
        db = FakeDB()
        db.add("organizations", None)
        app = self._build_app(db)

        client = TestClient(app)
        resp = client.get("/api/org/public/nonexistent/name")
        assert resp.status_code == 404

    def test_returns_400_when_inactive(self):
        """企业停用返回 400"""
        db = FakeDB()
        db.add("organizations", {"name": "已停用企业", "status": "inactive"})
        app = self._build_app(db)

        client = TestClient(app)
        resp = client.get("/api/org/public/org-456/name")
        assert resp.status_code == 400
        assert "停用" in resp.json()["detail"]
