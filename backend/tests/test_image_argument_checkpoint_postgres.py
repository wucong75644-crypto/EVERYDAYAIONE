"""Real checkpoint/JSONB contracts in an explicitly supplied temporary cluster.

This minimal schema and test RLS are not a production permission/schema clone.
No application database URL or credential file is read.
"""
import json
import os
from pathlib import Path
import re
import uuid

import pytest

DSN = os.environ.get("IMAGE_ARGUMENT_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="需要显式临时 PostgreSQL Unix socket 测试库")


@pytest.fixture(scope="module")
def database():
    import psycopg
    from psycopg.conninfo import conninfo_to_dict
    args = conninfo_to_dict(DSN)
    assert args.get("host", "").startswith("/private/tmp/image-argument-pg.")
    assert args.get("dbname", "").startswith("image_argument_")
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("""
            CREATE ROLE image_argument_runtime NOLOGIN;
            CREATE TABLE tasks(id uuid PRIMARY KEY, user_id uuid, conversation_id uuid,
                turn_id uuid, type text, status text, execution_token uuid,
                delivery_context jsonb, lease_expires_at timestamptz, result jsonb, completed_at timestamptz);
            CREATE TABLE conversation_turn_checkpoints(task_id uuid PRIMARY KEY,
                conversation_id uuid, turn_id uuid, version bigint, safe_point text,
                state jsonb, status text, updated_at timestamptz DEFAULT now());
            ALTER TABLE tasks ENABLE ROW LEVEL SECURITY;
            ALTER TABLE tasks FORCE ROW LEVEL SECURITY;
            CREATE POLICY test_actor ON tasks TO image_argument_runtime
                USING(user_id = current_setting('image_argument_test.actor_id')::uuid);
            ALTER TABLE conversation_turn_checkpoints ENABLE ROW LEVEL SECURITY;
            ALTER TABLE conversation_turn_checkpoints FORCE ROW LEVEL SECURITY;
            CREATE POLICY test_actor ON conversation_turn_checkpoints TO image_argument_runtime
                USING(EXISTS(SELECT 1 FROM tasks WHERE id=task_id));
            GRANT USAGE ON SCHEMA public TO image_argument_runtime;
            GRANT SELECT, INSERT, UPDATE ON tasks, conversation_turn_checkpoints TO image_argument_runtime;
        """)
        migration = (Path(__file__).parents[1] / "migrations/239_conversation_actor_production_contract_compat.sql").read_text()
        for name in ("save_generation_checkpoint", "load_generation_checkpoint"):
            match = re.search(rf"CREATE OR REPLACE FUNCTION public\.{name}\(.*?\n\$\$;", migration, re.S)
            assert match
            conn.execute(match[0])
            conn.execute(f"REVOKE ALL ON FUNCTION public.{name}(uuid,uuid" + (",text,jsonb)" if name.startswith("save") else ")") + " FROM PUBLIC")
            conn.execute(f"GRANT EXECUTE ON FUNCTION public.{name}(uuid,uuid" + (",text,jsonb)" if name.startswith("save") else ")") + " TO image_argument_runtime")
    return DSN


def task(conn):
    ids = [uuid.uuid4() for _ in range(5)]
    conn.execute("INSERT INTO tasks(id,user_id,conversation_id,turn_id,execution_token,type,status,delivery_context,lease_expires_at) "
                 "VALUES(%s,%s,%s,%s,%s,'chat','running','{\"actor\":true}',now()+interval '10 minutes')", ids)
    return ids[0], ids[1], ids[4]


def runtime(conn, actor):
    conn.execute("SET LOCAL ROLE image_argument_runtime")
    conn.execute("SELECT set_config('image_argument_test.actor_id',%s,true)", (str(actor),))


def save(conn, task_id, token):
    from psycopg.types.json import Jsonb
    snapshot = {"content_blocks": [{"image_argument_validation": {"repair": {
        "used": True, "dispatch_reserved": True,
        "dispatch_calls": [{"id": "actor-call:turn:round:1:index:0", "arguments_sha256": "frozen"}],
        "prompt_tokens": 12, "completion_tokens": 3, "estimated_chat_credits": 0.25,
    }}}]}
    return conn.execute("SELECT save_generation_checkpoint(%s,%s,'before_tool',%s)",
                        (task_id, token, Jsonb(snapshot))).fetchone()[0]


def test_before_tool_commit_reload_and_stale_owner_fence(database):
    import psycopg
    with psycopg.connect(database) as conn:
        task_id, actor, token = task(conn)
        runtime(conn, actor)
        assert save(conn, task_id, token)["outcome"] == "saved"
    # A new connection observes the committed reservation, not an in-process double.
    with psycopg.connect(database) as conn:
        runtime(conn, actor)
        row = conn.execute("SELECT load_generation_checkpoint(%s,%s)", (task_id, token)).fetchone()[0]
        assert row["safe_point"] == "before_tool"
        repair = row["state"]["content_blocks"][0]["image_argument_validation"]["repair"]
        assert repair["dispatch_reserved"] and repair["estimated_chat_credits"] == 0.25
        assert save(conn, task_id, uuid.uuid4())["outcome"] == "ownership_lost"
        assert conn.execute("SELECT version FROM conversation_turn_checkpoints WHERE task_id=%s", (task_id,)).fetchone()[0] == 1


def test_test_rls_rejects_other_actor_and_expired_lease(database):
    import psycopg
    with psycopg.connect(database) as conn:
        task_id, actor, token = task(conn)
    with psycopg.connect(database) as conn:
        runtime(conn, uuid.uuid4())
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="ACTOR_CHECKPOINT_SCOPE_MISMATCH"):
            save(conn, task_id, token)
        conn.rollback()
    with psycopg.connect(database) as conn:
        runtime(conn, actor)
        conn.execute("UPDATE tasks SET lease_expires_at=now()-interval '1 second' WHERE id=%s", (task_id,))
        assert save(conn, task_id, token)["outcome"] == "lease_expired"
        assert conn.execute("SELECT count(*) FROM conversation_turn_checkpoints WHERE task_id=%s", (task_id,)).fetchone()[0] == 0


def test_admin_projection_reads_only_metrics_jsonb(database):
    import psycopg
    from psycopg.types.json import Jsonb
    from services.handlers.chat.image_argument_correction import summarize_metrics
    with psycopg.connect(database) as conn:
        task_id, actor, _ = task(conn)
        runtime(conn, actor)
        conn.execute("UPDATE tasks SET result=%s WHERE id=%s", (Jsonb({"private": "secret input", "usage": {
            "image_argument_metrics": {"version": 1, "initial_calls": 2, "invalid_initial_calls": 1,
                "corrected_calls": 1, "correction_rounds": 1, "estimated_chat_credits": 0.25}}}), task_id))
        row = conn.execute("SELECT result->'usage'->'image_argument_metrics' AS metrics FROM tasks WHERE id=%s", (task_id,)).fetchone()
        summary = summarize_metrics([{"metrics": row[0]}])
        assert summary["initial_error_rate"] == 0.5 and summary["estimated_chat_credits"] == 0.25
        assert "secret input" not in json.dumps(summary)
