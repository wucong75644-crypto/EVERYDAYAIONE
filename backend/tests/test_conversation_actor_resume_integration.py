"""RESUME RPC 在隔离 PostgreSQL 上的状态转换验证。"""

from __future__ import annotations

import os
import uuid

import pytest


_DATABASE_URL = os.environ.get("CONVERSATION_ACTOR_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_CONVERSATION_ACTOR_DB_TEST") != "1"
    or not _DATABASE_URL,
    reason=(
        "需要显式提供 RUN_CONVERSATION_ACTOR_DB_TEST=1 和隔离的 "
        "CONVERSATION_ACTOR_TEST_DATABASE_URL"
    ),
)


def _connect():
    import psycopg

    return psycopg.connect(_DATABASE_URL, autocommit=False)


def test_resume_requeues_paused_task_with_checkpoint():
    conn = _connect()
    try:
        with conn.cursor() as cur:
            user_id = uuid.uuid4()
            conversation_id = uuid.uuid4()
            task_id = uuid.uuid4()
            turn_id = uuid.uuid4()
            old_token = uuid.uuid4()

            cur.execute(
                "INSERT INTO users (id, nickname) VALUES (%s, %s)",
                (user_id, f"resume_rpc_test_{user_id.hex[:8]}"),
            )
            cur.execute(
                """
                INSERT INTO conversations (id, user_id, org_id, title)
                VALUES (%s, %s, NULL, %s)
                """,
                (conversation_id, user_id, "resume rpc integration test"),
            )
            cur.execute(
                """
                INSERT INTO tasks (
                    id, user_id, conversation_id, type, status,
                    delivery_context, turn_id, execution_token,
                    lease_expires_at, execution_mode, execution_attempt
                )
                VALUES (
                    %s, %s, %s, 'chat', 'running', '{"actor": true}'::jsonb,
                    %s, %s, NOW() + INTERVAL '10 minutes', 'serial', 1
                )
                """,
                (task_id, user_id, conversation_id, turn_id, old_token),
            )
            cur.execute(
                """
                SELECT public.save_generation_checkpoint(
                    %s, %s, 'before_model', %s::jsonb
                )
                """,
                (
                    task_id,
                    old_token,
                    '{"messages": [{"role": "user", "content": "冻结"}], '
                    '"content_blocks": []}',
                ),
            )
            cur.execute(
                "UPDATE tasks SET status = 'paused', completed_at = NOW() WHERE id = %s",
                (task_id,),
            )
            cur.execute(
                "UPDATE conversation_turn_checkpoints SET status = 'paused' WHERE task_id = %s",
                (task_id,),
            )

            cur.execute(
                "SELECT public.resume_paused_generation_turn(%s, %s, NULL)",
                (task_id, user_id),
            )
            result = cur.fetchone()[0]
            assert result["outcome"] == "enqueued"
            assert result["checkpoint_version"] == 1

            cur.execute(
                "SELECT status, execution_token, completed_at FROM tasks WHERE id = %s",
                (task_id,),
            )
            status, execution_token, completed_at = cur.fetchone()
            assert status == "pending"
            assert execution_token is None
            assert completed_at is None

            cur.execute(
                """
                SELECT public.save_generation_checkpoint(
                    %s, %s, 'after_tool', %s::jsonb
                )
                """,
                (
                    task_id,
                    old_token,
                    '{"messages": [], "content_blocks": []}',
                ),
            )
            assert cur.fetchone()[0]["outcome"] == "terminal"
    finally:
        conn.rollback()
        conn.close()
