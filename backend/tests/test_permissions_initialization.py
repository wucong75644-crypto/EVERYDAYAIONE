"""权限模块初始化和迁移测试

覆盖：
- initialize_organization 创建职位/角色/部门/默认角色映射
- 幂等性（重复调用不报错）
- _ensure_owner_assignment（已存在时更新）
- migrate_existing_organizations（迁移现有 org_members）
"""
from __future__ import annotations
import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pytest
from unittest.mock import MagicMock
from collections import defaultdict
from typing import Any, Dict, List

from services.permissions.initialization import (
    initialize_organization,
    migrate_existing_organizations,
    SYSTEM_POSITIONS,
    SYSTEM_ROLES,
    DEFAULT_DEPARTMENTS,
    DEPT_TYPE_TO_ROLE_CODE,
)


# ════════════════════════════════════════════════════════
# Fake DB（用 Python dict 模拟表存储）
# ════════════════════════════════════════════════════════

class FakeTable:
    def __init__(self, store: List[Dict], filters: Dict | None = None):
        self.store = store
        self.filters = filters or {}
        self._select_fields = "*"
        self._limit = None
        self._update_data: Dict | None = None
        self._delete = False

    def select(self, fields="*", **kwargs):
        new = FakeTable(self.store, dict(self.filters))
        new._select_fields = fields
        new._update_data = self._update_data
        new._delete = self._delete
        new._limit = self._limit
        return new

    def insert(self, data):
        if isinstance(data, dict):
            self.store.append(dict(data))
        else:
            for d in data:
                self.store.append(dict(d))
        return self

    def update(self, data):
        new = FakeTable(self.store, dict(self.filters))
        new._update_data = dict(data)
        return new

    def delete(self):
        new = FakeTable(self.store, dict(self.filters))
        new._delete = True
        return new

    def eq(self, field, value):
        new = FakeTable(self.store, dict(self.filters))
        new.filters[field] = value
        new._update_data = self._update_data
        new._delete = self._delete
        new._limit = self._limit
        return new

    def in_(self, field, values):
        new = FakeTable(self.store, dict(self.filters))
        new.filters[f"{field}__in"] = list(values)
        new._update_data = self._update_data
        new._delete = self._delete
        new._limit = self._limit
        return new

    def limit(self, n):
        new = FakeTable(self.store, dict(self.filters))
        new._limit = n
        new._update_data = self._update_data
        new._delete = self._delete
        return new

    def _matches(self, row):
        for k, v in self.filters.items():
            if k.endswith("__in"):
                base = k[:-4]
                if row.get(base) not in v:
                    return False
            else:
                if row.get(k) != v:
                    return False
        return True

    def execute(self):
        # 处理 update
        if self._update_data is not None:
            updated = []
            for row in self.store:
                if self._matches(row):
                    row.update(self._update_data)
                    updated.append(row)
            r = MagicMock()
            r.data = updated
            return r

        # 处理 delete
        if self._delete:
            kept = []
            removed = []
            for row in self.store:
                if self._matches(row):
                    removed.append(row)
                else:
                    kept.append(row)
            self.store[:] = kept
            r = MagicMock()
            r.data = removed
            return r

        # 处理 select
        result = [row for row in self.store if self._matches(row)]
        if self._limit:
            result = result[: self._limit]

        r = MagicMock()
        r.data = result
        return r


class FakeDB:
    def __init__(self):
        self._tables: Dict[str, List[Dict]] = defaultdict(list)

    def table(self, name: str) -> FakeTable:
        return FakeTable(self._tables[name])

    def get(self, name: str) -> List[Dict]:
        return self._tables[name]

    def add(self, name: str, row: Dict):
        self._tables[name].append(dict(row))


# ════════════════════════════════════════════════════════
# 测试 initialize_organization
# ════════════════════════════════════════════════════════

class TestInitializeOrganization:

    @pytest.mark.asyncio
    async def test_creates_5_positions(self):
        db = FakeDB()
        await initialize_organization(db, "org_1", "user_owner")
        positions = db.get("org_positions")
        assert len(positions) == 5
        codes = {p["code"] for p in positions}
        assert codes == {"boss", "vp", "manager", "deputy", "member"}

    @pytest.mark.asyncio
    async def test_creates_8_system_roles(self):
        db = FakeDB()
        await initialize_organization(db, "org_1", "user_owner")
        roles = db.get("org_roles")
        # 6 业务 + 老板全权 + 副总全权 = 8
        assert len(roles) == len(SYSTEM_ROLES)
        codes = {r["code"] for r in roles}
        assert "role_ops" in codes
        assert "role_boss_full" in codes

    @pytest.mark.asyncio
    async def test_creates_6_default_departments(self):
        db = FakeDB()
        await initialize_organization(db, "org_1", "user_owner")
        depts = db.get("org_departments")
        assert len(depts) == 6
        types = {d["type"] for d in depts}
        assert types == {"ops", "finance", "warehouse", "service", "design", "hr"}

    @pytest.mark.asyncio
    async def test_dept_path_uses_ltree_format(self):
        db = FakeDB()
        await initialize_organization(db, "org_1", "user_owner")
        for dept in db.get("org_departments"):
            assert dept["path"].startswith("root.")
            assert dept["type"] in dept["path"]

    @pytest.mark.asyncio
    async def test_role_permissions_populated(self):
        db = FakeDB()
        await initialize_organization(db, "org_1", "user_owner")

        # 找运营角色
        ops_role = next(r for r in db.get("org_roles") if r["code"] == "role_ops")
        ops_perms = [
            rp for rp in db.get("role_permissions")
            if rp["role_id"] == ops_role["id"]
        ]
        # 运营角色应该有任务和订单权限
        codes = {rp["permission_code"] for rp in ops_perms}
        assert "task.view" in codes
        assert "order.view" in codes

    @pytest.mark.asyncio
    async def test_position_default_roles_mapping(self):
        db = FakeDB()
        await initialize_organization(db, "org_1", "user_owner")

        mappings = db.get("position_default_roles")
        # 6 部门类型 × 3 职位（member/deputy/manager）= 18
        # + manager 每个部门类型加成包 = 0（manager_addon 已删）
        # + boss = 1, vp = 1
        # = 18 + 2 = 20

        # 至少检查每个职位都有对应映射
        position_codes = {m["position_code"] for m in mappings}
        assert "boss" in position_codes
        assert "vp" in position_codes
        assert "manager" in position_codes
        assert "member" in position_codes

        # 老板和副总用 'all' 作为 dept_type
        boss_mapping = next(
            m for m in mappings if m["position_code"] == "boss"
        )
        assert boss_mapping["department_type"] == "all"

    @pytest.mark.asyncio
    async def test_owner_assigned_as_boss(self):
        db = FakeDB()
        await initialize_organization(db, "org_1", "user_owner")

        assignments = db.get("org_member_assignments")
        assert len(assignments) == 1
        a = assignments[0]
        assert a["user_id"] == "user_owner"
        assert a["data_scope"] == "all"
        assert a["department_id"] is None  # 老板不属于具体部门

        # position 应该是 boss
        boss_pos = next(
            p for p in db.get("org_positions") if p["code"] == "boss"
        )
        assert a["position_id"] == boss_pos["id"]

    @pytest.mark.asyncio
    async def test_idempotent(self):
        """重复调用不会报错，也不会重复创建"""
        db = FakeDB()
        await initialize_organization(db, "org_1", "user_owner")
        await initialize_organization(db, "org_1", "user_owner")

        assert len(db.get("org_positions")) == 5
        assert len(db.get("org_departments")) == 6
        assert len(db.get("org_member_assignments")) == 1

    @pytest.mark.asyncio
    async def test_existing_assignment_updated_to_boss(self):
        """已有 assignment 时，重新初始化会把 owner 升级为 boss"""
        db = FakeDB()
        # 先创建一个 member 任职
        # 这里直接初始化两次，第一次创建的就是 owner，但模拟"先 member 后 owner 升级"
        # 简化版：先调用一次，verify owner 是 boss
        await initialize_organization(db, "org_1", "user_owner")

        # 模拟 assignment 被改成了 member
        assignments = db.get("org_member_assignments")
        member_pos = next(p for p in db.get("org_positions") if p["code"] == "member")
        assignments[0]["position_id"] = member_pos["id"]
        assignments[0]["data_scope"] = "self"

        # 再次初始化：owner 应该被升回 boss
        await initialize_organization(db, "org_1", "user_owner")

        boss_pos = next(p for p in db.get("org_positions") if p["code"] == "boss")
        assert assignments[0]["position_id"] == boss_pos["id"]
        assert assignments[0]["data_scope"] == "all"


# ════════════════════════════════════════════════════════
# 测试 migrate_existing_organizations
# ════════════════════════════════════════════════════════

class TestMigrateExistingOrganizations:

    @pytest.mark.asyncio
    async def test_migrate_creates_assignments_for_all_members(self):
        """现有 org_members 全部迁移到 org_member_assignments"""
        db = FakeDB()
        # 准备：1 个组织，3 个成员（1 owner + 1 admin + 1 member）
        db.add("organizations", {"id": "org_1", "owner_id": "user_owner"})
        db.add("org_members", {
            "org_id": "org_1", "user_id": "user_owner",
            "role": "owner", "status": "active",
        })
        db.add("org_members", {
            "org_id": "org_1", "user_id": "user_admin",
            "role": "admin", "status": "active",
        })
        db.add("org_members", {
            "org_id": "org_1", "user_id": "user_zhangsan",
            "role": "member", "status": "active",
        })

        stats = await migrate_existing_organizations(db)

        assert stats["orgs_initialized"] == 1
        # owner 在 initialize 时已经创建 assignment，再看 admin/member 是否被加进来
        assignments = db.get("org_member_assignments")
        user_ids = {a["user_id"] for a in assignments}
        assert "user_owner" in user_ids
        assert "user_admin" in user_ids
        assert "user_zhangsan" in user_ids

    @pytest.mark.asyncio
    async def test_admin_and_member_become_member_position(self):
        """admin/member 角色都迁移到 position=member（部门待分配）"""
        db = FakeDB()
        db.add("organizations", {"id": "org_1", "owner_id": "user_owner"})
        db.add("org_members", {
            "org_id": "org_1", "user_id": "user_owner",
            "role": "owner", "status": "active",
        })
        db.add("org_members", {
            "org_id": "org_1", "user_id": "user_admin",
            "role": "admin", "status": "active",
        })

        await migrate_existing_organizations(db)

        # admin 应该是 member position + scope=self + 没分配部门
        assignments = db.get("org_member_assignments")
        admin_a = next(a for a in assignments if a["user_id"] == "user_admin")

        member_pos = next(p for p in db.get("org_positions") if p["code"] == "member")
        assert admin_a["position_id"] == member_pos["id"]
        assert admin_a["data_scope"] == "self"
        assert admin_a["department_id"] is None

    @pytest.mark.asyncio
    async def test_skips_disabled_members(self):
        """已停用的成员不迁移"""
        db = FakeDB()
        db.add("organizations", {"id": "org_1", "owner_id": "user_owner"})
        db.add("org_members", {
            "org_id": "org_1", "user_id": "user_owner",
            "role": "owner", "status": "active",
        })
        db.add("org_members", {
            "org_id": "org_1", "user_id": "user_left",
            "role": "member", "status": "disabled",
        })

        await migrate_existing_organizations(db)

        assignments = db.get("org_member_assignments")
        user_ids = {a["user_id"] for a in assignments}
        assert "user_left" not in user_ids
