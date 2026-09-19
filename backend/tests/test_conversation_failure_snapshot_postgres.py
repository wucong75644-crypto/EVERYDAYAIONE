"""Execute failure/rollback RPCs in a disposable PostgreSQL, never production."""

import getpass
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
import pytest

from tests.test_scheduled_task_draft_delete_integration import postgres_socket  # noqa: F401

MIGRATIONS = Path(__file__).parents[1] / "migrations"
FIX = MIGRATIONS / "258_conversation_failure_snapshot.sql"
ROLLBACK = MIGRATIONS / "rollback/258_conversation_failure_snapshot_rollback.sql"
SKILL = {"type": "skill_step", "step_id": "manual-skill", "status": "completed",
         "name": "参考图多方案提示词", "revision": "v1"}
ERROR = "模型响应超时，本次回答未完成，请重试。"


@pytest.fixture
def database(postgres_socket):
    with psycopg.connect(host=postgres_socket, dbname="postgres", user=getpass.getuser()) as conn:
        conn.execute("""
            CREATE ROLE everydayai NOSUPERUSER NOBYPASSRLS;
            CREATE TABLE conversations(id uuid PRIMARY KEY, org_id uuid,
                active_serial_task_id uuid, actor_updated_at timestamptz);
            CREATE TABLE messages(id uuid PRIMARY KEY, content jsonb, status text, is_error boolean);
            CREATE TABLE tasks(id uuid PRIMARY KEY, conversation_id uuid, org_id uuid,
                type text, delivery_context jsonb, execution_token uuid, status text,
                assistant_message_id uuid, accumulated_blocks jsonb, accumulated_content text,
                fail_code text, error_message text, completed_at timestamptz,
                lease_expires_at timestamptz, terminal_reason text);
            GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO everydayai;
        """)
        conn.execute(ROLLBACK.read_text())
        for _ in range(2):
            conn.execute(FIX.read_text())
        task, conv, org, message, token = [uuid4() for _ in range(5)]
        conn.execute("INSERT INTO conversations VALUES (%s,%s,%s,NULL)", (conv, org, task))
        conn.execute("INSERT INTO messages VALUES (%s,'[]','streaming',false)", (message,))
        conn.execute("""INSERT INTO tasks(id,conversation_id,org_id,type,delivery_context,
            execution_token,status,assistant_message_id,accumulated_blocks,accumulated_content)
            VALUES (%s,%s,%s,'chat','{"actor":true}',%s,'running',%s,%s,'前文后续')""",
            (task, conv, org, token, message, Jsonb([SKILL, {"type": "text", "text": "前文"}])))
        conn.execute("SET LOCAL ROLE everydayai")

        def fail(execution_token=token):
            return conn.execute("SELECT fail_generation_turn(%s,%s,'MODEL_TIMEOUT',%s)",
                                (task, execution_token, ERROR)).fetchone()[0]

        try:
            yield conn, fail, task, conv, message
        finally:
            conn.rollback()


def test_failure_preserves_skill_and_partial_without_duplication(database):
    conn, fail, task, conv, message = database
    assert fail()["outcome"] == "failed"
    assert fail()["outcome"] == "already_failed"
    content, status, is_error = conn.execute(
        "SELECT content,status,is_error FROM messages WHERE id=%s", (message,)).fetchone()
    assert content == [SKILL, {"type": "text", "text": "前文"},
                       {"type": "text", "text": "后续"}, {"type": "text", "text": "\n\n" + ERROR}]
    assert (status, is_error) == ("failed", True)
    assert conn.execute("SELECT status,fail_code FROM tasks WHERE id=%s", (task,)).fetchone() == ("failed", "MODEL_TIMEOUT")
    assert conn.execute("SELECT active_serial_task_id FROM conversations WHERE id=%s", (conv,)).fetchone() == (None,)


@pytest.mark.parametrize("blocks,text,expected", [
    ([], "", []), ([], "部分输出", [{"type": "text", "text": "部分输出"}]),
    ([{"type": "skill_step", "status": "failed", "reason": "当前无法启用。"}], "",
     [{"type": "skill_step", "status": "failed", "reason": "当前无法启用。"}]),
    ([{"type": "tool_step", "status": "running"}], "", [{"type": "tool_step", "status": "error"}]),
])
def test_empty_ordinary_skill_failure_and_running_tool_snapshots(database, blocks, text, expected):
    conn, fail, task, _, message = database
    conn.execute("UPDATE tasks SET accumulated_blocks=%s,accumulated_content=%s WHERE id=%s",
                 (Jsonb(blocks), text, task))
    assert fail()["outcome"] == "failed"
    content = conn.execute("SELECT content FROM messages WHERE id=%s", (message,)).fetchone()[0]
    assert content[:-1] == expected
    assert content[-1] == {"type": "text", "text": ("\n\n" if expected else "") + ERROR}


def test_stale_owner_cannot_publish_failure_snapshot(database):
    conn, fail, task, _, message = database
    assert fail(uuid4())["outcome"] == "ownership_lost"
    assert conn.execute("SELECT content,status FROM messages WHERE id=%s", (message,)).fetchone() == ([], "streaming")
    assert conn.execute("SELECT status FROM tasks WHERE id=%s", (task,)).fetchone() == ("running",)


def test_scope_mismatch_and_cancelled_task_remain_protected(database):
    conn, fail, task, _, message = database
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="ACTOR_FAIL_SCOPE_MISMATCH"):
        with conn.transaction():
            conn.execute("UPDATE tasks SET org_id=%s WHERE id=%s", (uuid4(), task))
            fail()
    conn.execute("UPDATE tasks SET status='cancelled' WHERE id=%s", (task,))
    assert fail() == {"outcome": "terminal", "status": "cancelled"}
    assert conn.execute("SELECT content FROM messages WHERE id=%s", (message,)).fetchone()[0] == []


def test_rollback_restores_old_function_without_deleting_saved_messages(database):
    conn, fail, _, _, message = database
    fail()
    before = conn.execute("SELECT content FROM messages WHERE id=%s", (message,)).fetchone()[0]
    conn.execute("RESET ROLE")
    conn.execute(ROLLBACK.read_text())
    definition = conn.execute("SELECT pg_get_functiondef('fail_generation_turn(uuid,uuid,text,text)'::regprocedure)").fetchone()[0]
    assert "v_snapshot" not in definition
    assert conn.execute("SELECT content FROM messages WHERE id=%s", (message,)).fetchone()[0] == before
