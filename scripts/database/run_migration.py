#!/usr/bin/env python3
"""
执行数据库迁移脚本
"""
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
BACKEND_DIR = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from supabase import create_client
from dotenv import load_dotenv

load_dotenv(BACKEND_DIR / '.env')

def run_migration():
    """执行 add_client_task_id.sql 迁移"""

    # 读取环境变量
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY")

    if not supabase_url or not supabase_key:
        print("❌ 错误: 缺少 SUPABASE_URL 或 SUPABASE_KEY 环境变量")
        return False

    print(f"📡 连接到 Supabase: {supabase_url}")

    # 创建 Supabase 客户端
    supabase = create_client(supabase_url, supabase_key)

    # 读取迁移 SQL
    migration_file = str(BACKEND_DIR / "migrations" / "017_add_client_task_id.sql")

    print(f"📄 读取迁移文件: {migration_file}")

    with open(migration_file, "r", encoding="utf-8") as f:
        sql = f.read()

    # 移除注释行（以 -- 开头的行）
    sql_lines = [
        line for line in sql.split("\n")
        if line.strip() and not line.strip().startswith("--")
    ]

    # 分割成单独的 SQL 语句
    statements = []
    current_statement = []

    for line in sql_lines:
        current_statement.append(line)
        if ";" in line:
            statements.append("\n".join(current_statement))
            current_statement = []

    print(f"🔧 执行 {len(statements)} 条 SQL 语句...\n")

    # 执行每条语句
    try:
        for i, statement in enumerate(statements, 1):
            clean_statement = statement.strip()
            if not clean_statement:
                continue

            print(f"[{i}/{len(statements)}] 执行: {clean_statement[:80]}...")

            # 使用 rpc 调用来执行原始 SQL（如果 Supabase 支持）
            # 注意：anon key 可能没有权限执行 DDL，需要 service_role key
            result = supabase.rpc("exec_sql", {"sql": clean_statement}).execute()

            print(f"    ✅ 成功")

    except Exception as e:
        print(f"\n❌ 迁移失败: {e}")
        print("\n⚠️  注意：")
        print("   1. 确保使用的是 SERVICE_ROLE_KEY (不是 ANON_KEY)")
        print("   2. 或者直接在 Supabase Web UI 的 SQL Editor 中执行迁移")
        print(f"\n   迁移 SQL 文件路径: {migration_file}")
        return False

    print("\n" + "=" * 60)
    print("🎉 迁移执行成功！")
    print("=" * 60)
    print("\n✅ tasks 表已添加 client_task_id 字段")
    print("✅ 索引已创建")
    print("\n现在可以重新测试 Google 模型了！")

    return True


if __name__ == "__main__":
    success = run_migration()
    exit(0 if success else 1)
