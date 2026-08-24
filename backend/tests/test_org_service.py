"""
企业管理服务测试

覆盖: 企业 CRUD、成员管理、邀请流程。
"""

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pytest
from unittest.mock import MagicMock, patch

from services.org.org_service import OrgService
from core.exceptions import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)


# ── Fixtures ────────────────────────────────────────────────


class FakeQueryBuilder:
    """模拟 Supabase query builder 链式调用"""

    def __init__(self, data=None, count=None):
        # 统一存为 list
        if isinstance(data, dict):
            self._data = [data]
        else:
            self._data = data if data is not None else []
        self._count = count
        self._is_single = False

    def select(self, *args, **kwargs):
        return self

    def insert(self, data):
        self._data = [{"id": "new-id", **data}]
        return self

    def upsert(self, data, on_conflict=""):
        if isinstance(data, dict):
            self._data = [data]
        else:
            self._data = data
        return self

    def update(self, data):
        if self._data:
            self._data = [{**self._data[0], **data}]
        return self

    def delete(self):
        return self

    def eq(self, col, val):
        return self

    def neq(self, col, val):
        return self

    def single(self):
        self._is_single = True
        return self

    def maybe_single(self):
        self._is_single = True
        return self

    def limit(self, n):
        return self

    def order(self, col):
        return self

    def execute(self):
        result = MagicMock()
        if self._is_single:
            result.data = self._data[0] if self._data else None
        else:
            result.data = self._data
        result.count = self._count
        return result


class FakeDB:
    """模拟 Supabase Client，支持同表多次设置（每次 table() 返回独立 builder）"""

    def __init__(self):
        self._tables: dict[str, list[FakeQueryBuilder]] = {}

    def set_table(self, name: str, data=None, count=None):
        """追加一个 builder（按调用顺序消费）"""
        if name not in self._tables:
            self._tables[name] = []
        self._tables[name].append(FakeQueryBuilder(data, count))

    def table(self, name: str):
        builders = self._tables.get(name, [])
        if builders:
            return builders.pop(0)
        return FakeQueryBuilder()

    def rpc(self, name: str, params=None):
        params = params or {}
        if name == "get_governed_actor_authority":
            org = self.table("organizations").single().execute().data
            if not org or org.get("status") != "active":
                raise Exception("GOVERNANCE_ORG_INACTIVE")
            member = self.table("org_members").single().execute().data
            if not member or member.get("status") != "active":
                raise Exception("GOVERNANCE_AUTHORITY_DENIED")
            return FakeQueryBuilder(member["role"]).single()
        if name == "create_governed_organization":
            existing = self.table("organizations").execute().data
            if existing:
                raise Exception("GOVERNANCE_ORG_NAME_CONFLICT")
            return FakeQueryBuilder({
                "id": "new-id",
                "name": params["p_name"],
                "owner_id": params["p_owner_id"],
            }).single()
        if name == "get_governed_organization":
            return self.table("organizations").single()
        if name == "list_governed_members":
            org = self.table("organizations").single().execute().data
            if org and org.get("status") != "active":
                raise Exception("GOVERNANCE_ORG_INACTIVE")
            member = self.table("org_members").single().execute().data
            if not member or member.get("status") != "active":
                raise Exception("GOVERNANCE_AUTHORITY_DENIED")
            rows = self.table("org_members").execute().data
            result = []
            for row in rows:
                account = self.table("users").single().execute().data or {}
                phone = account.get("phone") or ""
                result.append({
                    **row,
                    "nickname": account.get("nickname"),
                    "phone": (
                        f"{phone[:3]}****{phone[-4:]}"
                        if len(phone) >= 7 else phone
                    ),
                })
            return FakeQueryBuilder(result)
        if name in (
            "add_governed_member",
            "remove_governed_member",
            "change_governed_member_role",
            "create_governed_invitation",
        ):
            org = self.table("organizations").single().execute().data
            if org and org.get("status") != "active":
                raise Exception("GOVERNANCE_ORG_INACTIVE")
            authority = self.table("org_members").single().execute().data or {}
            role = authority.get("role")
            if name == "change_governed_member_role" and role != "owner":
                raise Exception("GOVERNANCE_AUTHORITY_DENIED")
            if role not in ("owner", "admin"):
                raise Exception("GOVERNANCE_AUTHORITY_DENIED")
            if name == "remove_governed_member":
                target = self.table("org_members").single().execute().data or {}
                if target.get("role") == "owner":
                    raise Exception("GOVERNANCE_AUTHORITY_DENIED")
                return FakeQueryBuilder(True).single()
            if name == "create_governed_invitation":
                account = self.table("users").execute().data
                if account:
                    existing = self.table("org_members").execute().data
                    if existing:
                        raise Exception("GOVERNANCE_INVITATION_CONFLICT")
                pending = self.table("org_invitations").execute().data
                if pending:
                    raise Exception("GOVERNANCE_INVITATION_CONFLICT")
                return FakeQueryBuilder({
                    "id": "new-id",
                    "invite_token": params["p_invite_token"],
                    **params,
                }).single()
            return FakeQueryBuilder({
                "user_id": params["p_target_user_id"],
                "role": params.get("p_role", "member"),
            }).single()
        if name == "accept_governed_invitation":
            invitation = self.table("org_invitations").single().execute().data
            if not invitation or invitation.get("status") != "pending":
                raise Exception("GOVERNANCE_INVITATION_MISSING")
            if invitation["expires_at"].startswith("2020"):
                raise Exception("GOVERNANCE_INVITATION_EXPIRED")
            account = self.table("users").single().execute().data
            if not account:
                raise Exception("GOVERNANCE_INVITATION_RECIPIENT_MISMATCH")
            if account.get("phone") != invitation["phone"]:
                raise Exception("GOVERNANCE_INVITATION_RECIPIENT_MISMATCH")
            org = self.table("organizations").single().execute().data
            if not org or org.get("status") != "active":
                raise Exception("GOVERNANCE_ORG_INACTIVE")
            existing = self.table("org_members").execute().data
            if existing:
                raise Exception("GOVERNANCE_MEMBER_EXISTS")
            count = self.table("org_members").execute().count or 0
            if count >= org.get("max_members", 50):
                raise Exception("GOVERNANCE_MEMBER_LIMIT_REACHED")
            return FakeQueryBuilder({
                "org_id": invitation["org_id"],
                "role": invitation["role"],
                "org_name": org["name"],
            }).single()
        if name == "list_actor_organizations":
            members = self.table("org_members").execute().data
            result = []
            for member in members:
                org = self.table("organizations").single().execute().data or {}
                if org.get("status") == "active":
                    result.append({
                        "org_id": org["id"],
                        "name": org["name"],
                        "logo_url": org.get("logo_url"),
                        "role": member["role"],
                        "features": org.get("features", {}),
                    })
            return FakeQueryBuilder(result)
        raise AssertionError(f"unexpected rpc: {name}")


@pytest.fixture
def db():
    return FakeDB()


@pytest.fixture
def svc(db):
    return OrgService(db)


# ── 企业创建 ────────────────────────────────────────────────


class TestCreateOrganization:

    def test_create_success(self, db, svc):
        db.set_table("organizations", data=[])  # 无重名
        org = svc.create_organization("测试企业", "owner-1")
        assert org["name"] == "测试企业"

    def test_create_duplicate_name(self, db, svc):
        db.set_table("organizations", data=[{"id": "existing"}])
        with pytest.raises(ConflictError, match="已存在"):
            svc.create_organization("测试企业", "owner-1")

    def test_create_empty_name(self, db, svc):
        with pytest.raises(ValidationError, match="不能为空"):
            svc.create_organization("", "owner-1")

    def test_create_name_too_long(self, db, svc):
        with pytest.raises(ValidationError, match="不超过100"):
            svc.create_organization("A" * 101, "owner-1")


# ── 企业查询 ────────────────────────────────────────────────


class TestGetOrganization:

    def test_get_success(self, db, svc):
        db.set_table("organizations", data={"id": "org-1", "name": "测试"})
        org = svc.get_organization("org-1")
        assert org["name"] == "测试"

    def test_get_not_found(self, db, svc):
        db.set_table("organizations", data=None)
        with pytest.raises(NotFoundError):
            svc.get_organization("nonexistent")


# ── 成员管理 ────────────────────────────────────────────────


class TestMemberManagement:

    def _setup_require_role(self, db, role="owner"):
        """模拟 _require_role 需要的两次查询：organizations + org_members"""
        db.set_table("organizations", data={"id": "org-1", "status": "active"})
        db.set_table("org_members", data={"role": role, "status": "active"})

    def test_list_members(self, db, svc):
        # _require_role: organizations + org_members
        self._setup_require_role(db, "member")
        # 实际查询成员列表（分步查：先 org_members 再 users）
        db.set_table("org_members", data=[{
            "user_id": "u1", "role": "owner", "status": "active",
            "joined_at": "2026-01-01",
        }])
        # 分步查 users 表
        db.set_table("users", data={"nickname": "张三", "phone": "13800138000"})
        members = svc.list_members("org-1", "u1")
        assert len(members) == 1
        assert members[0]["phone"] == "138****8000"

    def test_add_member_non_admin_rejected(self, db, svc):
        self._setup_require_role(db, "member")
        with pytest.raises(PermissionDeniedError):
            svc.add_member("org-1", "operator", "target", "member")

    def test_remove_self_rejected(self, db, svc):
        """不能移除自己"""
        with pytest.raises(ValidationError, match="自己"):
            svc.remove_member("org-1", "user-1", "user-1")

    def test_remove_owner_rejected(self, db, svc):
        """owner 不能被移除"""
        # _check_org_active
        db.set_table("organizations", data={"id": "org-1", "status": "active"})
        # _get_member_role for operator
        db.set_table("org_members", data={"role": "owner", "status": "active"})
        # _get_member_role for target
        db.set_table("org_members", data={"role": "owner", "status": "active"})
        with pytest.raises(PermissionDeniedError):
            svc.remove_member("org-1", "admin-1", "owner-1")

    def test_change_role_non_owner_rejected(self, db, svc):
        self._setup_require_role(db, "admin")
        with pytest.raises(PermissionDeniedError):
            svc.change_member_role("org-1", "admin-1", "target", "member")

    def test_change_own_role_rejected(self, db, svc):
        self._setup_require_role(db, "owner")
        with pytest.raises(ValidationError, match="自己"):
            svc.change_member_role("org-1", "u1", "u1", "member")

    def test_suspended_org_rejected(self, db, svc):
        """停用企业不能操作"""
        db.set_table("organizations", data={"id": "org-1", "status": "suspended"})
        with pytest.raises(PermissionDeniedError, match="停用"):
            svc.list_members("org-1", "u1")


# ── 邀请 ────────────────────────────────────────────────────


class TestInvitation:

    def _setup_require_role(self, db, role="admin"):
        db.set_table("organizations", data={"id": "org-1", "status": "active"})
        db.set_table("org_members", data={"role": role, "status": "active"})

    def test_create_invitation_success(self, db, svc):
        self._setup_require_role(db, "admin")
        # check user exists
        db.set_table("users", data=[])
        # check pending invitation
        db.set_table("org_invitations", data=[])
        # insert invitation
        db.set_table("org_invitations", data=[])
        inv = svc.create_invitation("org-1", "admin-1", "13800138000", "member")
        assert "invite_token" in inv

    def test_create_invitation_non_admin_rejected(self, db, svc):
        self._setup_require_role(db, "member")
        with pytest.raises(PermissionDeniedError):
            svc.create_invitation("org-1", "user-1", "13800138000")

    def test_accept_invitation_phone_mismatch(self, db, svc):
        """手机号不匹配时拒绝接受"""
        db.set_table("org_invitations", data={
            "id": "inv-1", "org_id": "org-1", "phone": "13800138000",
            "role": "member", "status": "pending", "invited_by": "admin-1",
            "expires_at": "2099-01-01T00:00:00+00:00",
        })
        # 用户手机号与邀请不同
        db.set_table("users", data={"id": "user-1", "phone": "13999999999"})
        with pytest.raises(ValidationError, match="手机号不匹配"):
            svc.accept_invitation("token-1", "user-1")

    def test_accept_invitation_suspended_org_rejected(self, db, svc):
        """已停用企业不能接受邀请"""
        db.set_table("org_invitations", data={
            "id": "inv-1", "org_id": "org-1", "phone": "13800138000",
            "role": "member", "status": "pending", "invited_by": "admin-1",
            "expires_at": "2099-01-01T00:00:00+00:00",
        })
        db.set_table("users", data={"id": "user-1", "phone": "13800138000"})
        db.set_table("organizations", data={"id": "org-1", "status": "suspended"})
        with pytest.raises(PermissionDeniedError, match="停用"):
            svc.accept_invitation("token-1", "user-1")

    def test_accept_invitation_expired(self, db, svc):
        # select invitation
        db.set_table("org_invitations", data={
            "id": "inv-1", "org_id": "org-1", "phone": "138",
            "role": "member", "status": "pending", "invited_by": "admin-1",
            "expires_at": "2020-01-01T00:00:00+00:00",
        })
        # 手机号匹配
        db.set_table("users", data={"id": "user-1", "phone": "138"})
        # update invitation status to expired
        db.set_table("org_invitations", data={"id": "inv-1", "status": "expired"})
        with pytest.raises(ValidationError, match="过期"):
            svc.accept_invitation("token-1", "user-1")


# ── 用户企业列表 ────────────────────────────────────────────


class TestListUserOrgs:

    def test_list_orgs(self, db, svc):
        # 分步查：先 org_members 再 organizations
        db.set_table("org_members", data=[{
            "org_id": "org-1", "role": "member", "status": "active",
        }])
        db.set_table("organizations", data={
            "id": "org-1", "name": "测试企业",
            "logo_url": None, "status": "active", "features": {},
        })
        orgs = svc.list_user_organizations("user-1")
        assert len(orgs) == 1
        assert orgs[0]["name"] == "测试企业"

    def test_list_orgs_excludes_suspended(self, db, svc):
        db.set_table("org_members", data=[{
            "org_id": "org-1", "role": "member", "status": "active",
        }])
        db.set_table("organizations", data={
            "id": "org-1", "name": "已停用", "logo_url": None,
            "status": "suspended", "features": {},
        })
        orgs = svc.list_user_organizations("user-1")
        assert len(orgs) == 0


class TestGovernanceCapabilityRouting:

    @pytest.mark.parametrize(
        ("method_name", "rpc_name", "args"),
        (
            ("list_all_organizations", "list_all_governed_organizations", ()),
            (
                "search_user_by_phone",
                "search_governed_user_by_phone",
                ("13800138000",),
            ),
            (
                "list_pending_invitations",
                "list_actor_pending_invitations",
                (),
            ),
        ),
    )
    def test_control_reads_use_governance_rpc(
        self, method_name, rpc_name, args,
    ):
        database = MagicMock()
        database.rpc.return_value.execute.return_value.data = []
        service = OrgService(database)

        getattr(service, method_name)(*args)

        assert database.rpc.call_args.args[0] == rpc_name

    def test_list_all_organizations_has_no_org_parameter(self):
        database = MagicMock()
        database.rpc.return_value.execute.return_value.data = []

        OrgService(database).list_all_organizations()

        database.rpc.assert_called_once_with(
            "list_all_governed_organizations", None,
        )

    @pytest.mark.parametrize(
        "marker",
        (
            "GOVERNANCE_AUTHORITY_DENIED",
            "GOVERNANCE_PRINCIPAL_INACTIVE",
            "GOVERNANCE_ROLE_SCOPE_MISMATCH",
        ),
    )
    def test_platform_governance_denials_map_to_403(self, marker):
        database = MagicMock()
        database.rpc.return_value.execute.side_effect = Exception(marker)

        with pytest.raises(PermissionDeniedError):
            OrgService(database).list_all_organizations()
