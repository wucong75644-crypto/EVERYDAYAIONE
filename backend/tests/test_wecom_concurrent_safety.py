"""
企微用户创建并发安全集成测试（连真实生产库 / staging）

验证 migration 116 三件套（唯一索引 + advisory lock + 单事务）能在真实并发下
保证同一 wecom_userid 不会创建多个 user 行。

启用条件：同时设置 RUN_EXTERNAL_TESTS=1 和 DATABASE_URL。
清理：使用一个独立的虚拟 wecom_userid 前缀 (concurrent_test_xxx)，
     测试结束后 DELETE 所有匹配数据，保证可重复执行不污染生产。
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path

import pytest

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))


pytestmark = [
    pytest.mark.external,
    pytest.mark.skipif(
        os.environ.get("RUN_EXTERNAL_TESTS") != "1"
        or not os.environ.get("DATABASE_URL"),
        reason="需要 RUN_EXTERNAL_TESTS=1 和 DATABASE_URL",
    ),
]


def _get_pg_conn():
    import psycopg2
    return psycopg2.connect(os.environ["DATABASE_URL"], connect_timeout=15)


def _cleanup(test_wecom_userid: str, test_corp_id: str) -> None:
    """清理测试创建的所有数据"""
    conn = _get_pg_conn()
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


def test_concurrent_rpc_creates_only_one_user():
    """
    100 个并发 RPC 调用同一个 (wecom_userid, corp_id) →
    应只创建 1 个 user + 1 个 mapping。
    """
    test_wecom_userid = f"concurrent_test_{uuid.uuid4().hex[:8]}"
    test_corp_id = "test_corp_concurrent"

    try:
        # 用 50 个并发线程跑 RPC
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def _call_rpc():
            conn = _get_pg_conn()
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
        conn = _get_pg_conn()
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
        _cleanup(test_wecom_userid, test_corp_id)


def test_merge_rpc_migrates_all_refs():
    """
    合并 RPC：drop_uids 的 credits/conversations/tasks 等都迁到 keep_uid，
    drop_uids 被删除。
    """
    conn = _get_pg_conn()
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

    finally:
        # 清理 keep
        if keep_uid:
            cur.execute("DELETE FROM users WHERE id = %s;", (keep_uid,))
        # 清理 drop（如果合并失败留下）
        if drop_uids:
            cur.execute("DELETE FROM users WHERE id = ANY(%s::uuid[]);", (drop_uids,))
        conn.commit()
        conn.close()
