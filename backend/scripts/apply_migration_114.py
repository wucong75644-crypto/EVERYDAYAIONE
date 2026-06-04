#!/usr/bin/env python3
"""
应用 migration 114: kuaimai_external_data

幂等：可重复运行，CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS 已写好。

用法：
    cd backend && venv/bin/python scripts/apply_migration_114.py

验证：
    跑完后会自动 SELECT 4 张表的 count，输出空表确认 schema 已建立。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger

from core.database import get_db


MIGRATION_FILE = Path(__file__).parent.parent / "migrations" / "114_kuaimai_external_data.sql"

EXPECTED_TABLES = [
    "kuaimai_external_credentials",
    "erp_thinktank_profit_shop",
    "erp_viperp_sale_finance",
    "kuaimai_sync_logs",
    "kuaimai_field_audit",
    "erp_shop_operators",
    "erp_operators",
]


def main() -> int:
    if not MIGRATION_FILE.exists():
        logger.error(f"迁移文件不存在: {MIGRATION_FILE}")
        return 1

    sql = MIGRATION_FILE.read_text(encoding="utf-8")
    logger.info(f"读取迁移文件: {MIGRATION_FILE} ({len(sql)} 字符)")

    db = get_db()
    pool = db.pool

    # 执行迁移（psycopg 自动开事务，整个 SQL 一次性执行）
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
    logger.info("✅ 迁移 SQL 执行成功")

    # 验证 4 张表存在且可查询
    logger.info("─" * 50)
    logger.info("验证表结构:")
    with pool.connection() as conn:
        with conn.cursor() as cur:
            for table in EXPECTED_TABLES:
                cur.execute(f"SELECT COUNT(*) AS n FROM {table}")
                row = cur.fetchone()
                count = row["n"] if row else 0
                logger.info(f"  ✓ {table:40s} | rows={count}")

    logger.info("─" * 50)
    logger.info("🎉 Migration 114 应用完成")
    logger.info("下一步：把 4 张表加到 core/org_scoped_db.py 的 TENANT_TABLES")
    return 0


if __name__ == "__main__":
    sys.exit(main())
