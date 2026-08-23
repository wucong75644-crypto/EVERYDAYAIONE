"""工具 invocation ledger 在隔离 PostgreSQL 上的恢复语义验证。

只接受显式提供的隔离数据库连接串，不读取项目生产环境文件：

    RUN_CONVERSATION_ACTOR_DB_TEST=1 \
    CONVERSATION_ACTOR_TEST_DATABASE_URL='postgresql://...' \
    pytest -q backend/tests/test_tool_invocation_uncertain_integration.py
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


def _jsonb(value):
    from psycopg.types.json import Jsonb

    return Jsonb(value)


def _create_running_task(cur):
    user_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    task_id = uuid.uuid4()
    turn_id = uuid.uuid4()
    token = uuid.uuid4()

    cur.execute(
        "INSERT INTO users (id, nickname) VALUES (%s, %s)",
        (user_id, f"tool_uncertain_test_{user_id.hex[:8]}"),
    )
    cur.execute(
        """
        INSERT INTO conversations (id, user_id, org_id, title)
        VALUES (%s, %s, NULL, %s)
        """,
        (conversation_id, user_id, "tool uncertain integration test"),
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
    return task_id, conversation_id, turn_id, token


def _rpc(cur, name, params):
    cur.execute(
        f"SELECT public.{name}({', '.join(['%s'] * len(params))})",
        params,
    )
    return cur.fetchone()[0]


def test_stale_running_is_uncertain_and_success_is_replayed():
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT to_regprocedure(
                    'public.mark_stale_tool_invocation_uncertain(uuid,uuid,text,uuid,integer)'
                )
                """
            )
            assert cur.fetchone()[0] is not None, "migration 239 尚未应用"

            task_id, conversation_id, turn_id, token = _create_running_task(cur)
            tool_call_id = "tool-stale-1"
            args_hash = "a" * 64
            cur.execute(
                """
                INSERT INTO tool_invocations (
                    task_id, conversation_id, turn_id, tool_call_id,
                    tool_name, args_hash, status, execution_token,
                    started_at, updated_at
                )
                VALUES (
                    %s, %s, %s, %s, 'erp_execute', %s, 'running', %s,
                    NOW() - INTERVAL '30 minutes',
                    NOW() - INTERVAL '30 minutes'
                )
                """,
                (task_id, conversation_id, turn_id, tool_call_id, args_hash, token),
            )

            stale = _rpc(
                cur,
                "mark_stale_tool_invocation_uncertain",
                (task_id, turn_id, tool_call_id, token, 900),
            )
            assert stale["outcome"] == "uncertain"

            replay_decision = _rpc(
                cur,
                "begin_tool_invocation",
                (
                    task_id,
                    conversation_id,
                    turn_id,
                    token,
                    tool_call_id,
                    "erp_execute",
                    args_hash,
                ),
            )
            assert replay_decision["outcome"] == "uncertain"

            wrong_owner = _rpc(
                cur,
                "mark_stale_tool_invocation_uncertain",
                (task_id, turn_id, tool_call_id, uuid.uuid4(), 900),
            )
            assert wrong_owner["outcome"] == "ownership_lost"

            replay_call_id = "tool-replay-1"
            replay_hash = "b" * 64
            execute = _rpc(
                cur,
                "begin_tool_invocation",
                (
                    task_id,
                    conversation_id,
                    turn_id,
                    token,
                    replay_call_id,
                    "erp_execute",
                    replay_hash,
                ),
            )
            assert execute["outcome"] == "execute"
            completed = _rpc(
                cur,
                "complete_tool_invocation",
                (
                    task_id,
                    turn_id,
                    replay_call_id,
                    token,
                    "succeeded",
                    _jsonb({"kind": "json", "value": {"ok": True}}),
                    "",
                ),
            )
            assert completed["outcome"] == "succeeded"
            replayed = _rpc(
                cur,
                "begin_tool_invocation",
                (
                    task_id,
                    conversation_id,
                    turn_id,
                    token,
                    replay_call_id,
                    "erp_execute",
                    replay_hash,
                ),
            )
            assert replayed == {
                "outcome": "replay",
                "result": {"kind": "json", "value": {"ok": True}},
            }
    finally:
        conn.rollback()
        conn.close()
