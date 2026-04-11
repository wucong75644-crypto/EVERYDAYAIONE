"""组织权限初始化

新建组织时调用 initialize_organization()，自动创建：
- 5 个职位（boss/vp/manager/deputy/member）
- 9 个系统预设角色
- 6 个默认部门（运营/财务/仓库/客服/设计/人事）
- 职位默认角色映射
- 把组织 owner 设为 boss

设计文档: docs/document/TECH_组织架构与权限模型.md §4.4
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional
from uuid import uuid4

from loguru import logger

from services.permissions.permission_points import (
    PERMISSIONS,
    ROLE_OPS_PERMS,
    ROLE_FINANCE_PERMS,
    ROLE_WAREHOUSE_PERMS,
    ROLE_SERVICE_PERMS,
    ROLE_DESIGN_PERMS,
    ROLE_HR_PERMS,
    ROLE_BOSS_FULL_PERMS,
    ROLE_VP_FULL_PERMS,
)


# ============================================================
# 系统预设职位
# ============================================================
SYSTEM_POSITIONS = [
    ("boss",    "老板",   1),
    ("vp",      "副总",   2),
    ("manager", "主管",   3),
    ("deputy",  "副主管", 4),
    ("member",  "员工",   5),
]


# ============================================================
# 系统预设角色（code, name, permissions）
# ============================================================
SYSTEM_ROLES = [
    ("role_ops",       "运营角色", ROLE_OPS_PERMS),
    ("role_finance",   "财务角色", ROLE_FINANCE_PERMS),
    ("role_warehouse", "仓库角色", ROLE_WAREHOUSE_PERMS),
    ("role_service",   "客服角色", ROLE_SERVICE_PERMS),
    ("role_design",    "设计角色", ROLE_DESIGN_PERMS),
    ("role_hr",        "人事角色", ROLE_HR_PERMS),
    ("role_boss_full", "老板全权", ROLE_BOSS_FULL_PERMS),
    ("role_vp_full",   "副总全权", ROLE_VP_FULL_PERMS),
]


# ============================================================
# 默认部门（name, type）
# ============================================================
DEFAULT_DEPARTMENTS = [
    ("运营一部", "ops"),
    ("财务部",   "finance"),
    ("仓库部",   "warehouse"),
    ("客服部",   "service"),
    ("设计部",   "design"),
    ("人事部",   "hr"),
]


# ============================================================
# 部门类型 → 业务角色映射
# ============================================================
DEPT_TYPE_TO_ROLE_CODE = {
    "ops":       "role_ops",
    "finance":   "role_finance",
    "warehouse": "role_warehouse",
    "service":   "role_service",
    "design":    "role_design",
    "hr":        "role_hr",
}


async def initialize_organization(
    db: Any, org_id: str, owner_user_id: str
) -> None:
    """新建组织时初始化职位/角色/默认部门

    幂等：可重复调用，已存在的不会重复创建
    """
    logger.info(f"initialize_organization | org_id={org_id}")

    # 1. 创建职位
    position_id_map: Dict[str, str] = {}
    for code, name, level in SYSTEM_POSITIONS:
        position_id = await _upsert_position(db, org_id, code, name, level)
        position_id_map[code] = position_id

    # 2. 创建角色
    role_id_map: Dict[str, str] = {}
    for code, name, perms in SYSTEM_ROLES:
        role_id = await _upsert_role(db, org_id, code, name)
        role_id_map[code] = role_id
        # 关联权限
        await _set_role_permissions(db, role_id, perms)

    # 3. 创建默认部门
    for name, dept_type in DEFAULT_DEPARTMENTS:
        await _upsert_department(db, org_id, name, dept_type)

    # 4. 配置职位默认角色映射
    # 业务角色：员工/副主管/主管按部门类型自动获得对应业务角色
    for dept_type, role_code in DEPT_TYPE_TO_ROLE_CODE.items():
        role_id = role_id_map[role_code]
        for pos_code in ("member", "deputy", "manager"):
            await _upsert_position_default_role(
                db, org_id, pos_code, dept_type, role_id
            )

    # 老板和副总不属于具体部门，独立配置（用 'all' 占位 dept_type）
    await _upsert_position_default_role(
        db, org_id, "boss", "all", role_id_map["role_boss_full"]
    )
    await _upsert_position_default_role(
        db, org_id, "vp", "all", role_id_map["role_vp_full"]
    )

    # 5. 把 owner 设为 boss（如果还没有 assignment）
    await _ensure_owner_assignment(
        db, org_id, owner_user_id, position_id_map["boss"]
    )

    logger.info(f"initialize_organization done | org_id={org_id}")


# ════════════════════════════════════════════════════════
# 内部辅助函数
# ════════════════════════════════════════════════════════

async def _upsert_position(
    db: Any, org_id: str, code: str, name: str, level: int
) -> str:
    """创建或获取职位 ID"""
    existing = db.table("org_positions") \
        .select("id") \
        .eq("org_id", org_id) \
        .eq("code", code) \
        .limit(1) \
        .execute()
    if existing.data:
        return existing.data[0]["id"]

    position_id = str(uuid4())
    db.table("org_positions").insert({
        "id": position_id,
        "org_id": org_id,
        "code": code,
        "name": name,
        "level": level,
        "is_system": True,
    }).execute()
    return position_id


async def _upsert_role(db: Any, org_id: str, code: str, name: str) -> str:
    """创建或获取角色 ID"""
    existing = db.table("org_roles") \
        .select("id") \
        .eq("org_id", org_id) \
        .eq("code", code) \
        .limit(1) \
        .execute()
    if existing.data:
        return existing.data[0]["id"]

    role_id = str(uuid4())
    db.table("org_roles").insert({
        "id": role_id,
        "org_id": org_id,
        "code": code,
        "name": name,
        "is_system": True,
    }).execute()
    return role_id


async def _set_role_permissions(db: Any, role_id: str, permission_codes: set) -> None:
    """设置角色的权限点（先清空再插入）"""
    # 删除现有
    db.table("role_permissions").delete().eq("role_id", role_id).execute()

    # 批量插入
    if permission_codes:
        rows = [
            {"role_id": role_id, "permission_code": code}
            for code in permission_codes
        ]
        db.table("role_permissions").insert(rows).execute()


async def _upsert_department(
    db: Any, org_id: str, name: str, dept_type: str
) -> str:
    """创建或获取部门 ID"""
    existing = db.table("org_departments") \
        .select("id") \
        .eq("org_id", org_id) \
        .eq("name", name) \
        .limit(1) \
        .execute()
    if existing.data:
        return existing.data[0]["id"]

    dept_id = str(uuid4())
    # ltree 路径：root.{type}_{short_id}
    short_id = dept_id.replace("-", "")[:8]
    path = f"root.{dept_type}_{short_id}"

    db.table("org_departments").insert({
        "id": dept_id,
        "org_id": org_id,
        "name": name,
        "type": dept_type,
        "path": path,
    }).execute()
    return dept_id


async def _upsert_position_default_role(
    db: Any,
    org_id: str,
    position_code: str,
    department_type: str,  # 不能为 NULL（PostgreSQL 主键约束）
    role_id: str,
) -> None:
    """职位默认角色映射"""
    existing = db.table("position_default_roles") \
        .select("role_id") \
        .eq("org_id", org_id) \
        .eq("position_code", position_code) \
        .eq("department_type", department_type) \
        .eq("role_id", role_id) \
        .limit(1) \
        .execute()
    if existing.data:
        return

    db.table("position_default_roles").insert({
        "org_id": org_id,
        "position_code": position_code,
        "department_type": department_type,
        "role_id": role_id,
    }).execute()


async def _ensure_owner_assignment(
    db: Any, org_id: str, user_id: str, boss_position_id: str
) -> None:
    """确保 owner 在 org_member_assignments 里有 boss 任职"""
    existing = db.table("org_member_assignments") \
        .select("id") \
        .eq("org_id", org_id) \
        .eq("user_id", user_id) \
        .eq("is_primary", True) \
        .limit(1) \
        .execute()
    if existing.data:
        # 已有，更新 position 为 boss
        db.table("org_member_assignments") \
            .update({
                "position_id": boss_position_id,
                "data_scope": "all",
                "department_id": None,
            }) \
            .eq("id", existing.data[0]["id"]) \
            .execute()
        return

    db.table("org_member_assignments").insert({
        "id": str(uuid4()),
        "org_id": org_id,
        "user_id": user_id,
        "department_id": None,
        "position_id": boss_position_id,
        "data_scope": "all",
        "is_primary": True,
    }).execute()


# ════════════════════════════════════════════════════════
# 现有数据迁移：把所有现存组织全部初始化一遍
# ════════════════════════════════════════════════════════

async def migrate_existing_organizations(db: Any) -> Dict[str, int]:
    """迁移现有组织：
    1. 为每个组织调用 initialize_organization
    2. 把现有 org_members 迁移到 org_member_assignments

    Returns:
        统计字典 {orgs_initialized, members_migrated}
    """
    stats = {"orgs_initialized": 0, "members_migrated": 0}

    # 1. 初始化所有组织
    orgs = db.table("organizations").select("id, owner_id").execute()
    for org in (orgs.data or []):
        try:
            await initialize_organization(db, org["id"], org["owner_id"])
            stats["orgs_initialized"] += 1
        except Exception as e:
            logger.error(f"initialize_organization failed | org={org['id']} | error={e}")

    # 2. 迁移现有 org_members → org_member_assignments
    members = db.table("org_members") \
        .select("org_id, user_id, role, status") \
        .eq("status", "active") \
        .execute()

    for m in (members.data or []):
        try:
            # 老板（owner）→ position=boss, scope=all
            # admin/member → position=member, scope=self
            target_position = "boss" if m["role"] == "owner" else "member"
            target_scope = "all" if m["role"] == "owner" else "self"

            # 查 position id
            pos = db.table("org_positions") \
                .select("id") \
                .eq("org_id", m["org_id"]) \
                .eq("code", target_position) \
                .limit(1) \
                .execute()
            if not pos.data:
                logger.warning(
                    f"position not found | org={m['org_id']} | code={target_position}"
                )
                continue

            # 检查是否已存在
            existing = db.table("org_member_assignments") \
                .select("id") \
                .eq("org_id", m["org_id"]) \
                .eq("user_id", m["user_id"]) \
                .eq("is_primary", True) \
                .limit(1) \
                .execute()
            if existing.data:
                continue

            db.table("org_member_assignments").insert({
                "id": str(uuid4()),
                "org_id": m["org_id"],
                "user_id": m["user_id"],
                "department_id": None,    # 待管理员手动分配
                "position_id": pos.data[0]["id"],
                "data_scope": target_scope,
                "is_primary": True,
            }).execute()
            stats["members_migrated"] += 1
        except Exception as e:
            logger.error(
                f"migrate member failed | org={m['org_id']} | "
                f"user={m['user_id']} | error={e}"
            )

    logger.info(f"migrate_existing_organizations done | stats={stats}")
    return stats
