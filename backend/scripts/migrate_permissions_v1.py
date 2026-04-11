#!/usr/bin/env python3
"""
权限模型 V1 一次性迁移脚本

功能：
1. 为所有现有 organizations 调用 initialize_organization
   - 创建 5 个职位、9 个系统角色、6 个默认部门、职位默认角色映射
2. 把所有现有 org_members 迁移到 org_member_assignments
   - owner → boss + scope=all
   - admin/member → member + scope=self（部门待管理员手动分配）

使用方法：
cd backend && source venv/bin/activate
python3 scripts/migrate_permissions_v1.py

幂等：可重复运行，不会重复创建或覆盖。

⚠️ 部署前置条件：
- migration 060-068 必须先跑过（建表）
- 包含 CREATE EXTENSION ltree

设计文档: docs/document/TECH_组织架构与权限模型.md §十
"""

import asyncio
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger

from core.database import get_db
from services.permissions.initialization import migrate_existing_organizations


async def main():
    logger.info("权限模型 V1 迁移开始...")

    db = get_db()

    # 1. 检查必要的表是否存在（fail-fast）
    try:
        db.table("org_positions").select("id").limit(1).execute()
        db.table("org_departments").select("id").limit(1).execute()
        db.table("org_member_assignments").select("id").limit(1).execute()
    except Exception as e:
        logger.error(
            f"前置检查失败：权限模型表不存在或不可访问 | error={e}\n"
            "请先运行 migration 060-068 建表后再执行本脚本"
        )
        sys.exit(1)

    # 2. 跑迁移
    try:
        stats = await migrate_existing_organizations(db)
        logger.info(f"迁移完成 | stats={stats}")
    except Exception as e:
        logger.error(f"迁移失败 | error={e}")
        sys.exit(1)

    # 3. 输出后续提醒
    print()
    print("=" * 60)
    print("✅ 权限模型 V1 迁移完成")
    print("=" * 60)
    print(f"  组织初始化数量: {stats.get('orgs_initialized', 0)}")
    print(f"  成员迁移数量:   {stats.get('members_migrated', 0)}")
    print()
    print("⚠️ 后续步骤：")
    print("  1. 老板/admin 登录管理面板 → 「部门职位」tab")
    print("  2. 为每个非 owner 的成员分配部门 + 职位")
    print("  3. 未分配的成员默认数据范围 = 'self'（只能看自己）")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
