"""全局权限点常量

权限码格式: <module>.<action>
对应数据库 permissions 表（migration 063）
"""
from typing import Dict, Tuple

# (module, action, name)
PERMISSIONS: Dict[str, Tuple[str, str, str]] = {
    # ─── 定时任务 ───
    "task.view":            ("task", "view",            "查看定时任务"),
    "task.create":          ("task", "create",          "创建定时任务"),
    "task.edit":            ("task", "edit",            "编辑定时任务"),
    "task.delete":          ("task", "delete",          "删除定时任务"),
    "task.execute":         ("task", "execute",         "立即执行定时任务"),
    "task.push_to_others":  ("task", "push_to_others",  "推送任务给他人"),

    # ─── 订单 ───
    "order.view":   ("order", "view",   "查看订单"),
    "order.edit":   ("order", "edit",   "编辑订单"),
    "order.export": ("order", "export", "导出订单"),

    # ─── 商品 ───
    "product.view": ("product", "view", "查看商品"),
    "product.edit": ("product", "edit", "编辑商品"),

    # ─── 财务 ───
    "finance.view":      ("finance", "view",      "查看财务"),
    "finance.export":    ("finance", "export",    "导出财务"),
    "finance.reconcile": ("finance", "reconcile", "财务对账"),

    # ─── 库存 ───
    "stock.view":     ("stock", "view",     "查看库存"),
    "stock.edit":     ("stock", "edit",     "编辑库存"),
    "stock.inbound":  ("stock", "inbound",  "入库"),
    "stock.outbound": ("stock", "outbound", "出库"),

    # ─── 系统配置（仅老板）───
    "sys.member.add":       ("sys", "member_add",  "添加员工"),
    "sys.member.edit":      ("sys", "member_edit", "编辑员工部门职位"),
    "sys.erp.config":       ("sys", "erp_config",  "配置 ERP 凭证"),
    "sys.wecom.config":     ("sys", "wecom_config","配置企微"),
    "sys.permission.grant": ("sys", "perm_grant",  "授予额外权限"),
}


def is_system_permission(code: str) -> bool:
    """判断是否为系统配置权限（仅老板可用）"""
    return code.startswith("sys.")


# ────────────────────────────────────────────────────────────
# 角色 → 权限映射（V1 硬编码，对应 migration 中的预填角色）
# ────────────────────────────────────────────────────────────

# task.push_to_others 的语义：允许把定时任务的 push_target 设为同事或群聊。
# 在 6 个部门角色里都包含——但 member 职位会在 effective_perms / checker
# 里被显式剥离，只有 deputy/manager（及以上 boss/vp）实际拥有此权限。
_PUSH_TO_OTHERS = {"task.push_to_others"}

# 业务角色（按部门类型自动分配）
ROLE_OPS_PERMS = {
    "task.view", "task.create", "task.edit", "task.delete", "task.execute",
    "order.view", "order.edit", "order.export",
    "product.view", "product.edit",
} | _PUSH_TO_OTHERS

ROLE_FINANCE_PERMS = {
    "task.view", "task.create", "task.edit", "task.delete", "task.execute",
    "finance.view", "finance.export", "finance.reconcile",
    "order.view", "order.export",
} | _PUSH_TO_OTHERS

ROLE_WAREHOUSE_PERMS = {
    "task.view", "task.create", "task.edit", "task.delete", "task.execute",
    "stock.view", "stock.edit", "stock.inbound", "stock.outbound",
    "product.view",
} | _PUSH_TO_OTHERS

ROLE_SERVICE_PERMS = {
    "task.view", "task.create", "task.edit", "task.delete", "task.execute",
    "order.view", "order.edit",
    "product.view",
} | _PUSH_TO_OTHERS

ROLE_DESIGN_PERMS = {
    "task.view", "task.create", "task.edit", "task.delete",
    "product.view",
} | _PUSH_TO_OTHERS

ROLE_HR_PERMS = {
    "task.view", "task.create", "task.edit", "task.delete",
    "sys.member.edit",  # 人事可以编辑员工部门职位
} | _PUSH_TO_OTHERS

# 仅管理职位（manager/deputy/boss/vp）拥有，普通 member 被显式剥离
MEMBER_DENIED_PERMS = frozenset({"task.push_to_others"})

# 系统级角色
ROLE_BOSS_FULL_PERMS = set(PERMISSIONS.keys())  # 老板：全部权限

ROLE_VP_FULL_PERMS = {
    code for code in PERMISSIONS
    if not code.startswith("sys.")  # 副总：除系统配置外的全部业务权限
}

# 部门类型 → 业务角色权限映射
DEPT_TYPE_TO_PERMS = {
    "ops":       ROLE_OPS_PERMS,
    "finance":   ROLE_FINANCE_PERMS,
    "warehouse": ROLE_WAREHOUSE_PERMS,
    "service":   ROLE_SERVICE_PERMS,
    "design":    ROLE_DESIGN_PERMS,
    "hr":        ROLE_HR_PERMS,
}
