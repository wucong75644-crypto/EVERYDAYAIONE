#!/usr/bin/env python3
"""
数据迁移脚本：从 Supabase 导出非 ERP 数据 → 导入本地 PostgreSQL

用法：
    # 在服务器上，backend venv 内运行
    cd /var/www/everydayai/backend
    source venv/bin/activate
    python /var/www/everydayai/deploy/migrate-data.py

环境变量（从 .env 读取）：
    SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY  — 源数据库
    DATABASE_URL                              — 目标本地数据库
"""

import json
import os
import sys
from datetime import datetime

# 从 .env 加载环境变量
from dotenv import load_dotenv
load_dotenv()

import psycopg
from psycopg.rows import dict_row
from supabase import create_client

# ============================================================
# 配置
# ============================================================

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
DATABASE_URL = os.environ["DATABASE_URL"]

# 需要迁移数据的表（按依赖顺序排列）
TABLES_TO_MIGRATE = [
    # 无外键依赖
    "users",
    "models",
    # 依赖 users
    "user_subscriptions",
    "conversations",
    "image_generations",
    "credits_history",
    "admin_action_logs",
    "user_memory_settings",
    "wecom_user_mappings",
    "wecom_chat_targets",
    "wecom_departments",
    "wecom_employees",
    # 依赖 conversations
    "messages",
    "tasks",
    # 依赖 tasks
    "credit_transactions",
    # 知识库（无外键依赖用户表）
    "knowledge_metrics",
    "knowledge_nodes",
    "knowledge_edges",
    "scoring_audit_log",
]

# Supabase 每次查询最大行数
PAGE_SIZE = 1000


# ============================================================
# 工具函数
# ============================================================

def log(msg: str):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def fetch_all_rows(supabase, table_name: str) -> list[dict]:
    """从 Supabase 分页读取全部数据"""
    all_rows = []
    offset = 0

    while True:
        response = (
            supabase.table(table_name)
            .select("*")
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
        )
        rows = response.data
        if not rows:
            break
        all_rows.extend(rows)
        if len(rows) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    return all_rows


def insert_rows(conn, table_name: str, rows: list[dict]):
    """批量插入数据到本地 PostgreSQL"""
    if not rows:
        return 0

    columns = list(rows[0].keys())
    col_sql = ", ".join(f'"{c}"' for c in columns)
    placeholders = ", ".join(["%s"] * len(columns))

    # 使用 ON CONFLICT DO NOTHING 避免重复插入
    sql = f'INSERT INTO "{table_name}" ({col_sql}) VALUES ({placeholders}) ON CONFLICT DO NOTHING'

    count = 0
    with conn.cursor() as cur:
        for row in rows:
            values = []
            for c in columns:
                val = row.get(c)
                # JSONB / list / dict → JSON 字符串
                if isinstance(val, (dict, list)):
                    val = json.dumps(val, ensure_ascii=False)
                values.append(val)
            try:
                cur.execute(sql, values)
                count += 1
            except Exception as e:
                log(f"  ⚠ 跳过行 ({table_name}): {e}")
                conn.rollback()
                continue

    conn.commit()
    return count


# ============================================================
# 主流程
# ============================================================

def main():
    log("=" * 60)
    log("EverydayAI 数据迁移：Supabase → 本地 PostgreSQL")
    log("=" * 60)

    # 连接 Supabase
    log("连接 Supabase...")
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    log("Supabase 连接成功 ✓")

    # 连接本地 PostgreSQL
    log(f"连接本地 PostgreSQL...")
    conn = psycopg.connect(DATABASE_URL, row_factory=dict_row)
    conn.autocommit = False
    log("本地 PostgreSQL 连接成功 ✓")

    # 临时禁用外键检查（加速导入）
    with conn.cursor() as cur:
        cur.execute("SET session_replication_role = 'replica';")
    conn.commit()

    total_migrated = 0
    errors = []

    for table in TABLES_TO_MIGRATE:
        try:
            log(f"\n--- {table} ---")

            # 从 Supabase 读取
            log(f"  读取 Supabase 数据...")
            rows = fetch_all_rows(supabase, table)
            log(f"  读取到 {len(rows)} 行")

            if not rows:
                log(f"  跳过（空表）")
                continue

            # 写入本地
            log(f"  写入本地数据库...")
            count = insert_rows(conn, table, rows)
            log(f"  成功写入 {count} 行 ✓")
            total_migrated += count

        except Exception as e:
            log(f"  ✗ 迁移失败: {e}")
            errors.append((table, str(e)))
            conn.rollback()

    # 恢复外键检查
    with conn.cursor() as cur:
        cur.execute("SET session_replication_role = 'origin';")
    conn.commit()

    # 重置序列（BIGSERIAL 表）
    log("\n重置序列...")
    serial_tables = [
        "erp_document_items", "erp_document_items_archive",
        "erp_product_daily_stats", "erp_products", "erp_product_skus",
        "erp_stock_status", "erp_suppliers", "erp_product_platform_map",
        "erp_sync_state",
    ]
    with conn.cursor() as cur:
        for t in serial_tables:
            try:
                cur.execute(f"""
                    SELECT setval(pg_get_serial_sequence('"{t}"', 'id'),
                                  COALESCE(MAX(id), 1))
                    FROM "{t}";
                """)
            except Exception:
                pass
    conn.commit()

    conn.close()

    # 结果汇总
    log("\n" + "=" * 60)
    log(f"迁移完成！共迁移 {total_migrated} 行数据")
    if errors:
        log(f"\n⚠ {len(errors)} 个表迁移失败：")
        for table, err in errors:
            log(f"  - {table}: {err}")
    else:
        log("所有表迁移成功 ✓")
    log("=" * 60)


if __name__ == "__main__":
    main()
