"""ReplayCheckpoint RPC 在隔离 PostgreSQL 上的集成验证。"""

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


def _create_running_task(cur):
    user_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    task_id = uuid.uuid4()
    turn_id = uuid.uuid4()
    token = uuid.uuid4()

    cur.execute(
        "INSERT INTO users (id, nickname) VALUES (%s, %s)",
        (user_id, f"replay_checkpoint_test_{user_id.hex[:8]}"),
    )
    cur.execute(
        """
        INSERT INTO conversations (id, user_id, org_id, title)
        VALUES (%s, %s, NULL, %s)
        """,
        (conversation_id, user_id, "replay checkpoint integration test"),
    )
    cur.execute(
        """
        INSERT INTO tasks (
            id, user_id, conversation_id, type, status,
            delivery_context, turn_id, execution_token,
            lease_expires_at
        )
        VALUES (
            %s, %s, %s, 'chat', 'running',
            '{"actor": true}'::jsonb, %s, %s,
            NOW() + INTERVAL '10 minutes'
        )
        """,
        (task_id, user_id, conversation_id, turn_id, token),
    )
    return task_id, user_id, conversation_id, turn_id, token


def _write(cur, task_id, token, boundary, payload):
    cur.execute(
        """
        SELECT public.save_generation_checkpoint(
            %s, %s, %s, %s::jsonb
        )
        """,
        (task_id, token, boundary, payload),
    )
    return cur.fetchone()[0]


def test_checkpoint_write_replay_read_and_fencing():
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT to_regprocedure(
                    'public.save_generation_checkpoint(uuid,uuid,text,jsonb)'
                )
                """
            )
            assert cur.fetchone()[0] is not None, "migration 239 尚未应用"

            task_id, user_id, conversation_id, turn_id, token = _create_running_task(cur)
            payload = '{"messages":[{"role":"assistant","content":"基线"}]}'

            written = _write(
                cur, task_id, token, "before_model", payload
            )
            assert written["outcome"] == "saved"

            repeated = _write(
                cur, task_id, token, "before_model", payload
            )
            assert repeated["outcome"] == "saved"
            assert repeated["version"] == written["version"] + 1

            cur.execute(
                """
                SELECT public.load_generation_checkpoint(%s, %s)
                """,
                (task_id, token),
            )
            found = cur.fetchone()[0]
            assert found["outcome"] == "loaded"
            assert found["safe_point"] == "before_model"
            assert found["state"] == {
                "messages": [{"role": "assistant", "content": "基线"}]
            }

            wrong_owner = _write(
                cur,
                task_id,
                uuid.uuid4(),
                "after_tool",
                '{"tool_result":"must not write"}',
            )
            assert wrong_owner["outcome"] == "ownership_lost"

            cur.execute(
                "UPDATE tasks SET status = 'paused' WHERE id = %s",
                (task_id,),
            )
            cur.execute(
                """
                SELECT public.load_generation_checkpoint(%s, %s)
                """,
                (task_id, token),
            )
            assert cur.fetchone()[0]["outcome"] == "terminal"
    finally:
        conn.rollback()
        conn.close()
