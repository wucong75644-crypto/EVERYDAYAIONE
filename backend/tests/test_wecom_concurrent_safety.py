"""Real WeCom concurrency RPCs in a disposable, socket-only PostgreSQL.

Never reads application DATABASE_URL or connects to a pre-existing database.
"""

from __future__ import annotations

import getpass
import sys
import uuid
from pathlib import Path

import pytest

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))


@pytest.fixture(scope="module")
def wecom_db(chat_image_postgres_socket):
    import psycopg
    from psycopg import sql
    name = "wecom_concurrency_" + uuid.uuid4().hex
    admin = psycopg.connect(host=chat_image_postgres_socket, dbname="postgres",
                            user=getpass.getuser(), autocommit=True)
    admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(name)))
    dsn = psycopg.conninfo.make_conninfo(host=chat_image_postgres_socket,
                                       dbname=name, user=getpass.getuser())
    try:
        with psycopg.connect(dsn) as conn:
            conn.execute("""
                CREATE TYPE user_created_by AS ENUM ('wecom');
                CREATE TYPE user_role AS ENUM ('user');
                CREATE TYPE account_status AS ENUM ('active');
                CREATE TYPE credits_change_type AS ENUM ('register_gift');
                CREATE TABLE users(id uuid PRIMARY KEY DEFAULT gen_random_uuid(), nickname text,
                  login_methods jsonb, created_by user_created_by, role user_role, credits int,
                  status account_status, last_login_at timestamptz, updated_at timestamptz);
                CREATE TABLE wecom_user_mappings(id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                  wecom_userid text, corp_id text, user_id uuid REFERENCES users ON DELETE CASCADE,
                  channel text, wecom_nickname text, org_id uuid);
                CREATE TABLE credits_history(id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                  user_id uuid REFERENCES users ON DELETE CASCADE, change_amount int,
                  balance_after int, change_type credits_change_type, description text, org_id uuid);
                CREATE TABLE conversations(user_id uuid REFERENCES users);
                CREATE TABLE image_generations(user_id uuid REFERENCES users);
                CREATE TABLE tasks(user_id uuid REFERENCES users);
                CREATE TABLE org_members(user_id uuid REFERENCES users, org_id uuid);
                CREATE TABLE credit_transactions(user_id uuid REFERENCES users);
                CREATE TABLE admin_action_logs(target_user_id uuid REFERENCES users);
            """)
            conn.execute((backend_dir / "migrations/116_wecom_dedup_root_fix.sql").read_text())
        yield dsn
    finally:
        admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
        admin.close()


def _get_pg_conn(dsn):
    import psycopg2
    return psycopg2.connect(dsn, connect_timeout=15)


def _cleanup(dsn: str, test_wecom_userid: str, test_corp_id: str) -> None:
    """清理测试创建的所有数据"""
    conn = _get_pg_conn(dsn)
    conn.autocommit = True
    cur = conn.cursor()
    try:
        cur.execute("""
        DELETE FROM users WHERE id IN (
            SELECT user_id FROM wecom_user_mappings
            WHERE wecom_userid = %s AND corp_id = %s
        );
        """, (test_wecom_userid, test_corp_id))
        cur.execute(
            "DELETE FROM wecom_user_mappings WHERE wecom_userid = %s AND corp_id = %s;",
            (test_wecom_userid, test_corp_id),
        )
    finally:
        conn.close()


def test_concurrent_rpc_creates_only_one_user(wecom_db):
    """
    50 个并发 RPC 调用同一个 (wecom_userid, corp_id) →
    应只创建 1 个 user + 1 个 mapping。
    """
    test_wecom_userid = f"concurrent_test_{uuid.uuid4().hex[:8]}"
    test_corp_id = "test_corp_concurrent"

    try:
        # 用 50 个并发线程跑 RPC
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def _call_rpc():
            conn = _get_pg_conn(wecom_db)
            conn.autocommit = True
            try:
                cur = conn.cursor()
                cur.execute("""
                SELECT wecom_get_or_create_user(%s, %s, NULL, 'smart_robot', '并发测试用户');
                """, (test_wecom_userid, test_corp_id))
                return cur.fetchone()[0]
            finally:
                conn.close()

        results = []
        with ThreadPoolExecutor(max_workers=50) as ex:
            futures = [ex.submit(_call_rpc) for _ in range(50)]
            for f in as_completed(futures):
                results.append(f.result())

        # 验证 1: 所有 RPC 返回同一个 user_id
        user_ids = {r["user_id"] for r in results}
        assert len(user_ids) == 1, (
            f"并发安全失败：返回了 {len(user_ids)} 个不同 user_id: {user_ids}"
        )

        # 验证 2: 只有 1 个 is_new=True（赢家），其余都是 False
        winners = [r for r in results if r["is_new"]]
        assert len(winners) == 1, (
            f"is_new=true 应该只有 1 个赢家，实际 {len(winners)}"
        )

        # 验证 3: DB 里只有 1 个 user + 1 个 mapping
        conn = _get_pg_conn(wecom_db)
        try:
            cur = conn.cursor()
            cur.execute("""
            SELECT count(*) FROM users WHERE id IN (
                SELECT user_id FROM wecom_user_mappings
                WHERE wecom_userid = %s AND corp_id = %s
            );
            """, (test_wecom_userid, test_corp_id))
            user_count = cur.fetchone()[0]
            assert user_count == 1, f"users 表应只有 1 行，实际 {user_count}"

            cur.execute(
                "SELECT count(*) FROM wecom_user_mappings WHERE wecom_userid = %s AND corp_id = %s;",
                (test_wecom_userid, test_corp_id),
            )
            mapping_count = cur.fetchone()[0]
            assert mapping_count == 1, f"mapping 表应只有 1 行，实际 {mapping_count}"

            # 验证 4: 只有 1 条 register_gift（赢家拿到了 100 积分）
            cur.execute("""
            SELECT count(*), sum(change_amount) FROM credits_history
            WHERE user_id IN (SELECT user_id FROM wecom_user_mappings
                              WHERE wecom_userid = %s AND corp_id = %s)
              AND change_type = 'register_gift';
            """, (test_wecom_userid, test_corp_id))
            gift_count, gift_sum = cur.fetchone()
            assert gift_count == 1, f"register_gift 应只有 1 条，实际 {gift_count}"
            assert gift_sum == 100, f"register_gift 总额应为 100，实际 {gift_sum}"
        finally:
            conn.close()

    finally:
        _cleanup(wecom_db, test_wecom_userid, test_corp_id)


def test_merge_rpc_migrates_all_refs(wecom_db):
    """
    合并 RPC：drop_uids 的 credits/conversations/tasks 等都迁到 keep_uid，
    drop_uids 被删除。
    """
    conn = _get_pg_conn(wecom_db)
    conn.autocommit = False
    cur = conn.cursor()

    keep_uid = None
    drop_uids = []
    try:
        # 创建 3 个测试 user（1 个 keep + 2 个 drop）
        for i in range(3):
            cur.execute("""
            INSERT INTO users (nickname, login_methods, created_by, role, credits, status)
            VALUES ('test_merge_target', '["wecom"]'::jsonb, 'wecom',
                    'user', %s, 'active')
            RETURNING id;
            """, (50 + i * 10,))  # 50 / 60 / 70 积分
            uid = cur.fetchone()[0]
            if i == 0:
                keep_uid = uid
            else:
                drop_uids.append(uid)
        conn.commit()

        # Real FK rows must move too; credits alone cannot prove reference migration.
        for uid in drop_uids:
            for table in ('conversations', 'image_generations', 'tasks', 'credit_transactions'):
                cur.execute(f"INSERT INTO {table}(user_id) VALUES (%s)", (uid,))
            cur.execute("INSERT INTO admin_action_logs(target_user_id) VALUES (%s)", (uid,))
            cur.execute("INSERT INTO credits_history(user_id,change_amount) VALUES (%s,1)", (uid,))
        org = str(uuid.uuid4())
        for uid in [keep_uid, *drop_uids]:
            cur.execute("INSERT INTO org_members(user_id,org_id) VALUES (%s,%s)", (uid, org))
        conn.commit()

        # 调合并 RPC
        cur.execute(
            "SELECT merge_wecom_duplicate_users(%s, %s::uuid[]);",
            (keep_uid, drop_uids),
        )
        result = cur.fetchone()[0]
        conn.commit()

        # 验证
        assert result["dropped"] == 2
        assert result["merged_credits"] == 60 + 70  # 60 + 70 = 130

        # keep 的积分: 50 (原) + 130 (合并) = 180
        cur.execute("SELECT credits FROM users WHERE id = %s;", (keep_uid,))
        assert cur.fetchone()[0] == 180, "积分累加错误"

        # drop_uids 应被删除
        cur.execute(
            "SELECT count(*) FROM users WHERE id = ANY(%s::uuid[]);",
            (drop_uids,),
        )
        assert cur.fetchone()[0] == 0, "drop_uids 应已删除"
        for table in ('conversations', 'image_generations', 'tasks', 'credit_transactions', 'credits_history'):
            cur.execute(f"SELECT count(*) FROM {table} WHERE user_id=%s", (keep_uid,))
            assert cur.fetchone()[0] == 2, table
        cur.execute("SELECT count(*) FROM admin_action_logs WHERE target_user_id=%s", (keep_uid,))
        assert cur.fetchone()[0] == 2
        cur.execute("SELECT count(*) FROM org_members WHERE user_id=%s AND org_id=%s", (keep_uid, org))
        assert cur.fetchone()[0] == 1

    finally:
        conn.rollback()
        for table in ('conversations', 'image_generations', 'tasks', 'credit_transactions', 'org_members'):
            cur.execute(f"DELETE FROM {table} WHERE user_id=ANY(%s::uuid[])", ([keep_uid, *drop_uids],))
        cur.execute("DELETE FROM admin_action_logs WHERE target_user_id=ANY(%s::uuid[])", ([keep_uid, *drop_uids],))
        # 清理 keep
        if keep_uid:
            cur.execute("DELETE FROM users WHERE id = %s;", (keep_uid,))
        # 清理 drop（如果合并失败留下）
        if drop_uids:
            cur.execute("DELETE FROM users WHERE id = ANY(%s::uuid[]);", (drop_uids,))
        conn.commit()
        conn.close()
