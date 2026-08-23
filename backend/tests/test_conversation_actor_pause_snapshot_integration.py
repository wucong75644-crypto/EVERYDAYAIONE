"""Conversation Actor PAUSE/ CANCEL RPC 集成测试。

只接受显式提供的隔离数据库连接串，不读取项目生产环境文件：

    RUN_CONVERSATION_ACTOR_DB_TEST=1 \
    CONVERSATION_ACTOR_TEST_DATABASE_URL='postgresql://...' \
    pytest -q backend/tests/test_conversation_actor_pause_snapshot_integration.py

测试在单个事务中创建数据并回滚，不应连接生产库或复用业务数据。
"""

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


def _fixture(cur):
    user_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    input_message_id = uuid.uuid4()
    output_message_id = uuid.uuid4()
    task_id = uuid.uuid4()
    turn_id = uuid.uuid4()
    execution_token = uuid.uuid4()

    cur.execute(
        """
        INSERT INTO users (id, nickname)
        VALUES (%s, %s)
        """,
        (user_id, f"actor_pause_test_{user_id.hex[:8]}"),
    )
    cur.execute(
        """
        INSERT INTO conversations (id, user_id, org_id, title)
        VALUES (%s, %s, NULL, %s)
        """,
        (conversation_id, user_id, "actor pause integration test"),
    )
    cur.execute(
        """
        INSERT INTO messages (
            id, conversation_id, org_id, role, content, status, turn_id
        )
        VALUES (%s, %s, NULL, 'user', %s, 'completed', %s)
        """,
            (
                input_message_id,
                conversation_id,
                '[{"type":"text","text":"继续"}]',
                turn_id,
            ),
    )
    cur.execute(
        """
        INSERT INTO messages (
            id, conversation_id, org_id, role, content, status,
            turn_id, reply_to_message_id
        )
        VALUES (%s, %s, NULL, 'assistant', %s, 'streaming', %s, %s)
        """,
        (
            output_message_id,
            conversation_id,
            '[{"type":"text","text":"已生成的前缀"}]',
            turn_id,
            input_message_id,
        ),
    )
    cur.execute(
        """
        INSERT INTO tasks (
            id, user_id, org_id, conversation_id, type, status,
            delivery_context, assistant_message_id, input_message_id,
            turn_id, execution_token, lease_expires_at,
            accumulated_content, accumulated_blocks
        )
        VALUES (
            %s, %s, NULL, %s, 'chat', 'running',
            '{"actor": true}'::jsonb, %s, %s, %s, %s,
            NOW() + INTERVAL '10 minutes', %s, %s::jsonb
        )
        """,
        (
            task_id,
            user_id,
            conversation_id,
            output_message_id,
            input_message_id,
            turn_id,
            execution_token,
            "已生成的前缀和最后一段",
            '[{"type":"text","text":"已生成的前缀"}]',
        ),
    )
    cur.execute(
        "UPDATE conversations SET active_serial_task_id = %s WHERE id = %s",
        (task_id, conversation_id),
    )
    return {
        "user_id": user_id,
        "conversation_id": conversation_id,
        "input_message_id": input_message_id,
        "output_message_id": output_message_id,
        "task_id": task_id,
        "turn_id": turn_id,
        "execution_token": execution_token,
    }


def _rpc(cur, task_id, token, user_id, control):
    if control == "cancel" and token is None:
        cur.execute(
            "SELECT public.cancel_paused_generation_turn(%s, %s, NULL)",
            (task_id, user_id),
        )
    elif token is None:
        cur.execute(
            """
            SELECT public.append_conversation_control_command(
                (SELECT conversation_id FROM tasks WHERE id = %s),
                %s, (SELECT turn_id FROM tasks WHERE id = %s), 'pause',
                %s, '{"reason":"user_pause"}'::jsonb
            )
            """,
            (task_id, task_id, task_id, f"pause:{task_id}"),
        )
        result = cur.fetchone()[0]
        return {**result, "outcome": "requested"} if result["outcome"] == "enqueued" else result
    else:
        cur.execute(
            "SELECT public.pause_generation_turn_owned(%s, %s, 'user_paused')",
            (task_id, token),
        )
    return cur.fetchone()[0]


def test_pause_defers_running_task_then_owner_saves_snapshot_and_cancel_wins():
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT to_regprocedure(
                    'public.pause_generation_turn_owned(uuid,uuid,text)'
                )
                """
            )
            assert cur.fetchone()[0] is not None, "migration 239 尚未应用"

            data = _fixture(cur)
            requested = _rpc(
                cur,
                data["task_id"],
                None,
                data["user_id"],
                "pause",
            )
            assert requested["outcome"] == "requested"

            cur.execute(
                "SELECT public.save_generation_checkpoint(%s, %s, 'before_model', %s::jsonb)",
                (data["task_id"], data["execution_token"], '{"messages":[]}' ),
            )
            assert cur.fetchone()[0]["outcome"] == "saved"

            cur.execute(
                "SELECT status, accumulated_content FROM tasks WHERE id = %s",
                (data["task_id"],),
            )
            assert cur.fetchone() == ("running", "已生成的前缀和最后一段")

            paused = _rpc(
                cur,
                data["task_id"],
                data["execution_token"],
                None,
                "pause",
            )
            assert paused["outcome"] == "paused"
            assert paused["snapshot_saved"] is True

            cur.execute(
                """
                SELECT status, execution_token, terminal_reason
                FROM tasks WHERE id = %s
                """,
                (data["task_id"],),
            )
            assert cur.fetchone() == ("paused", None, "user_paused")

            cur.execute(
                "SELECT status, content FROM messages WHERE id = %s",
                (data["output_message_id"],),
            )
            message_status, content = cur.fetchone()
            assert message_status == "interrupted"
            assert any(
                "最后一段" in str(block.get("text") or "")
                for block in content if isinstance(block, dict)
            )
            assert any(
                block.get("reason") == "user_pause"
                for block in content if isinstance(block, dict)
            )

            stale = _rpc(
                cur,
                data["task_id"],
                data["execution_token"],
                None,
                "pause",
            )
            assert stale["outcome"] == "already_paused"

            cancelled = _rpc(
                cur,
                data["task_id"],
                None,
                data["user_id"],
                "cancel",
            )
            assert cancelled["outcome"] == "cancelled"

            cur.execute(
                "SELECT status, terminal_reason FROM tasks WHERE id = %s",
                (data["task_id"],),
            )
            assert cur.fetchone() == ("cancelled", "user_cancelled")
    finally:
        conn.rollback()
        conn.close()


def test_wrong_owner_cannot_finalize_pause():
    conn = _connect()
    try:
        with conn.cursor() as cur:
            data = _fixture(cur)
            requested = _rpc(
                cur,
                data["task_id"],
                None,
                data["user_id"],
                "pause",
            )
            assert requested["outcome"] == "requested"

            wrong_owner = _rpc(
                cur,
                data["task_id"],
                uuid.uuid4(),
                None,
                "pause",
            )
            assert wrong_owner["outcome"] == "ownership_lost"

            cur.execute(
                "SELECT status, execution_token FROM tasks WHERE id = %s",
                (data["task_id"],),
            )
            assert cur.fetchone() == ("running", data["execution_token"])
    finally:
        conn.rollback()
        conn.close()
