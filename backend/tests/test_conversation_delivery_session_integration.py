"""Conversation Delivery Session 的 PostgreSQL 事务级集成验证。"""

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


def _jsonb(value):
    from psycopg.types.json import Jsonb

    return Jsonb(value)


def _rpc(cur, name, params):
    cur.execute(
        f"SELECT public.{name}({', '.join(['%s'] * len(params))})",
        params,
    )
    return cur.fetchone()[0]


def _create_running_task(cur):
    user_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    input_message_id = uuid.uuid4()
    output_message_id = uuid.uuid4()
    task_id = uuid.uuid4()
    turn_id = uuid.uuid4()
    execution_token = uuid.uuid4()

    cur.execute(
        "INSERT INTO users (id, nickname) VALUES (%s, %s)",
        (user_id, f"delivery_session_test_{user_id.hex[:8]}"),
    )
    cur.execute(
        """
        INSERT INTO conversations (id, user_id, org_id, title)
        VALUES (%s, %s, NULL, %s)
        """,
        (conversation_id, user_id, "delivery session integration test"),
    )
    cur.execute(
        """
        INSERT INTO messages (id, conversation_id, org_id, role, content, turn_id)
        VALUES (%s, %s, NULL, 'user', %s, %s),
               (%s, %s, NULL, 'assistant', %s, %s)
        """,
        (
            input_message_id,
            conversation_id,
            "交付会话测试",
            turn_id,
            output_message_id,
            conversation_id,
            "",
            turn_id,
        ),
    )
    cur.execute(
        """
        INSERT INTO tasks (
            id, user_id, org_id, conversation_id, type, status,
            delivery_context, assistant_message_id, input_message_id,
            turn_id, execution_mode, execution_token, execution_attempt,
            accumulated_content, accumulated_blocks, lease_expires_at
        )
        VALUES (
            %s, %s, NULL, %s, 'chat', 'running', '{"actor": true}'::jsonb,
            %s, %s, %s, 'serial', %s, 1, %s, %s::jsonb,
            NOW() + INTERVAL '10 minutes'
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
            "已有快照",
            '[{"type": "text", "text": "已有快照"}]',
        ),
    )
    return task_id, user_id, output_message_id, execution_token


def test_delivery_session_replays_snapshot_and_fences_old_attempt():
    conn = _connect()
    try:
        with conn.cursor() as cur:
            for signature in (
                "begin_conversation_delivery_session(uuid,uuid,integer,uuid)",
                "append_conversation_delivery_event(uuid,uuid,text,jsonb)",
                "save_conversation_delivery_snapshot(uuid,uuid,text,jsonb)",
                "read_conversation_delivery_state(uuid,uuid,bigint)",
            ):
                cur.execute("SELECT to_regprocedure(%s)", (f"public.{signature}",))
                assert cur.fetchone()[0] is not None, f"缺少 RPC: {signature}"

            task_id, user_id, message_id, old_token = _create_running_task(cur)

            started = _rpc(
                cur,
                "begin_conversation_delivery_session",
                (task_id, old_token, 1, message_id),
            )
            assert started["outcome"] == "started"
            stream_id = started["stream_id"]
            assert started["snapshot_content"] == "已有快照"

            first = _rpc(
                cur,
                "append_conversation_delivery_event",
                (task_id, old_token, "message_start", _jsonb({"model": "test"})),
            )
            second = _rpc(
                cur,
                "append_conversation_delivery_event",
                (task_id, old_token, "message_chunk", _jsonb({"chunk": "新增"})),
            )
            assert first["delivery_seq"] == 1
            assert second["delivery_seq"] == 2

            saved = _rpc(
                cur,
                "save_conversation_delivery_snapshot",
                (
                    task_id,
                    old_token,
                    "已有快照新增",
                    _jsonb([{"type": "text", "text": "已有快照新增"}]),
                ),
            )
            assert saved["outcome"] == "saved"
            assert saved["snapshot_seq"] == 2

            replay = _rpc(
                cur,
                "read_conversation_delivery_state",
                (task_id, user_id, 0),
            )
            assert replay["outcome"] == "found"
            assert replay["stream_id"] == stream_id
            assert replay["snapshot_seq"] == 2
            assert replay["events"] == []

            after_snapshot = _rpc(
                cur,
                "append_conversation_delivery_event",
                (task_id, old_token, "thinking_chunk", _jsonb({"chunk": "思考"})),
            )
            assert after_snapshot["delivery_seq"] == 3
            incremental = _rpc(
                cur,
                "read_conversation_delivery_state",
                (task_id, user_id, 2),
            )
            assert [event["delivery_seq"] for event in incremental["events"]] == [3]

            cur.execute(
                "UPDATE tasks SET status = 'paused' WHERE id = %s",
                (task_id,),
            )
            paused = _rpc(
                cur,
                "read_conversation_delivery_state",
                (task_id, user_id, 2),
            )
            assert paused["delivery_status"] == "paused"

            current_token = uuid.uuid4()
            cur.execute(
                "UPDATE tasks SET status = 'running', execution_token = %s, "
                "execution_attempt = 2 WHERE id = %s",
                (current_token, task_id),
            )

            old_append = _rpc(
                cur,
                "append_conversation_delivery_event",
                (task_id, old_token, "message_chunk", _jsonb({"chunk": "旧"})),
            )
            assert old_append["outcome"] == "ownership_lost"

            restarted = _rpc(
                cur,
                "begin_conversation_delivery_session",
                (task_id, current_token, 2, message_id),
            )
            assert restarted["outcome"] == "started"
            assert restarted["stream_id"] != stream_id

            cur.execute(
                "SELECT COUNT(*) FROM conversation_delivery_events WHERE task_id = %s",
                (task_id,),
            )
            assert cur.fetchone()[0] == 0
    finally:
        conn.rollback()
        conn.close()
