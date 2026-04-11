"""权限模块（V1 - 硬编码职位检查）

参考 Salesforce Profile + Permission Sets / 飞书职位角色 / AWS IAM 三级优先级
设计文档: docs/document/TECH_组织架构与权限模型.md
"""
from services.permissions.permission_points import PERMISSIONS
from services.permissions.checker import PermissionChecker, check_permission
from services.permissions.scope_filter import apply_data_scope, get_users_in_depts

__all__ = [
    "PERMISSIONS",
    "PermissionChecker",
    "check_permission",
    "apply_data_scope",
    "get_users_in_depts",
]
