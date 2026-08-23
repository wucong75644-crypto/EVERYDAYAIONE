"""Conversation Actor lease 过期重领与旧 token fencing 的 PostgreSQL 集成验证。"""

from __future__ import annotations

import os
import uuid
from concurrent.futures import ThreadPoolExecutor

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


def _create_pending_task(cur):
    user_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    input_message_id = uuid.uuid4()
    output_message_id = uuid.uuid4()
    task_id = uuid.uuid4()
    turn_id = uuid.uuid4()

    cur.execute(
        "INSERT INTO users (id, nickname) VALUES (%s, %s)",
        (user_id, f"actor_recovery_test_{user_id.hex[:8]}"),
    )
    cur.execute(
        """
        INSERT INTO conversations (id, user_id, org_id, title)
        VALUES (%s, %s, NULL, %s)
        """,
        (conversation_id, user_id, "actor recovery integration test"),
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
            "recover",
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
            turn_id, execution_mode
        )
        VALUES (
            %s, %s, NULL, %s, 'chat', 'pending',
            '{"actor": true}'::jsonb, %s, %s, %s, 'serial'
        )
        """,
        (
            task_id,
            user_id,
            conversation_id,
            output_message_id,
            input_message_id,
            turn_id,
        ),
    )
    return conversation_id, task_id, output_message_id


def _rpc(cur, name, params):
    cur.execute(
        f"SELECT public.{name}({', '.join(['%s'] * len(params))})",
        params,
    )
    return cur.fetchone()[0]


def _claim_once(conversation_id):
    conn = _connect()
    try:
        with conn.cursor() as cur:
            result = _rpc(
                cur,
                "claim_next_serial_generation_turn",
                (conversation_id, 90, 3),
            )
            conn.commit()
            return result
    finally:
        conn.close()


def test_expired_lease_is_reclaimed_and_old_token_is_fenced():
    owner = _connect()
    contender = _connect()
    try:
        with owner.cursor() as cur:
            conversation_id, task_id, output_message_id = _create_pending_task(cur)
            first = _rpc(
                cur,
                "claim_next_serial_generation_turn",
                (conversation_id, 90, 3),
            )
            assert first["outcome"] == "claimed"
            old_token = uuid.UUID(first["execution_token"])
            owner.commit()

        with contender.cursor() as cur:
            busy = _rpc(
                cur,
                "claim_next_serial_generation_turn",
                (conversation_id, 90, 3),
            )
            assert busy["outcome"] == "busy"
            contender.rollback()

        with owner.cursor() as cur:
            cur.execute(
                "UPDATE tasks SET lease_expires_at = NOW() - INTERVAL '1 second' WHERE id = %s",
                (task_id,),
            )
            owner.commit()

        with contender.cursor() as cur:
            second = _rpc(
                cur,
                "claim_next_serial_generation_turn",
                (conversation_id, 90, 3),
            )
            assert second["outcome"] == "claimed"
            new_token = uuid.UUID(second["execution_token"])
            assert new_token != old_token
            assert second["execution_attempt"] == 2

            old_renew = _rpc(cur, "renew_generation_lease", (task_id, old_token, 90))
            assert old_renew["outcome"] == "ownership_lost"

            old_commit = _rpc(
                cur,
                "commit_generation_turn",
                (
                    task_id,
                    old_token,
                    output_message_id,
                    _jsonb([{"type": "text", "text": "旧 worker"}]),
                    _jsonb({}),
                    0,
                    None,
                ),
            )
            assert old_commit["outcome"] == "ownership_lost"

            current_renew = _rpc(cur, "renew_generation_lease", (task_id, new_token, 90))
            assert current_renew["outcome"] == "renewed"
    finally:
        owner.rollback()
        contender.rollback()
        owner.close()
        contender.close()


def test_two_postgres_claimers_only_one_enters_execution():
    setup = _connect()
    try:
        with setup.cursor() as cur:
            conversation_id, _task_id, _output_message_id = _create_pending_task(cur)
            setup.commit()
    finally:
        setup.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(_claim_once, [conversation_id, conversation_id]))

    assert sorted(result["outcome"] for result in results) == ["busy", "claimed"]
