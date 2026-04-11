"""有效权限计算

为前端 /api/auth/me 提供扁平化的权限码列表 + 成员任职信息
设计文档: docs/document/TECH_组织架构与权限模型.md §八
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional, Set

from loguru import logger

from services.permissions.permission_points import (
    DEPT_TYPE_TO_PERMS,
    MEMBER_DENIED_PERMS,
    ROLE_BOSS_FULL_PERMS,
    ROLE_VP_FULL_PERMS,
    is_system_permission,
)
from services.permissions.checker import get_checker


async def compute_user_permissions(
    db: Any, user_id: str, org_id: str
) -> List[str]:
    """计算用户的所有权限码（扁平化列表，供前端 usePermission 使用）

    V1 实现：根据职位 + 部门类型硬编码
    V2 实现：再叠加 user_extra_grants 和 user_revocations
    """
    checker = get_checker(db)
    assignment = await checker.get_assignment(user_id, org_id)

    if not assignment:
        return []

    position = assignment["position_code"]

    # 老板：全部权限
    if position == "boss":
        return sorted(ROLE_BOSS_FULL_PERMS)

    # 副总：业务权限全部
    if position == "vp":
        return sorted(ROLE_VP_FULL_PERMS)

    # 主管/副主管/员工：按部门类型给权限
    perms: Set[str] = set()
    dept_type = assignment.get("department_type")
    if dept_type:
        perms.update(DEPT_TYPE_TO_PERMS.get(dept_type, set()))

    # 主管的特殊优惠：sys.member.edit（如果是人事主管）
    if position == "manager" and dept_type == "hr":
        perms.add("sys.member.edit")

    # 员工显式剥离管理职位专属权限（如 task.push_to_others）
    if position == "member":
        perms -= MEMBER_DENIED_PERMS

    return sorted(perms)


async def get_member_context(
    db: Any, user_id: str, org_id: str
) -> Optional[Dict[str, Any]]:
    """获取用户在组织内的成员上下文（用于 /api/auth/me）

    Returns:
        {
            position_code, department_id, department_name, department_type,
            job_title, data_scope, managed_departments
        }
    """
    checker = get_checker(db)
    assignment = await checker.get_assignment(user_id, org_id)

    if not assignment:
        return None

    result = {
        "position_code": assignment["position_code"],
        "department_id": assignment.get("department_id"),
        "department_name": assignment.get("department_name"),
        "department_type": assignment.get("department_type"),
        "job_title": assignment.get("job_title"),
        "data_scope": assignment["data_scope"],
        "managed_departments": None,
    }

    # 副总分管部门：把 UUID[] 展开成 [{id, name}]
    if assignment["position_code"] == "vp" and assignment.get("data_scope_dept_ids"):
        try:
            dept_ids = assignment["data_scope_dept_ids"]
            depts = db.table("org_departments") \
                .select("id, name") \
                .in_("id", dept_ids) \
                .execute()
            result["managed_departments"] = [
                {"id": d["id"], "name": d["name"]} for d in (depts.data or [])
            ]
        except Exception as e:
            logger.error(f"get_member_context managed_depts | error={e}")

    return result
