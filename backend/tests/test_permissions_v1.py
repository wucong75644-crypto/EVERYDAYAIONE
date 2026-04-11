"""权限模块 V1 测试 — Phase 0

测试范围：
- PERMISSIONS 字典完整性
- DEPT_TYPE_TO_PERMS 映射
- ROLE_BOSS_FULL_PERMS / ROLE_VP_FULL_PERMS
- PermissionChecker 5 个职位的判断逻辑（mock assignment）
- compute_user_permissions 扁平权限码计算
"""
from __future__ import annotations
import pytest
from unittest.mock import AsyncMock, MagicMock

from services.permissions.permission_points import (
    PERMISSIONS,
    DEPT_TYPE_TO_PERMS,
    ROLE_BOSS_FULL_PERMS,
    ROLE_VP_FULL_PERMS,
    ROLE_OPS_PERMS,
    ROLE_HR_PERMS,
    is_system_permission,
)
from services.permissions.checker import PermissionChecker
from services.permissions.effective_perms import (
    compute_user_permissions,
    get_member_context,
)


# ════════════════════════════════════════════════════════
# 1. 权限点字典完整性
# ════════════════════════════════════════════════════════

class TestPermissionPoints:
    def test_all_permissions_have_three_fields(self):
        """每个权限点都有 (module, action, name)"""
        for code, value in PERMISSIONS.items():
            assert len(value) == 3, f"{code} 缺少字段"
            module, action, name = value
            assert module and action and name

    def test_task_permissions_exist(self):
        """定时任务的 5 个权限点存在"""
        assert "task.view" in PERMISSIONS
        assert "task.create" in PERMISSIONS
        assert "task.edit" in PERMISSIONS
        assert "task.delete" in PERMISSIONS
        assert "task.execute" in PERMISSIONS

    def test_sys_permissions_identified(self):
        """系统配置权限以 sys. 开头"""
        assert is_system_permission("sys.member.add")
        assert is_system_permission("sys.erp.config")
        assert not is_system_permission("task.view")
        assert not is_system_permission("order.view")

    def test_boss_has_all_permissions(self):
        """老板拥有所有权限点"""
        assert ROLE_BOSS_FULL_PERMS == set(PERMISSIONS.keys())

    def test_vp_excludes_sys_permissions(self):
        """副总不包含 sys.* 权限"""
        for code in ROLE_VP_FULL_PERMS:
            assert not code.startswith("sys."), f"副总不应有 {code}"
        # 但应包含全部业务权限
        assert "task.view" in ROLE_VP_FULL_PERMS
        assert "order.export" in ROLE_VP_FULL_PERMS
        assert "finance.reconcile" in ROLE_VP_FULL_PERMS

    def test_dept_type_role_mapping(self):
        """部门类型映射"""
        assert "ops" in DEPT_TYPE_TO_PERMS
        assert "finance" in DEPT_TYPE_TO_PERMS
        assert "warehouse" in DEPT_TYPE_TO_PERMS
        assert "service" in DEPT_TYPE_TO_PERMS
        assert "design" in DEPT_TYPE_TO_PERMS
        assert "hr" in DEPT_TYPE_TO_PERMS

    def test_ops_role_can_do_orders(self):
        """运营角色可以管订单"""
        assert "order.view" in ROLE_OPS_PERMS
        assert "order.edit" in ROLE_OPS_PERMS
        assert "order.export" in ROLE_OPS_PERMS

    def test_hr_role_can_edit_members(self):
        """人事角色可以编辑成员"""
        assert "sys.member.edit" in ROLE_HR_PERMS


# ════════════════════════════════════════════════════════
# 2. PermissionChecker 职位判断逻辑
# ════════════════════════════════════════════════════════

class TestPermissionChecker:
    """测试 PermissionChecker 的核心判断逻辑"""

    @pytest.fixture
    def boss_assignment(self):
        return {
            "user_id": "user_boss",
            "org_id": "org_1",
            "position_code": "boss",
            "department_id": None,
            "department_type": None,
            "data_scope": "all",
            "data_scope_dept_ids": None,
        }

    @pytest.fixture
    def vp_full_assignment(self):
        return {
            "user_id": "user_vp1",
            "org_id": "org_1",
            "position_code": "vp",
            "department_id": None,
            "department_type": None,
            "data_scope": "all",
            "data_scope_dept_ids": None,
        }

    @pytest.fixture
    def vp_managed_assignment(self):
        return {
            "user_id": "user_vp2",
            "org_id": "org_1",
            "position_code": "vp",
            "department_id": None,
            "department_type": None,
            "data_scope": "dept_subtree",
            "data_scope_dept_ids": ["dept_ops_1", "dept_ops_2"],
        }

    @pytest.fixture
    def manager_assignment(self):
        return {
            "user_id": "user_manager",
            "org_id": "org_1",
            "position_code": "manager",
            "department_id": "dept_ops_1",
            "department_type": "ops",
            "data_scope": "dept_subtree",
            "data_scope_dept_ids": None,
        }

    @pytest.fixture
    def member_assignment(self):
        return {
            "user_id": "user_zhangsan",
            "org_id": "org_1",
            "position_code": "member",
            "department_id": "dept_ops_1",
            "department_type": "ops",
            "data_scope": "self",
            "data_scope_dept_ids": None,
        }

    @pytest.fixture
    def deputy_assignment(self):
        return {
            "user_id": "user_deputy",
            "org_id": "org_1",
            "position_code": "deputy",
            "department_id": "dept_ops_1",
            "department_type": "ops",
            "data_scope": "self",
            "data_scope_dept_ids": None,
        }

    @pytest.fixture
    def hr_manager_assignment(self):
        return {
            "user_id": "user_hr_mgr",
            "org_id": "org_1",
            "position_code": "manager",
            "department_id": "dept_hr",
            "department_type": "hr",
            "data_scope": "dept_subtree",
            "data_scope_dept_ids": None,
        }

    def _make_checker(self, assignment_returns: dict):
        """构造一个 mock checker，_get_assignment 返回指定 dict"""
        checker = PermissionChecker(db=MagicMock())
        checker._get_assignment = AsyncMock(side_effect=lambda uid, oid: assignment_returns.get(uid))
        return checker

    @pytest.mark.asyncio
    async def test_boss_can_do_anything(self, boss_assignment):
        checker = self._make_checker({"user_boss": boss_assignment})
        # 业务权限
        assert await checker.check("user_boss", "org_1", "task.view")
        assert await checker.check("user_boss", "org_1", "order.export")
        # 系统配置权限
        assert await checker.check("user_boss", "org_1", "sys.member.add")
        assert await checker.check("user_boss", "org_1", "sys.erp.config")

    @pytest.mark.asyncio
    async def test_vp_can_do_business_not_sys(self, vp_full_assignment):
        checker = self._make_checker({"user_vp1": vp_full_assignment})
        assert await checker.check("user_vp1", "org_1", "task.view")
        assert await checker.check("user_vp1", "org_1", "finance.export")
        # 不能改系统配置
        assert not await checker.check("user_vp1", "org_1", "sys.erp.config")
        assert not await checker.check("user_vp1", "org_1", "sys.member.add")

    @pytest.mark.asyncio
    async def test_vp_full_can_see_all_resources(self, vp_full_assignment):
        checker = self._make_checker({"user_vp1": vp_full_assignment})
        # 全公司副总：任意 resource 都允许
        resource_other_dept = {"user_id": "user_other", "org_id": "org_1"}
        assert await checker.check("user_vp1", "org_1", "task.view", resource_other_dept)

    @pytest.mark.asyncio
    async def test_manager_can_do_dept_business(self, manager_assignment, member_assignment):
        checker = self._make_checker({
            "user_manager": manager_assignment,
            "user_zhangsan": member_assignment,
        })
        # 主管业务权限：本部门成员
        same_dept_resource = {"user_id": "user_zhangsan", "org_id": "org_1"}
        assert await checker.check("user_manager", "org_1", "task.view", same_dept_resource)
        assert await checker.check("user_manager", "org_1", "task.edit", same_dept_resource)

    @pytest.mark.asyncio
    async def test_manager_cannot_do_sys_config(self, manager_assignment):
        checker = self._make_checker({"user_manager": manager_assignment})
        assert not await checker.check("user_manager", "org_1", "sys.erp.config")
        assert not await checker.check("user_manager", "org_1", "sys.member.add")

    @pytest.mark.asyncio
    async def test_hr_manager_can_edit_members(self, hr_manager_assignment):
        checker = self._make_checker({"user_hr_mgr": hr_manager_assignment})
        # 人事主管特殊权限：可以改员工部门职位
        assert await checker.check("user_hr_mgr", "org_1", "sys.member.edit")
        # 但不能改 ERP 凭证
        assert not await checker.check("user_hr_mgr", "org_1", "sys.erp.config")

    @pytest.mark.asyncio
    async def test_member_only_self(self, member_assignment):
        checker = self._make_checker({"user_zhangsan": member_assignment})
        # 自己的资源：允许
        own = {"user_id": "user_zhangsan", "org_id": "org_1"}
        assert await checker.check("user_zhangsan", "org_1", "task.view", own)
        assert await checker.check("user_zhangsan", "org_1", "task.edit", own)
        # 别人的资源：拒绝
        other = {"user_id": "user_other", "org_id": "org_1"}
        assert not await checker.check("user_zhangsan", "org_1", "task.view", other)
        assert not await checker.check("user_zhangsan", "org_1", "task.edit", other)

    @pytest.mark.asyncio
    async def test_deputy_same_as_member(self, deputy_assignment):
        checker = self._make_checker({"user_deputy": deputy_assignment})
        # 副主管 = 员工权限
        own = {"user_id": "user_deputy", "org_id": "org_1"}
        other = {"user_id": "user_other", "org_id": "org_1"}
        assert await checker.check("user_deputy", "org_1", "task.view", own)
        assert not await checker.check("user_deputy", "org_1", "task.view", other)
        # 副主管不能改系统配置
        assert not await checker.check("user_deputy", "org_1", "sys.member.add")

    @pytest.mark.asyncio
    async def test_member_no_dept_no_perms(self):
        """没有部门归属的成员（迁移后未分配） → 没有业务权限"""
        no_dept = {
            "user_id": "user_orphan",
            "org_id": "org_1",
            "position_code": "member",
            "department_id": None,
            "department_type": None,
            "data_scope": "self",
            "data_scope_dept_ids": None,
        }
        checker = self._make_checker({"user_orphan": no_dept})
        # 没部门 → 没角色 → 没权限
        assert not await checker.check("user_orphan", "org_1", "task.view")

    @pytest.mark.asyncio
    async def test_design_role_no_order_export(self):
        """设计员工不能导出订单"""
        design_member = {
            "user_id": "user_designer",
            "org_id": "org_1",
            "position_code": "member",
            "department_id": "dept_design",
            "department_type": "design",
            "data_scope": "self",
            "data_scope_dept_ids": None,
        }
        checker = self._make_checker({"user_designer": design_member})
        own = {"user_id": "user_designer", "org_id": "org_1"}
        # 可以创建任务
        assert await checker.check("user_designer", "org_1", "task.view", own)
        # 但不能导出订单
        assert not await checker.check("user_designer", "org_1", "order.export", own)

    @pytest.mark.asyncio
    async def test_no_assignment_returns_false(self):
        """没有任职记录 → 全部拒绝"""
        checker = self._make_checker({})  # 空映射
        assert not await checker.check("ghost_user", "org_1", "task.view")

    @pytest.mark.asyncio
    async def test_member_cannot_push_to_others(self, member_assignment):
        """member 职位不能 task.push_to_others（即使在自己资源上）"""
        checker = self._make_checker({"user_zhangsan": member_assignment})
        own = {"user_id": "user_zhangsan", "org_id": "org_1"}
        # member 自己的任务可以编辑
        assert await checker.check("user_zhangsan", "org_1", "task.edit", own)
        # 但不能 push_to_others
        assert not await checker.check("user_zhangsan", "org_1", "task.push_to_others")

    @pytest.mark.asyncio
    async def test_deputy_can_push_to_others(self, deputy_assignment):
        """deputy 职位可以 task.push_to_others"""
        checker = self._make_checker({"user_deputy": deputy_assignment})
        assert await checker.check("user_deputy", "org_1", "task.push_to_others")

    @pytest.mark.asyncio
    async def test_manager_can_push_to_others(self, manager_assignment):
        """manager 职位可以 task.push_to_others"""
        checker = self._make_checker({"user_manager": manager_assignment})
        assert await checker.check("user_manager", "org_1", "task.push_to_others")

    @pytest.mark.asyncio
    async def test_boss_can_push_to_others(self, boss_assignment):
        checker = self._make_checker({"user_boss": boss_assignment})
        assert await checker.check("user_boss", "org_1", "task.push_to_others")


# ════════════════════════════════════════════════════════
# 3. compute_user_permissions 扁平权限码
# ════════════════════════════════════════════════════════

class TestComputeUserPermissions:

    def _patch_checker(self, monkeypatch, assignment):
        """patch get_checker 返回 mock"""
        from services.permissions import effective_perms
        mock_checker = MagicMock()
        mock_checker.get_assignment = AsyncMock(return_value=assignment)
        monkeypatch.setattr(effective_perms, "get_checker", lambda db: mock_checker)

    @pytest.mark.asyncio
    async def test_boss_has_all_permission_codes(self, monkeypatch):
        boss = {
            "position_code": "boss",
            "department_type": None,
            "data_scope": "all",
        }
        self._patch_checker(monkeypatch, boss)
        perms = await compute_user_permissions(MagicMock(), "user_b", "org_1")
        # 应该包含所有权限点
        assert set(perms) == set(PERMISSIONS.keys())

    @pytest.mark.asyncio
    async def test_vp_has_no_sys_permissions(self, monkeypatch):
        vp = {
            "position_code": "vp",
            "department_type": None,
            "data_scope": "all",
        }
        self._patch_checker(monkeypatch, vp)
        perms = await compute_user_permissions(MagicMock(), "user_v", "org_1")
        for code in perms:
            assert not code.startswith("sys.")

    @pytest.mark.asyncio
    async def test_ops_member_has_ops_perms(self, monkeypatch):
        ops_member = {
            "position_code": "member",
            "department_type": "ops",
            "data_scope": "self",
        }
        self._patch_checker(monkeypatch, ops_member)
        perms = set(await compute_user_permissions(MagicMock(), "user_o", "org_1"))
        assert "task.view" in perms
        assert "order.export" in perms
        assert "finance.export" not in perms  # 财务权限没有
        assert "stock.inbound" not in perms  # 仓库权限没有

    @pytest.mark.asyncio
    async def test_hr_manager_has_member_edit(self, monkeypatch):
        hr_mgr = {
            "position_code": "manager",
            "department_type": "hr",
            "data_scope": "dept_subtree",
        }
        self._patch_checker(monkeypatch, hr_mgr)
        perms = set(await compute_user_permissions(MagicMock(), "user_h", "org_1"))
        assert "sys.member.edit" in perms
        # 但不能改 ERP
        assert "sys.erp.config" not in perms

    @pytest.mark.asyncio
    async def test_no_assignment_empty(self, monkeypatch):
        from services.permissions import effective_perms
        mock_checker = MagicMock()
        mock_checker.get_assignment = AsyncMock(return_value=None)
        monkeypatch.setattr(effective_perms, "get_checker", lambda db: mock_checker)

        perms = await compute_user_permissions(MagicMock(), "ghost", "org_1")
        assert perms == []

    @pytest.mark.asyncio
    async def test_member_does_not_have_push_to_others(self, monkeypatch):
        """member 职位不应有 task.push_to_others"""
        ops_member = {
            "position_code": "member",
            "department_type": "ops",
            "data_scope": "self",
        }
        self._patch_checker(monkeypatch, ops_member)
        perms = set(await compute_user_permissions(MagicMock(), "user_m", "org_1"))
        assert "task.create" in perms  # 普通任务权限有
        assert "task.push_to_others" not in perms  # 但不能推给别人

    @pytest.mark.asyncio
    async def test_deputy_has_push_to_others(self, monkeypatch):
        """deputy 职位有 task.push_to_others"""
        ops_deputy = {
            "position_code": "deputy",
            "department_type": "ops",
            "data_scope": "self",
        }
        self._patch_checker(monkeypatch, ops_deputy)
        perms = set(await compute_user_permissions(MagicMock(), "user_d", "org_1"))
        assert "task.push_to_others" in perms

    @pytest.mark.asyncio
    async def test_manager_has_push_to_others(self, monkeypatch):
        """manager 职位有 task.push_to_others"""
        ops_manager = {
            "position_code": "manager",
            "department_type": "ops",
            "data_scope": "dept_subtree",
        }
        self._patch_checker(monkeypatch, ops_manager)
        perms = set(await compute_user_permissions(MagicMock(), "user_mgr", "org_1"))
        assert "task.push_to_others" in perms
