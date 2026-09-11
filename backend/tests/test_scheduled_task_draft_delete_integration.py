"""Exercise the real delete RPC against a disposable, socket-only PostgreSQL.

Never reads DATABASE_URL or project credentials. No pre-existing server/database
is used. Environments without PostgreSQL binaries explicitly skip this module.
"""
import getpass
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest


MIGRATIONS = Path(__file__).parents[1] / "migrations"
FIX = "253_scheduled_task_confirmed_draft_delete.sql"


@pytest.fixture(scope="module")
def postgres_socket():
    initdb, pg_ctl = shutil.which("initdb"), shutil.which("pg_ctl")
    if not initdb or not pg_ctl:
        pytest.skip("Disposable PostgreSQL requires initdb and pg_ctl")
    # Use a short path: Unix socket paths are limited to ~104 bytes on macOS.
    with tempfile.TemporaryDirectory(prefix="tool05-pg-") as directory:
        root = Path(directory)
        data, socket = root / "data", root / "s"
        socket.mkdir()
        subprocess.run([initdb, "-D", str(data), "-A", "trust", "--no-locale", "-E", "UTF8"],
                       check=True, capture_output=True, text=True)
        subprocess.run([pg_ctl, "-D", str(data), "-l", str(root / "postgres.log"),
                        "-o", f"-F -k {socket} -c listen_addresses=''", "-w", "start"],
                       check=True, capture_output=True, text=True)
        try:
            yield str(socket)
        finally:
            subprocess.run([pg_ctl, "-D", str(data), "-m", "fast", "-w", "stop"],
                           check=True, capture_output=True, text=True)


@pytest.fixture
def database(postgres_socket):
    with psycopg.connect(host=postgres_socket, dbname="postgres", user=getpass.getuser()) as conn:
        # DDL and seed data roll back after every case.
        conn.execute("CREATE ROLE everydayai")
        conn.execute("CREATE TABLE organizations(id uuid PRIMARY KEY); CREATE TABLE users(id uuid PRIMARY KEY)")
        conn.execute((MIGRATIONS / "069_scheduled_tasks.sql").read_text())
        conn.execute((MIGRATIONS / "071_scheduled_task_schedule_type.sql").read_text())
        conn.execute((MIGRATIONS / "244_scheduled_task_preflight_workflow.sql").read_text())
        conn.execute((MIGRATIONS / "245_scheduled_task_lifecycle_integrity.sql").read_text())
        conn.execute((MIGRATIONS / "249_scheduled_task_changeset_adapter.sql").read_text())
        org, user, task, other, draft, update_draft, run = [uuid4() for _ in range(7)]
        conn.execute("INSERT INTO organizations VALUES (%s);", (org,))
        conn.execute("INSERT INTO users VALUES (%s)", (user,))
        for tid in (task, other):
            conn.execute("""INSERT INTO scheduled_tasks(id,org_id,user_id,name,prompt,cron_expr,push_target,
                         execution_policy) VALUES (%s,%s,%s,'test','test','0 9 * * *','{}','{}')""",
                         (tid, org, user))
        conn.execute("""INSERT INTO scheduled_task_drafts(id,org_id,user_id,definition,config_hash,status,confirmed_task_id)
                     VALUES (%s,%s,%s,'{}','fixture','confirmed',%s)""", (draft, org, user, task))
        conn.execute("""INSERT INTO scheduled_task_drafts(id,org_id,user_id,definition,config_hash,status,source_task_id)
                     VALUES (%s,%s,%s,'{}','fixture','ready',%s)""", (update_draft, org, user, task))
        conn.execute("""INSERT INTO scheduled_task_preflight_runs(id,draft_id,org_id,config_hash,
                     definition_snapshot,plan_snapshot,policy_snapshot,status)
                     VALUES (%s,%s,%s,'fixture','{}','{}','{}','passed')""", (run, draft, org))
        def delete(*, expected_revision=0, org_id=org):
            return conn.execute("SELECT commit_scheduled_task_changeset(%s,%s,%s,%s,'delete',%s,'{}',%s)",
                                (uuid4(), org_id, user, task, expected_revision, "same-delete-key")).fetchone()[0]
        try:
            yield conn, delete, task, other, draft, update_draft, run
        finally:
            conn.rollback()


def test_original_constraint_reproduces_production_error(database):
    conn, delete, *_ = database
    with pytest.raises(psycopg.errors.ForeignKeyViolation, match="confirmed_task_id_fkey"):
        delete()


def test_delete_cascades_only_intermediates_and_keeps_idempotent_receipt(database):
    conn, delete, task, other, draft, update_draft, run = database
    # Reapplying migration must be safe; it changes a constraint, not task data.
    for _ in range(2):
        conn.execute((MIGRATIONS / FIX).read_text())
    assert conn.execute("SELECT count(*) FROM scheduled_tasks").fetchone()[0] == 2
    result = delete()
    assert result["outcome"] == "deleted" and result["task"] is None
    assert result["task_id"] == str(task)
    assert conn.execute("SELECT id FROM scheduled_tasks").fetchall() == [(other,)]
    assert conn.execute("SELECT count(*) FROM scheduled_task_drafts").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM scheduled_task_preflight_runs").fetchone()[0] == 0
    receipt = conn.execute("SELECT task_id,result FROM scheduled_task_change_receipts").fetchone()
    assert receipt[0] == task and receipt[1]["outcome"] == "deleted"
    assert delete()["outcome"] == "duplicate"
    assert conn.execute("SELECT count(*) FROM scheduled_task_change_receipts").fetchone()[0] == 1


@pytest.mark.parametrize("conflict", ["wrong_org", "revision", "running"])
def test_delete_preconditions_remain_enforced(database, conflict):
    conn, delete, task, *_ = database
    conn.execute((MIGRATIONS / FIX).read_text())
    kwargs = {}
    if conflict == "wrong_org":
        kwargs["org_id"] = uuid4()
    elif conflict == "revision":
        kwargs["expected_revision"] = 99
    else:
        conn.execute("UPDATE scheduled_tasks SET status='running' WHERE id=%s", (task,))
        kwargs["expected_revision"] = 1
    assert delete(**kwargs)["outcome"] == "conflict"
    assert conn.execute("SELECT count(*) FROM scheduled_tasks").fetchone()[0] == 2
    assert conn.execute("SELECT count(*) FROM scheduled_task_drafts").fetchone()[0] == 2
    assert conn.execute("SELECT count(*) FROM scheduled_task_change_receipts").fetchone()[0] == 0


def test_constraint_rollback_restores_old_behavior(database):
    conn, delete, *_ = database
    conn.execute((MIGRATIONS / FIX).read_text())
    conn.execute((MIGRATIONS / "rollback/253_scheduled_task_confirmed_draft_delete_rollback.sql").read_text())
    with pytest.raises(psycopg.errors.ForeignKeyViolation, match="confirmed_task_id_fkey"):
        delete()


@pytest.mark.parametrize("apply_fix", [False, True])
def test_legacy_confirm_then_changeset_delete_lifecycle(database, apply_fix):
    conn, _, seed_task, *_ = database
    org, user = conn.execute("SELECT org_id,user_id FROM scheduled_tasks WHERE id=%s", (seed_task,)).fetchone()
    draft, task, preflight, change = [uuid4() for _ in range(4)]
    definition = {"name": "legacy-created", "prompt": "read synthetic data", "cron_expr": "0 9 * * *",
                  "schedule_type": "daily", "push_target": {}}
    conn.execute("""INSERT INTO scheduled_task_drafts(id,org_id,user_id,definition,config_hash,
                 preflight_config_hash,status,execution_policy,plan)
                 VALUES (%s,%s,%s,%s::jsonb,'legacy','legacy','ready','{}','{}')""",
                 (draft, org, user, json.dumps(definition)))
    conn.execute("""INSERT INTO scheduled_task_preflight_runs(id,draft_id,org_id,config_hash,
                 definition_snapshot,plan_snapshot,policy_snapshot,status)
                 VALUES (%s,%s,%s,'legacy',%s::jsonb,'{}','{}','passed')""",
                 (preflight, draft, org, json.dumps(definition)))
    def confirm():
        return conn.execute("SELECT confirm_scheduled_task_draft(%s,%s,%s,'legacy',%s,NOW()+INTERVAL '1 day')",
                            (draft, org, user, task)).fetchone()[0]
    assert confirm() == {"outcome": "created", "task_id": str(task)}
    assert confirm() == {"outcome": "confirmed", "task_id": str(task)}
    def delete():
        return conn.execute("SELECT commit_scheduled_task_changeset(%s,%s,%s,%s,'delete',0,'{}','legacy-delete')",
                            (change, org, user, task)).fetchone()[0]
    if not apply_fix:
        with pytest.raises(psycopg.errors.ForeignKeyViolation, match="confirmed_task_id_fkey"):
            with conn.transaction():
                delete()
        assert conn.execute("SELECT count(*) FROM scheduled_tasks WHERE id=%s", (task,)).fetchone()[0] == 1
        return
    conn.execute((MIGRATIONS / FIX).read_text())
    assert delete()["outcome"] == "deleted"
    assert delete()["outcome"] == "duplicate"
    assert confirm() == {"outcome": "missing"}  # A deleted old form cannot recreate the task.
    assert conn.execute("SELECT count(*) FROM scheduled_task_preflight_runs WHERE id=%s", (preflight,)).fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM scheduled_task_change_receipts WHERE task_id=%s", (task,)).fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM scheduled_tasks").fetchone()[0] == 2
