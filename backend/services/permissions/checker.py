"""权限检查器（V1 - 硬编码职位逻辑）

V1 阶段：根据职位 + 部门 + 数据范围判断
V2 阶段：在此基础上叠加 user_extra_grants 和 user_revocations

设计文档: docs/document/TECH_组织架构与权限模型.md §5
"""
from __future__ import annotations
from typing import Any, Dict, Optional

from loguru import logger

from services.permissions.permission_points import (
    DEPT_TYPE_TO_PERMS,
    MEMBER_DENIED_PERMS,
    ROLE_BOSS_FULL_PERMS,
    ROLE_VP_FULL_PERMS,
    is_system_permission,
)


class PermissionChecker:
    """V1 权限检查器（硬编码职位逻辑）

    V1 不做缓存：多 worker 进程时进程内缓存会产生权限漂移
    （A worker 改了员工部门，B worker 仍读旧数据）。
    V2 改为 Redis + 失效机制。
    """

    def __init__(self, db: Any):
        self.db = db

    async def check(
        self,
        user_id: str,
        org_id: str,
        permission_code: str,
        resource: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """检查用户是否有权限执行操作

        Args:
            user_id: 用户 ID
            org_id: 当前组织 ID
            permission_code: 权限码（如 "task.edit"）
            resource: 资源对象（dict），需要包含 user_id 字段（创建者）；
                     None 表示列表查询，由 apply_data_scope 处理

        Returns:
            True 表示允许，False 表示拒绝
        """
        assignment = await self._get_assignment(user_id, org_id)
        if not assignment:
            logger.warning(
                f"PermissionChecker: 用户 {user_id} 在组织 {org_id} 没有任职记录"
            )
            return False

        position_code = assignment["position_code"]

        # 1. 老板：全部允许
        if position_code == "boss":
            return True

        # 2. 副总：业务权限全部允许，系统配置拒绝（除非是老板）
        if position_code == "vp":
            if is_system_permission(permission_code):
                return False
            if permission_code not in ROLE_VP_FULL_PERMS:
                return False
            # 检查数据范围
            return await self._check_vp_scope(assignment, resource)

        # 3. 主管：本部门内的业务权限
        if position_code == "manager":
            if is_system_permission(permission_code):
                # 人事主管特殊处理：sys.member.edit 允许
                if permission_code == "sys.member.edit" and \
                   assignment.get("department_type") == "hr":
                    pass
                else:
                    return False
            if not self._has_dept_role_permission(assignment, permission_code):
                return False
            return await self._check_dept_scope(assignment, resource)

        # 4. 副主管 / 员工：只能操作自己的资源
        if position_code in ("deputy", "member"):
            if is_system_permission(permission_code):
                return False
            # member 显式拒绝管理职位专属权限（如 task.push_to_others）
            if position_code == "member" and permission_code in MEMBER_DENIED_PERMS:
                return False
            if not self._has_dept_role_permission(assignment, permission_code):
                return False
            # task.execute 员工/副主管只能执行自己的
            if resource is not None:
                if resource.get("user_id") != user_id:
                    return False
            return True

        return False

    def _has_dept_role_permission(
        self, assignment: Dict[str, Any], permission_code: str
    ) -> bool:
        """检查成员所在部门类型对应的业务角色是否包含该权限"""
        dept_type = assignment.get("department_type")
        if not dept_type:
            return False  # 没有分配部门 = 没有业务权限
        perms = DEPT_TYPE_TO_PERMS.get(dept_type, set())
        return permission_code in perms

    async def _check_vp_scope(
        self, assignment: Dict[str, Any], resource: Optional[Dict[str, Any]]
    ) -> bool:
        """副总数据范围检查"""
        if assignment["data_scope"] == "all":
            return True
        if not resource:
            return True  # 列表查询交给 apply_data_scope
        # 分管副总：检查资源创建者是否在分管部门
        managed = assignment.get("data_scope_dept_ids") or []
        if not managed:
            return False
        return await self._is_resource_in_depts(resource, managed)

    async def _check_dept_scope(
        self, assignment: Dict[str, Any], resource: Optional[Dict[str, Any]]
    ) -> bool:
        """主管数据范围检查：本部门"""
        if not resource:
            return True
        dept_id = assignment.get("department_id")
        if not dept_id:
            return False
        return await self._is_resource_in_depts(resource, [dept_id])

    async def _is_resource_in_depts(
        self, resource: Dict[str, Any], dept_ids: list
    ) -> bool:
        """判断资源创建者是否在指定部门列表中（含子部门）"""
        creator_id = resource.get("user_id")
        if not creator_id:
            return False

        # 查创建者的 assignment
        creator_org_id = resource.get("org_id")
        if not creator_org_id:
            return False
        creator_assignment = await self._get_assignment(creator_id, creator_org_id)
        if not creator_assignment:
            return False

        creator_dept = creator_assignment.get("department_id")
        if not creator_dept:
            return False

        # 简化版：直接 ID 匹配（不查 ltree 子树）
        # V2 优化：用 ltree 查子树支持嵌套部门
        return creator_dept in dept_ids

    async def get_assignment(
        self, user_id: str, org_id: str
    ) -> Optional[Dict[str, Any]]:
        """查询用户在组织内的任职信息（V1 直查 DB，无缓存）"""
        try:
            result = self.db.table("org_member_assignments") \
                .select(
                    "id, user_id, org_id, department_id, position_id, "
                    "job_title, data_scope, data_scope_dept_ids, perm_version"
                ) \
                .eq("user_id", user_id) \
                .eq("org_id", org_id) \
                .eq("is_primary", True) \
                .limit(1) \
                .execute()

            if not result.data:
                return None

            row = result.data[0]

            # 查 position code
            pos = self.db.table("org_positions") \
                .select("code") \
                .eq("id", row["position_id"]) \
                .single() \
                .execute()
            row["position_code"] = pos.data["code"] if pos.data else None

            # 查 department type（如有）
            if row.get("department_id"):
                dept = self.db.table("org_departments") \
                    .select("type, name") \
                    .eq("id", row["department_id"]) \
                    .single() \
                    .execute()
                if dept.data:
                    row["department_type"] = dept.data["type"]
                    row["department_name"] = dept.data["name"]

            return row
        except Exception as e:
            logger.error(f"get_assignment error | user={user_id} | error={e}")
            return None

    # 向后兼容别名（其他模块可能调用 _get_assignment）
    async def _get_assignment(
        self, user_id: str, org_id: str
    ) -> Optional[Dict[str, Any]]:
        return await self.get_assignment(user_id, org_id)

    def invalidate_cache(self, user_id: Optional[str] = None) -> None:
        """no-op（V1 无缓存，保留接口供调用方平滑迁移）"""
        return


# ────────────────────────────────────────────────────────────
# 全局便捷函数
# ────────────────────────────────────────────────────────────

_checker_instance: Optional[PermissionChecker] = None


def get_checker(db: Any) -> PermissionChecker:
    """获取全局 PermissionChecker 实例"""
    global _checker_instance
    if _checker_instance is None:
        _checker_instance = PermissionChecker(db)
    return _checker_instance


async def check_permission(
    db: Any,
    user_id: str,
    org_id: str,
    permission_code: str,
    resource: Optional[Dict[str, Any]] = None,
) -> bool:
    """便捷函数：检查权限"""
    checker = get_checker(db)
    return await checker.check(user_id, org_id, permission_code, resource)
