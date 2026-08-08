"""AR-18-A1.1 contract on a disposable PostgreSQL bound to 127.0.0.1."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
import pytest


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
M210 = ROOT / "migrations/210_worker_orphan_task_recovery_capability.sql"
MIGRATION = ROOT / "migrations/227_21_agent_runtime_legacy_lifecycle_fence.sql"
ROLLBACK = ROOT / "migrations/rollback/227_21_agent_runtime_legacy_lifecycle_fence_rollback.sql"
PG_BIN = Path(os.getenv("AGENT_RUNTIME_PG_BIN_DIR", "/opt/homebrew/bin"))


def _tool(name: str) -> str:
    return str(PG_BIN / name)


def _run(command: list[str], *, capture: bool = True) -> None:
    subprocess.run(
        command, check=True, capture_output=capture, text=capture, timeout=30,
    )


def _port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


BOOTSTRAP = r"""
CREATE EXTENSION pgcrypto;
CREATE ROLE everydayai_owner NOLOGIN;
CREATE ROLE everydayai_worker LOGIN NOSUPERUSER NOBYPASSRLS;
CREATE ROLE everydayai_runtime LOGIN;
CREATE ROLE everydayai_wecom_runtime LOGIN;
CREATE ROLE everydayai_sync LOGIN;
CREATE ROLE everydayai LOGIN;
CREATE ROLE everydayai_agent_runtime_worker LOGIN;
CREATE ROLE everydayai_agent_model_gateway LOGIN;
CREATE ROLE everydayai_projection_worker LOGIN;
CREATE ROLE everydayai_authorization_worker LOGIN;
CREATE ROLE everydayai_sandbox_worker LOGIN;
GRANT everydayai_owner TO CURRENT_USER;
GRANT ALL ON SCHEMA public TO everydayai_owner;
GRANT USAGE ON SCHEMA public TO everydayai_worker;
SET ROLE everydayai_owner;
CREATE TABLE users(id UUID PRIMARY KEY, credits INTEGER, updated_at TIMESTAMPTZ);
CREATE TABLE conversations(id UUID PRIMARY KEY, user_id UUID, org_id UUID,
  last_message_preview TEXT);
CREATE TABLE messages(id UUID PRIMARY KEY, conversation_id UUID, org_id UUID,
  role TEXT, content TEXT, status TEXT, credits_cost INTEGER, is_error BOOLEAN,
  task_id TEXT, generation_params JSONB);
CREATE TABLE credit_transactions(id UUID PRIMARY KEY, user_id UUID, org_id UUID,
  amount INTEGER, status TEXT, confirmed_at TIMESTAMPTZ);
CREATE TABLE credits_history(id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID, change_amount INTEGER, balance_after INTEGER, change_type TEXT,
  description TEXT, org_id UUID);
CREATE TABLE tasks(
  id UUID PRIMARY KEY, user_id UUID NOT NULL, org_id UUID,
  conversation_id UUID, type TEXT NOT NULL, status TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), started_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ, external_task_id TEXT, placeholder_message_id TEXT,
  model_id TEXT, client_task_id TEXT, accumulated_content TEXT,
  accumulated_blocks JSONB, credit_transaction_id UUID,
  delivery_context JSONB NOT NULL DEFAULT '{}', execution_token UUID,
  lease_expires_at TIMESTAMPTZ, execution_attempt INTEGER NOT NULL DEFAULT 0,
  terminal_reason TEXT, error_message TEXT);
ALTER TABLE tasks ENABLE ROW LEVEL SECURITY;
ALTER TABLE tasks FORCE ROW LEVEL SECURITY;
CREATE POLICY tasks_owner_all ON tasks TO everydayai_owner
  USING (TRUE) WITH CHECK (TRUE);
CREATE FUNCTION atomic_refund_credits(UUID) RETURNS JSONB LANGUAGE sql
  SET search_path=pg_catalog,public AS $$ SELECT '{"refunded":false}'::JSONB $$;
RESET ROLE;
"""


@pytest.fixture
def database() -> tuple[str, Path]:
    if os.getenv("RUN_AR18_A11_DB_TEST") != "1":
        pytest.skip("RUN_AR18_A11_DB_TEST=1 required")
    for name in ("initdb", "pg_ctl"):
        if not Path(_tool(name)).is_file():
            pytest.skip(f"PostgreSQL tool missing: {name}")
    port = _port()
    data = Path(tempfile.mkdtemp(prefix="ar18-a11-pg-", dir="/private/tmp"))
    url = f"postgresql://postgres@127.0.0.1:{port}/postgres"
    try:
        _run([_tool("initdb"), "-D", str(data), "-U", "postgres",
              "--auth-host=trust", "--auth-local=trust"])
        _run([_tool("pg_ctl"), "-D", str(data), "-o",
              f"-p {port} -k {data}", "-l", str(data / "postgres.log"),
              "-w", "start"], capture=False)
        with psycopg.connect(url) as conn:
            conn.execute(BOOTSTRAP)
            conn.execute(M210.read_text(encoding="utf-8"))
            conn.execute(MIGRATION.read_text(encoding="utf-8"))
            conn.commit()
        yield url, data
    finally:
        if (data / "postmaster.pid").exists():
            _run([_tool("pg_ctl"), "-D", str(data), "-m", "fast", "-w", "stop"])
        shutil.rmtree(data, ignore_errors=True)


def _worker_url(url: str) -> str:
    return url.replace("postgres@", "everydayai_worker@")


def _scope(conn: psycopg.Connection) -> None:
    conn.execute(
        "SELECT set_config('app.access_kind','worker',true),"
        "set_config('app.actor_user_id','',true),"
        "set_config('app.org_id','',true),"
        "set_config('app.request_id','ar18-a11',true)"
    )


def _insert_task(conn: psycopg.Connection, *, delivery: dict, status: str = "running"):
    task_id, user_id, conversation_id, message_id, token = (
        uuid4(), uuid4(), uuid4(), uuid4(), uuid4()
    )
    conn.execute("SET LOCAL ROLE everydayai_owner")
    conn.execute("INSERT INTO users VALUES (%s,100,NOW())", (user_id,))
    conn.execute(
        "INSERT INTO conversations(id,user_id) VALUES (%s,%s)",
        (conversation_id, user_id),
    )
    conn.execute(
        "INSERT INTO tasks(id,user_id,conversation_id,type,status,started_at,"
        "placeholder_message_id,accumulated_content,delivery_context,"
        "execution_token,lease_expires_at) VALUES "
        "(%s,%s,%s,'chat',%s,NOW()-INTERVAL '20 minutes',%s,'partial',%s,%s,"
        "NOW()+INTERVAL '5 minutes')",
        (task_id, user_id, conversation_id, status, str(message_id),
         Jsonb(delivery), token),
    )
    return task_id, token


def _expect_forbidden(conn: psycopg.Connection, query: str, params: tuple, marker: str):
    _scope(conn)
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match=marker):
        conn.execute(query, params).fetchone()
    conn.rollback()


def _verify_acl_and_rollback_cycle(url: str) -> None:
    with psycopg.connect(url) as admin:
        row_security = admin.execute(
            "SELECT relrowsecurity,relforcerowsecurity FROM pg_class "
            "WHERE oid='public.tasks'::regclass"
        ).fetchone()
        assert row_security == (True, True)
        for name in (
            "worker_claim_orphan_tasks(integer,integer)",
            "worker_complete_orphan_task(uuid,uuid,jsonb)",
            "worker_fail_orphan_task(uuid,uuid,text)",
            "worker_discover_legacy_active_tasks()",
            "worker_fail_legacy_stale_task(uuid,text,jsonb)",
        ):
            pro = admin.execute(
                "SELECT prosecdef,proconfig FROM pg_proc WHERE oid=%s::regprocedure",
                (name,),
            ).fetchone()
            assert pro == (True, ["search_path=pg_catalog, public"])
            assert admin.execute(
                "SELECT has_function_privilege('everydayai_worker',%s,'EXECUTE')",
                (name,),
            ).fetchone()[0]
            assert not admin.execute(
                "SELECT has_function_privilege('everydayai_runtime',%s,'EXECUTE')",
                (name,),
            ).fetchone()[0]

        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState,
                           match="ROLLBACK_ACTIVE_RUNTIME_TASKS"):
            admin.execute(ROLLBACK.read_text(encoding="utf-8"))
        admin.rollback()
        definition = admin.execute(
            "SELECT pg_get_functiondef('worker_claim_orphan_tasks(integer,integer)'::regprocedure)"
        ).fetchone()[0]
        assert "runtime" in definition
        admin.execute("SET LOCAL ROLE everydayai_owner")
        admin.execute(
            "UPDATE tasks SET status='failed' WHERE delivery_context @> %s",
            (Jsonb({"runtime": True}),),
        )
        admin.execute(ROLLBACK.read_text(encoding="utf-8"))
        definition = admin.execute(
            "SELECT pg_get_functiondef('worker_claim_orphan_tasks(integer,integer)'::regprocedure)"
        ).fetchone()[0]
        assert "runtime" not in definition
        admin.execute(MIGRATION.read_text(encoding="utf-8"))
        definition = admin.execute(
            "SELECT pg_get_functiondef('worker_claim_orphan_tasks(integer,integer)'::regprocedure)"
        ).fetchone()[0]
        assert "runtime" in definition
        admin.commit()

    with psycopg.connect(url) as admin:
        reapplied_runtime_id, _ = _insert_task(admin, delivery={"runtime": True})
        admin.execute(
            "UPDATE tasks SET execution_token=NULL,lease_expires_at=NULL WHERE id=%s",
            (reapplied_runtime_id,),
        )
        admin.commit()
    with psycopg.connect(_worker_url(url)) as worker:
        with worker.transaction():
            _scope(worker)
            claimed = worker.execute(
                "SELECT worker_claim_orphan_tasks(20,60)"
            ).fetchone()[0]
        assert str(reapplied_runtime_id) not in {row["id"] for row in claimed}


def test_runtime_fence_acl_rls_and_rollback_cycle(database: tuple[str, Path]) -> None:
    url, _ = database
    with psycopg.connect(url) as admin:
        runtime_id, runtime_token = _insert_task(admin, delivery={"runtime": True})
        actor_id, actor_token = _insert_task(admin, delivery={"actor": True})
        legacy_fail_id, _ = _insert_task(admin, delivery={})
        legacy_complete_id, _ = _insert_task(admin, delivery={})
        legacy_stale_id, _ = _insert_task(admin, delivery={})
        admin.execute(
            "UPDATE tasks SET execution_token=NULL,lease_expires_at=NULL "
            "WHERE id=ANY(%s)",
            ([legacy_fail_id, legacy_complete_id],),
        )
        admin.commit()

    with psycopg.connect(_worker_url(url)) as worker:
        with worker.transaction():
            _scope(worker)
            claimed = worker.execute(
                "SELECT worker_claim_orphan_tasks(20,60)"
            ).fetchone()[0]
            discovered = worker.execute(
                "SELECT worker_discover_legacy_active_tasks()"
            ).fetchone()[0]
        assert str(runtime_id) not in {row["id"] for row in claimed}
        assert str(actor_id) not in {row["id"] for row in claimed}
        claim_by_id = {row["id"]: row for row in claimed}
        assert str(legacy_fail_id) in claim_by_id
        assert str(legacy_complete_id) in claim_by_id
        assert str(runtime_id) not in {row["id"] for row in discovered}
        assert str(actor_id) not in {row["id"] for row in discovered}
        discovered_ids = {row["id"] for row in discovered}
        assert str(legacy_fail_id) in discovered_ids
        assert str(legacy_complete_id) in discovered_ids
        assert str(legacy_stale_id) in discovered_ids

        _expect_forbidden(
            worker, "SELECT worker_complete_orphan_task(%s,%s,%s)",
            (runtime_id, runtime_token, Jsonb([{"type": "text", "text": "x"}])),
            "ORPHAN_RECOVERY_RUNTIME_TASK_FORBIDDEN",
        )
        _expect_forbidden(
            worker, "SELECT worker_fail_orphan_task(%s,%s,%s)",
            (runtime_id, runtime_token, "x"),
            "ORPHAN_RECOVERY_RUNTIME_TASK_FORBIDDEN",
        )
        _expect_forbidden(
            worker, "SELECT worker_fail_legacy_stale_task(%s,%s,NULL)",
            (runtime_id, "x"), "MEDIA_WORKER_RUNTIME_TASK_FORBIDDEN",
        )
        _expect_forbidden(
            worker, "SELECT worker_fail_orphan_task(%s,%s,%s)",
            (actor_id, actor_token, "x"), "ORPHAN_RECOVERY_ACTOR_TASK_FORBIDDEN",
        )
        with worker.transaction():
            _scope(worker)
            completed = worker.execute(
                "SELECT worker_complete_orphan_task(%s,%s,%s)",
                (legacy_complete_id,
                 claim_by_id[str(legacy_complete_id)]["execution_token"],
                 Jsonb([{"type": "text", "text": "partial"}])),
            ).fetchone()[0]
            failed = worker.execute(
                "SELECT worker_fail_orphan_task(%s,%s,%s)",
                (legacy_fail_id,
                 claim_by_id[str(legacy_fail_id)]["execution_token"],
                 "restart"),
            ).fetchone()[0]
            result = worker.execute(
                "SELECT worker_fail_legacy_stale_task(%s,%s,NULL)",
                (legacy_stale_id, "timeout"),
            ).fetchone()[0]
            assert completed["outcome"] == "completed"
            assert failed["outcome"] == "failed"
            assert result["outcome"] == "failed"

    _verify_acl_and_rollback_cycle(url)
