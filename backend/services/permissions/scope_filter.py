"""SQL 数据范围注入工具

把用户的"数据范围"作为 WHERE 条件注入到查询里，避免"先查后过滤"。
设计文档: docs/document/TECH_组织架构与权限模型.md §6
"""
from __future__ import annotations
from typing import Any, Optional, Set

from loguru import logger

from services.permissions.checker import get_checker


async def apply_data_scope(
    db: Any,
    query: Any,
    user_id: str,
    org_id: str,
    permission_code: str,
    user_id_field: str = "user_id",
) -> Any:
    """根据用户权限给查询自动加 WHERE 条件

    Args:
        db: 数据库客户端
        query: QueryBuilder 实例
        user_id: 当前用户 ID
        org_id: 当前组织 ID
        permission_code: 权限码（暂未使用，预留 V2 校验权限点）
        user_id_field: 资源表中标记创建者的字段名，默认 "user_id"

    Returns:
        加了 WHERE 条件的 query

    用法:
        query = db.table("scheduled_tasks").select("*").eq("org_id", org_id)
        query = await apply_data_scope(
            db, query, current_user.id, current_user.org_id, "task.view"
        )
    """
    checker = get_checker(db)
    assignment = await checker._get_assignment(user_id, org_id)

    if not assignment:
        # 无任职记录 → 只能看自己
        logger.warning(
            f"apply_data_scope: 用户 {user_id} 无任职 → 限制为 self"
        )
        return query.eq(user_id_field, user_id)

    position = assignment["position_code"]

    # 老板：不加过滤
    if position == "boss":
        return query

    # 副总
    if position == "vp":
        if assignment["data_scope"] == "all":
            return query
        # 分管副总：限制到分管部门成员
        managed_dept_ids = assignment.get("data_scope_dept_ids") or []
        if not managed_dept_ids:
            return query.eq(user_id_field, user_id)
        dept_user_ids = await get_users_in_depts(db, managed_dept_ids)
        if not dept_user_ids:
            return query.eq(user_id_field, user_id)
        return query.in_(user_id_field, list(dept_user_ids))

    # 主管：本部门所有成员
    if position == "manager":
        dept_id = assignment.get("department_id")
        if not dept_id:
            return query.eq(user_id_field, user_id)
        dept_user_ids = await get_users_in_depts(db, [dept_id])
        if not dept_user_ids:
            return query.eq(user_id_field, user_id)
        return query.in_(user_id_field, list(dept_user_ids))

    # 副主管 / 员工：只看自己
    return query.eq(user_id_field, user_id)


async def get_users_in_depts(
    db: Any, dept_ids: list, include_subtree: bool = True
) -> Set[str]:
    """查询部门下所有成员（含子部门）

    Args:
        db: 数据库客户端
        dept_ids: 部门 ID 列表
        include_subtree: 是否包含子部门（V1 简化为不查子树，需要 ltree raw SQL）

    Returns:
        user_id 集合
    """
    if not dept_ids:
        return set()

    try:
        # V1 简化：直接查 dept_id IN (...)，不递归查子树
        # V2 优化：用 ltree 查子树
        result = db.table("org_member_assignments") \
            .select("user_id") \
            .in_("department_id", list(dept_ids)) \
            .eq("is_primary", True) \
            .execute()

        return {row["user_id"] for row in (result.data or [])}
    except Exception as e:
        logger.error(f"get_users_in_depts error | dept_ids={dept_ids} | error={e}")
        return set()
